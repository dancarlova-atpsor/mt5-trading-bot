"""
Trading Bot MT5 — Entry Point principal cu loop de trading.
Conecteaza la MT5, ruleaza strategii, gestioneaza risk si ordine.
Profit Manager: trailing profit per pozitie, cicluri repetitive, limite zilnice.
"""

import signal
import sys
import time

import MetaTrader5 as mt5

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


def poll_broker_closes(superbot, known_closed_tickets: set, magic: int, from_ts: float, symbol_last_close_time: dict = None) -> float:
    """Polls broker's deals history si feed-uieste pozitiile inchise noi in SuperBot.

    Detecteaza TOATE inchiderile (SL, TP, manual, trailing) — nu doar cele initiate de cod.
    Necesar pentru auto-disable: altfel SL-hits sunt invizibile pt performance_tracker.

    Returns: noul timestamp pentru urmatorul poll (now).
    """
    if superbot is None or not superbot.enabled:
        return time.time()

    now = time.time()
    try:
        # Cauta cu overlap 2 min pentru siguranta (clock skew, retry-uri broker)
        deals = mt5.history_deals_get(from_ts - 120, now + 60)
        if deals is None or len(deals) == 0:
            return now

        # Grupeaza pe position_id ca sa gasesc perechile IN/OUT
        by_position: dict = {}
        for d in deals:
            if d.magic != magic:
                continue
            pid = d.position_id
            if pid not in by_position:
                by_position[pid] = {"in": None, "out": None}
            if d.entry == mt5.DEAL_ENTRY_IN:
                by_position[pid]["in"] = d
            elif d.entry == mt5.DEAL_ENTRY_OUT:
                by_position[pid]["out"] = d

        for pid, pair in by_position.items():
            out_deal = pair["out"]
            if out_deal is None:
                continue  # Inca deschisa
            if out_deal.ticket in known_closed_tickets:
                continue  # Deja procesata
            known_closed_tickets.add(out_deal.ticket)

            in_deal = pair["in"]
            # Fallback: daca IN nu e in fereastra, cauta cu query pe position
            if in_deal is None:
                try:
                    all_deals = mt5.history_deals_get(position=pid)
                    if all_deals:
                        for d in all_deals:
                            if d.entry == mt5.DEAL_ENTRY_IN:
                                in_deal = d
                                break
                except Exception:
                    pass

            strategy = (in_deal.comment if in_deal else out_deal.comment) or "unknown"
            # Curata comentariile broker-side ("[sl 0.7116]", "[tp]")
            if strategy.startswith("[") or strategy.lower() in ("sl", "tp", "so"):
                strategy = "unknown"
            symbol = out_deal.symbol
            pnl = float(out_deal.profit) + float(out_deal.commission) + float(out_deal.swap)
            opened_at = float(in_deal.time) if in_deal else float(out_deal.time)
            closed_at = float(out_deal.time)

            try:
                superbot.on_trade_closed(
                    symbol=symbol, strategy=strategy, pnl=pnl,
                    opened_at=opened_at, closed_at=closed_at,
                )
                close_kind = "SL/TP" if pnl < 0 or "[" in (out_deal.comment or "") else "close"
                logger.info(
                    f"[SuperBot] Broker {close_kind}: #{out_deal.ticket} {symbol} "
                    f"[{strategy}] PnL={pnl:+.2f}"
                )
                if symbol_last_close_time is not None:
                    symbol_last_close_time[symbol] = closed_at
            except Exception as e:
                logger.error(f"on_trade_closed feed error: {e}")

    except Exception as e:
        logger.error(f"poll_broker_closes error: {e}")

    return now


def close_all_positions(order_manager: OrderManager, connector: MT5Connector, superbot=None, symbol_last_close_time: dict = None) -> float:
    """Inchide toate pozitiile deschise. Returneaza PnL total.

    Daca superbot != None, notifica on_trade_closed() pentru fiecare pozitie inchisa cu succes.
    """
    positions = order_manager.get_open_positions()
    total_pnl = 0.0
    close_ts = time.time()

    for pos in positions:
        result = order_manager.close_position(pos["ticket"], comment="cycle_close")
        if result.success:
            total_pnl += pos["profit"]
            logger.info(f"Inchis #{pos['ticket']} {pos['type']} {pos['volume']} {pos['symbol']} PnL={pos['profit']:+.2f}")
            if superbot is not None:
                try:
                    superbot.on_trade_closed(
                        symbol=pos["symbol"],
                        strategy=pos.get("comment") or "unknown",
                        pnl=pos["profit"],
                        closed_at=close_ts,
                    )
                except Exception as e:
                    logger.error(f"SuperBot on_trade_closed error: {e}")
            if symbol_last_close_time is not None:
                symbol_last_close_time[pos["symbol"]] = close_ts

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
    superbot = None
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

    # Config STOCKS (orar limitat NASDAQ + close-before-market-close)
    stocks_config = settings.get("stocks", {})
    stock_symbols = set(stocks_config.get("symbols", []))
    stock_trading_hours = stocks_config.get("trading_hours", "16:30-22:55")
    stock_close_at = stocks_config.get("close_before_market_close", "")
    stocks_closed_today = False

    outside_hours_logged = False
    daily_report_sent = False

    # SuperBot broker-close polling state
    superbot_known_closed_tickets: set = set()
    superbot_last_poll_ts = time.time()
    superbot_magic = settings["account"].get("magic_number", 123456)

    # Per-symbol cooldown — evita re-entry rapid dupa close
    symbol_last_close_time: dict[str, float] = {}
    position_cooldown = settings.get("position_cooldown_seconds", 300)

    try:
        while running:
            try:
                # Stocks close-before-market-close — inchide DOAR stocks la 22:55 (evita swap -$15/zi)
                if stock_close_at and stock_symbols:
                    try:
                        from datetime import datetime, time as dtime
                        hh, mm = stock_close_at.split(":")
                        close_t = dtime(int(hh), int(mm))
                        now_t = datetime.now().time()
                        close_window_end = dtime(int(hh), min(int(mm)+5, 59))
                        if close_t <= now_t <= close_window_end and not stocks_closed_today:
                            open_list = order_manager.get_open_positions()
                            stocks_open = [p for p in open_list if p["symbol"] in stock_symbols]
                            if stocks_open:
                                logger.info(f"[STOCKS CLOSE {stock_close_at}] Inchid {len(stocks_open)} pozitii stocks (evit swap -$15/zi)")
                                close_ts = time.time()
                                for pos in stocks_open:
                                    result = order_manager.close_position(pos["ticket"], comment="stock_mkt_close")
                                    if result.success:
                                        logger.info(f"  Inchis #{pos['ticket']} {pos['symbol']} {pos['volume']}lot PnL {pos['profit']:+.2f}")
                                        if superbot is not None:
                                            try:
                                                superbot.on_trade_closed(symbol=pos["symbol"], strategy=pos.get("comment") or "unknown",
                                                                          pnl=pos["profit"], closed_at=close_ts)
                                            except Exception:
                                                pass
                                        symbol_last_close_time[pos["symbol"]] = close_ts
                            stocks_closed_today = True
                        # Reset flag la schimbare zi (dupa miezul noptii)
                        if now_t < close_t and stocks_closed_today:
                            stocks_closed_today = False
                    except Exception as e:
                        logger.error(f"stocks close_at error: {e}")

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
                    # Poll broker pt inchideri noi (SL/TP) — feed in performance_tracker
                    superbot_last_poll_ts = poll_broker_closes(
                        superbot,
                        superbot_known_closed_tickets,
                        superbot_magic,
                        superbot_last_poll_ts,
                        symbol_last_close_time=symbol_last_close_time,
                    )

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
                        realized = close_all_positions(order_manager, connector, superbot=superbot, symbol_last_close_time=symbol_last_close_time)
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
                                # SuperBot — feed performance tracker
                                if superbot is not None:
                                    try:
                                        superbot.on_trade_closed(
                                            symbol=pos["symbol"],
                                            strategy=pos.get("comment") or "unknown",
                                            pnl=pos["profit"],
                                            closed_at=time.time(),
                                        )
                                    except Exception as e:
                                        logger.error(f"SuperBot on_trade_closed error: {e}")
                                # Cooldown per-simbol
                                symbol_last_close_time[pos["symbol"]] = time.time()

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
                        # Stock market hours check — stocks doar intre 16:30-22:55 RO
                        if symbol in stock_symbols:
                            if not is_trading_hours(stock_trading_hours, timezone_str):
                                logger.debug(f"[STOCKS HOURS] {symbol}: in afara {stock_trading_hours} — skip")
                                continue

                        # Cooldown check — nu re-intra pe simbol daca s-a inchis recent
                        last_close = symbol_last_close_time.get(symbol, 0)
                        if time.time() - last_close < position_cooldown:
                            remaining = int(position_cooldown - (time.time() - last_close))
                            logger.debug(f"[COOLDOWN] {symbol}: astept inca {remaining}s")
                            continue

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

                        # TrendFollowing: seteaza simbolul pt parametri per-symbol
                        if isinstance(strategy, TrendFollowing):
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

                        # SuperBot gate — regime + correlation + performance + MTF/S/R/swap
                        superbot_size_mult = 1.0
                        if superbot is not None:
                            # Luam current_price pt entry filter
                            _sym_info = connector.get_symbol_info(symbol)
                            current_price_hint = (_sym_info["ask"] if direction_str == "BUY" else _sym_info["bid"]) if _sym_info else 0.0
                            ok, size_mult, sb_reason = superbot.gate_trade(
                                symbol=symbol,
                                strategy_name=strat_info["name"],
                                direction=direction_str,
                                open_positions=open_positions,
                                current_price=current_price_hint,
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

                # SuperBot status — la fiecare 10 iteratii (~10 min la interval=60s)
                if superbot is not None and superbot.enabled:
                    if superbot._iter_counter % 10 == 0 and superbot._iter_counter > 0:
                        sb_status = superbot.get_status()
                        plan_summary = ", ".join(
                            f"{sym}:{p.get('regime','?')}/{p.get('strategy','?')}/x{p.get('size_mult',1.0):.1f}"
                            for sym, p in sb_status["symbol_plan"].items()
                        ) or "no plan"
                        logger.info(f"[SuperBot #{sb_status['iter_counter']}] {plan_summary}")
                        if sb_status["disabled_symbols"]:
                            logger.info(f"[SuperBot] Simboluri dezactivate: {sb_status['disabled_symbols']}")

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
