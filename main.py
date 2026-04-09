"""
Trading Bot MT5 — Entry Point principal cu loop de trading.
Conecteaza la MT5, ruleaza strategii, gestioneaza risk si ordine.
"""

import signal
import sys
import time

from core.mt5_connector import MT5Connector
from core.data_fetcher import DataFetcher
from core.order_manager import OrderManager, OrderType
from risk.risk_manager import RiskManager
from strategies.base_strategy import Signal
from strategies.trend_following import TrendFollowing
from strategies.mean_reversion import MeanReversion
from strategies.scalping import Scalping
from utils.logger import setup_logger
from utils.helpers import load_config, is_trading_hours, pips_to_price

logger = setup_logger("main", config_path="config/settings.yaml")

# Flag pentru oprire gracioasa
running = True


def signal_handler(sig, frame):
    """Handler pentru Ctrl+C — oprire gracioasa."""
    global running
    logger.info("Ctrl+C detectat — opresc botul...")
    running = False


def load_strategies(strategies_config: dict, active_list: list) -> list[dict]:
    """Incarca si instantiaza strategiile active."""
    strategy_map = {
        "trend_following": TrendFollowing,
        "mean_reversion": MeanReversion,
        "scalping": Scalping,
    }

    loaded = []
    for name in active_list:
        cfg = strategies_config.get(name)
        if cfg is None:
            logger.warning(f"Strategie necunoscuta in config: {name}")
            continue
        if not cfg.get("enabled", True):
            logger.info(f"Strategie {name} dezactivata — skip")
            continue

        cls = strategy_map.get(name)
        if cls is None:
            logger.warning(f"Clasa inexistenta pentru strategie: {name}")
            continue

        strategy = cls(cfg)
        symbols = cfg.get("symbols", [])
        timeframe = cfg.get("timeframe", "H1")

        loaded.append({
            "strategy": strategy,
            "symbols": symbols,
            "timeframe": timeframe,
            "name": name,
        })
        logger.info(f"Strategie incarcata: {name} | Simboluri: {symbols} | TF: {timeframe}")

    return loaded


def main():
    """Functia principala — loop de trading."""
    global running

    logger.info("=" * 50)
    logger.info("Trading Bot MT5 — Start")
    logger.info("=" * 50)

    # Incarca configurari
    settings = load_config("settings.yaml")
    strategies_config = load_config("strategies.yaml")

    # Initializare componente
    connector = MT5Connector()
    data_fetcher = DataFetcher()
    order_manager = OrderManager()
    risk_manager = RiskManager()

    # Conectare MT5
    if not connector.connect():
        logger.error("Nu m-am putut conecta la MT5. Iesire.")
        sys.exit(1)

    # Incarca strategii
    strategies = load_strategies(strategies_config, settings.get("active_strategies", []))
    if not strategies:
        logger.error("Nicio strategie activa. Verifica config/settings.yaml")
        connector.disconnect()
        sys.exit(1)

    # Graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    schedule = settings.get("schedule", {})
    interval = schedule.get("check_interval_seconds", 10)
    trading_hours = schedule.get("trading_hours", "08:00-22:00")
    timezone_str = schedule.get("timezone", "Europe/Bucharest")

    outside_hours_logged = False

    try:
        while running:
            try:
                # Verificare ore de trading
                if not is_trading_hours(trading_hours, timezone_str):
                    if not outside_hours_logged:
                        logger.info(f"In afara orelor de trading ({trading_hours}). Astept...")
                        outside_hours_logged = True
                    time.sleep(interval)
                    continue
                outside_hours_logged = False

                # Informatii cont
                account_info = connector.get_account_info()
                if account_info is None:
                    logger.error("Nu pot obtine info cont. Reincerc...")
                    time.sleep(interval)
                    continue

                # Pozitii deschise
                open_positions = order_manager.get_open_positions()

                # Itereaza strategii si simboluri
                for strat_info in strategies:
                    strategy = strat_info["strategy"]
                    symbols = strat_info["symbols"]
                    timeframe = strat_info["timeframe"]

                    for symbol in symbols:
                        # Fetch date
                        data = data_fetcher.get_rates(symbol, timeframe, bars=500)
                        if data is None or len(data) < 60:
                            continue

                        # Scalping: seteaza spread-ul curent
                        if isinstance(strategy, Scalping):
                            sym_info = connector.get_symbol_info(symbol)
                            if sym_info:
                                strategy.set_current_spread(sym_info["spread"])

                        # Analiza
                        signal_result = strategy.analyze(data)
                        if signal_result == Signal.HOLD:
                            continue

                        # Verificare risk
                        can_trade, reason = risk_manager.can_open_trade(
                            symbol, open_positions, account_info["balance"]
                        )
                        if not can_trade:
                            logger.info(f"[{strategy.name}] {symbol}: nu pot deschide — {reason}")
                            continue

                        # Pret curent
                        sym_info = connector.get_symbol_info(symbol)
                        if sym_info is None:
                            continue

                        direction = signal_result.value  # "BUY" sau "SELL"
                        current_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]

                        # Calcul SL, TP, volume
                        sl = risk_manager.calculate_stop_loss(data, direction, current_price, symbol)
                        tp = risk_manager.calculate_take_profit(current_price, sl, direction)
                        sl_pips = abs(current_price - sl) / pips_to_price(symbol, 1)
                        volume = risk_manager.calculate_position_size(
                            account_info["balance"], sl_pips, symbol
                        )

                        # Executie ordine
                        order_type = OrderType.MARKET_BUY if direction == "BUY" else OrderType.MARKET_SELL
                        result = order_manager.open_market_order(
                            symbol=symbol,
                            order_type=order_type,
                            volume=volume,
                            sl=sl,
                            tp=tp,
                            comment=f"{strategy.name}",
                        )

                        if result.success:
                            logger.info(
                                f"[{strategy.name}] {direction} {volume} {symbol} @ {current_price:.5f} "
                                f"| SL={sl:.5f} TP={tp:.5f} | ID={result.order_id}"
                            )
                            risk_manager.record_trade(0)  # PnL real se actualizeaza la inchidere
                            # Reincarca pozitii (pt limita urmatoare)
                            open_positions = order_manager.get_open_positions()

                # Trailing stop pe pozitii existente
                for pos in open_positions:
                    sym_info = connector.get_symbol_info(pos["symbol"])
                    if sym_info is None:
                        continue
                    current_price = sym_info["bid"] if pos["type"] == "BUY" else sym_info["ask"]
                    new_sl = risk_manager.update_trailing_stop(pos, current_price)
                    if new_sl is not None:
                        order_manager.modify_position(pos["ticket"], sl=new_sl)

                # Status periodic
                daily = risk_manager.get_daily_stats()
                logger.debug(
                    f"Status: {len(open_positions)} pozitii | "
                    f"Tranzactii azi: {daily['trades']} | PnL azi: {daily['pnl']:.2f}"
                )

            except Exception as e:
                logger.error(f"Eroare in loop: {e}", exc_info=True)

            time.sleep(interval)

    finally:
        connector.disconnect()
        logger.info("=" * 50)
        logger.info("Trading Bot MT5 — Stop")
        logger.info("=" * 50)


if __name__ == "__main__":
    main()
