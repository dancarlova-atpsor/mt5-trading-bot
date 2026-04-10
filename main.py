"""
Trading Bot MT5 — Entry Point principal cu loop de trading.
Conecteaza la MT5, ruleaza strategii, gestioneaza risk si ordine.
Profit Manager: trailing profit per pozitie, cicluri repetitive, limite zilnice.
"""

import signal
import sys
import time

from core.mt5_connector import MT5Connector
from core.data_fetcher import DataFetcher
from core.order_manager import OrderManager, OrderType
from core.signal_bridge import SignalBridge
from risk.risk_manager import RiskManager
from risk.profit_manager import ProfitManager
from strategies.base_strategy import Signal
from strategies.trend_following import TrendFollowing
from strategies.mean_reversion import MeanReversion
from strategies.scalping import Scalping
from strategies.news_strategy import NewsSentiment
from strategies.breakout import Breakout
from strategies.macd_divergence import MACDDivergence
from utils.logger import setup_logger
from utils.helpers import load_config, is_trading_hours, pips_to_price
from utils.notifier import TelegramNotifier
from utils.news_calendar import NewsCalendar
from superbot import SuperBot

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
        "news_sentiment": NewsSentiment,
        "breakout": Breakout,
        "macd_divergence": MACDDivergence,
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


def close_all_positions(order_manager: OrderManager, connector: MT5Connector) -> float:
    """Inchide toate pozitiile deschise. Returneaza PnL total."""
    positions = order_manager.get_open_positions()
    total_pnl = 0.0

    for pos in positions:
        result = order_manager.close_position(pos["ticket"], comment="cycle_close")
        if result.success:
            total_pnl += pos["profit"]
            logger.info(f"Inchis #{pos['ticket']} {pos['type']} {pos['volume']} {pos['symbol']} PnL={pos['profit']:+.2f}")

    return total_pnl


def main():
    """Functia principala — loop de trading cu cicluri repetitive."""
    global running

    logger.info("=" * 50)
    logger.info("Trading Bot MT5 — Start (Profit Manager activ)")
    logger.info("=" * 50)

    # Incarca configurari
    settings = load_config("settings.yaml")
    strategies_config = load_config("strategies.yaml")

    # Initializare componente
    connector = MT5Connector()
    data_fetcher = DataFetcher()
    order_manager = OrderManager()
    risk_manager = RiskManager()
    profit_manager = ProfitManager()
    notifier = TelegramNotifier()
    news_calendar = NewsCalendar()

    # SuperBot — layer de inteligenta adaptiv
    superbot = None

    # Signal Bridge (comunicare cu MQL5 EA)
    exec_config = settings.get("execution", {})
    execution_mode = exec_config.get("mode", "python_api")
    signal_bridge = None
    if execution_mode in ("mql5_ea", "both"):
        mt5_files_path = exec_config.get("mt5_files_path", "") or None
        signal_bridge = SignalBridge(mt5_files_path)
        logger.info(f"Signal Bridge activ — mod: {execution_mode}")

    # Conectare MT5
    if not connector.connect():
        logger.error("Nu m-am putut conecta la MT5. Iesire.")
        notifier.notify_error("Nu m-am putut conecta la MT5!")
        sys.exit(1)

    notifier.notify_bot_started()

    # Incarca strategii
    strategies = load_strategies(strategies_config, settings.get("active_strategies", []))
    if not strategies:
        logger.error("Nicio strategie activa. Verifica config/settings.yaml")
        connector.disconnect()
        sys.exit(1)

    # SuperBot — layer adaptiv (optional)
    if settings.get("superbot", {}).get("enabled", False):
        # Strategies map pt lookup in SuperBot
        strategies_map = {s["name"]: s["strategy"] for s in strategies}
        try:
            superbot = SuperBot(
                config=settings,
                data_fetcher=data_fetcher,
                risk_manager=risk_manager,
                strategies_map=strategies_map,
                strategies_config=strategies_config,
                symbols=settings.get("symbols", []),
            )
            superbot.start()
        except Exception as e:
            logger.error(f"SuperBot init failed: {e}")
            superbot = None

    # Graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    schedule = settings.get("schedule", {})
    interval = schedule.get("check_interval_seconds", 60)
    trading_hours = schedule.get("trading_hours", "00:00-23:59")
    timezone_str = schedule.get("timezone", "Europe/Bucharest")

    outside_hours_logged = False
    daily_report_sent = False

    try:
        while running:
            try:
                # Verificare ore de trading
                if not is_trading_hours(trading_hours, timezone_str):
                    if not outside_hours_logged:
                        logger.info(f"In afara orelor de trading ({trading_hours}). Astept...")
                        outside_hours_logged = True

                        if not daily_report_sent:
                            daily = risk_manager.get_daily_stats()
                            pm_stats = profit_manager.get_daily_stats()
                            acc = connector.get_account_info() or {}
                            daily["balance"] = acc.get("balance", 0)
                            daily["equity"] = acc.get("equity", 0)
                            daily["open_positions"] = len(order_manager.get_open_positions())
                            notifier.notify_daily_report(daily)
                            daily_report_sent = True

                    time.sleep(interval)
                    continue
                outside_hours_logged = False
                daily_report_sent = False

                # SuperBot tick — cere deep analysis la fiecare N iteratii
                if superbot is not None:
                    superbot.tick()

                # ============================================
                # PROFIT MANAGER — verificari inainte de orice
                # ============================================

                # Verificare limita zilnica
                daily_hit, daily_reason = profit_manager.is_daily_limit_reached()
                if daily_hit:
                    logger.info(f"[DAILY LIMIT] {daily_reason} — bot in asteptare")
                    time.sleep(interval)
                    continue

                # Verificare pauza intre cicluri
                paused, pause_reason = profit_manager.is_cycle_paused()
                if paused:
                    logger.debug(f"[PAUZA] {pause_reason}")
                    time.sleep(interval)
                    continue

                # Informatii cont
                account_info = connector.get_account_info()
                if account_info is None:
                    logger.error("Nu pot obtine info cont. Reincerc...")
                    time.sleep(interval)
                    continue

                # Pozitii deschise
                open_positions = order_manager.get_open_positions()

                # ============================================
                # PROFIT MANAGER — trailing profit + cycle target
                # ============================================
                if open_positions:
                    total_unrealized = sum(p["profit"] for p in open_positions)

                    # Verificare cycle target — inchide TOATE pozitiile
                    if profit_manager.check_cycle_target(total_unrealized):
                        realized = close_all_positions(order_manager, connector)
                        profit_manager.record_cycle_close(realized)
                        notifier.notify_error(
                            f"Ciclu complet! Profit: +{realized:.2f} EUR | "
                            f"Stats: {profit_manager.get_daily_stats()}"
                        )
                        open_positions = []
                        continue

                    # Verificare trailing profit per pozitie
                    for pos in open_positions:
                        should_close = profit_manager.check_position_trailing(
                            pos["ticket"], pos["profit"]
                        )
                        if should_close:
                            result = order_manager.close_position(pos["ticket"], comment="trailing_profit")
                            if result.success:
                                profit_manager.record_position_close(pos["ticket"], pos["profit"])
                                notifier.notify_trade_closed(
                                    pos["symbol"], pos["type"], pos["profit"], 0
                                )

                    # Reincarca pozitii dupa trailing closes
                    open_positions = order_manager.get_open_positions()

                # ============================================
                # STRATEGII — deschide pozitii noi
                # ============================================
                for strat_info in strategies:
                    strategy = strat_info["strategy"]
                    symbols = strat_info["symbols"]
                    timeframe = strat_info["timeframe"]

                    for symbol in symbols:
                        # News blackout check
                        is_blackout, blackout_reason = news_calendar.is_news_blackout(symbol)
                        if is_blackout:
                            logger.info(f"[NEWS BLACKOUT] {symbol}: {blackout_reason} — skip")
                            continue

                        # Fetch date
                        data = data_fetcher.get_rates(symbol, timeframe, bars=500)
                        if data is None or len(data) < 60:
                            continue

                        # Scalping: seteaza spread-ul curent
                        if isinstance(strategy, Scalping):
                            sym_info = connector.get_symbol_info(symbol)
                            if sym_info:
                                strategy.set_current_spread(sym_info["spread"])

                        # NewsSentiment: seteaza simbolul curent
                        if isinstance(strategy, NewsSentiment):
                            strategy.set_current_symbol(symbol)

                        # Analiza
                        signal_result = strategy.analyze(data)
                        if signal_result == Signal.HOLD:
                            continue

                        # Evita duplicate: nu deschide aceeasi directie pe acelasi simbol
                        direction_str = signal_result.value
                        already_open = any(
                            p["symbol"] == symbol and p["type"] == direction_str
                            for p in open_positions
                        )
                        if already_open:
                            continue

                        # SuperBot gate — regime + correlation + performance
                        superbot_size_mult = 1.0
                        if superbot is not None:
                            ok, size_mult, sb_reason = superbot.gate_trade(
                                symbol=symbol,
                                strategy_name=strat_info["name"],
                                direction=direction_str,
                                open_positions=open_positions,
                            )
                            if not ok:
                                logger.info(f"[SuperBot] {symbol}: BLOCKED — {sb_reason}")
                                continue
                            superbot_size_mult = size_mult
                            if size_mult != 1.0:
                                logger.info(f"[SuperBot] {symbol}: size_mult={size_mult:.2f} ({sb_reason})")

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

                        direction = signal_result.value
                        current_price = sym_info["ask"] if direction == "BUY" else sym_info["bid"]

                        # Calcul SL, TP, volume
                        sl = risk_manager.calculate_stop_loss(data, direction, current_price, symbol)
                        tp = risk_manager.calculate_take_profit(current_price, sl, direction, symbol)
                        sl_pips = abs(current_price - sl) / pips_to_price(symbol, 1)
                        volume = risk_manager.calculate_position_size(
                            account_info["balance"], sl_pips, symbol
                        )

                        # Aplica SuperBot size multiplier
                        if superbot_size_mult != 1.0:
                            volume = round(volume * superbot_size_mult, 2)
                            # Clamp la limite broker (min 0.01)
                            volume = max(0.01, volume)

                        # Executie ordine
                        executed = False

                        if execution_mode in ("python_api", "both"):
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
                                    f"[{strategy.name}][API] {direction} {volume} {symbol} @ {current_price:.5f} "
                                    f"| SL={sl} TP={tp} | ID={result.order_id}"
                                )
                                executed = True

                        if signal_bridge and execution_mode in ("mql5_ea", "both") and not executed:
                            magic = settings["account"].get("magic_number", 123456)
                            signal_bridge.write_signal(
                                symbol=symbol, action=direction, volume=volume,
                                sl=sl, tp=tp, magic=magic, comment=strategy.name,
                            )
                            timeout = exec_config.get("signal_timeout", 30)
                            ea_status = signal_bridge.wait_for_execution(timeout_seconds=timeout)
                            if ea_status and ea_status.get("status") == "FILLED":
                                logger.info(
                                    f"[{strategy.name}][EA] {direction} {volume} {symbol} "
                                    f"| Ticket={ea_status.get('ticket')}"
                                )
                                executed = True
                            signal_bridge.clear_signal()

                        if executed:
                            risk_manager.record_trade(0)
                            open_positions = order_manager.get_open_positions()
                            notifier.notify_trade_opened(
                                symbol, direction, volume, current_price, sl, tp, strategy.name
                            )

                # Trailing stop pe pret (SL-ul clasic)
                for pos in open_positions:
                    sym_info = connector.get_symbol_info(pos["symbol"])
                    if sym_info is None:
                        continue
                    current_price = sym_info["bid"] if pos["type"] == "BUY" else sym_info["ask"]
                    new_sl = risk_manager.update_trailing_stop(pos, current_price)
                    if new_sl is not None:
                        order_manager.modify_position(pos["ticket"], sl=new_sl)

                # Status periodic
                pm_stats = profit_manager.get_daily_stats()
                logger.debug(
                    f"Status: {len(open_positions)} pozitii | "
                    f"Cicluri azi: {pm_stats['cycles']} | PnL realizat: {pm_stats['realized_pnl']:+.2f}"
                )

            except Exception as e:
                logger.error(f"Eroare in loop: {e}", exc_info=True)
                notifier.notify_error(str(e))

            time.sleep(interval)

    finally:
        if superbot is not None:
            try:
                superbot.shutdown()
            except Exception as e:
                logger.error(f"SuperBot shutdown error: {e}")
        notifier.notify_bot_stopped()
        connector.disconnect()
        logger.info("=" * 50)
        logger.info("Trading Bot MT5 — Stop")
        logger.info("=" * 50)


if __name__ == "__main__":
    main()
