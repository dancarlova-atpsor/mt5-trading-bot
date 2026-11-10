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
from strategies.zone_based import ZoneBasedStrategy
from strategies.adaptive_mtf import AdaptiveMTFStrategy
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
        "zone_based": ZoneBasedStrategy,
        "adaptive_mtf": AdaptiveMTFStrategy,
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
        symbol_tf_overrides = cfg.get("symbol_tf_overrides", {}) or {}

        loaded.append({
            "strategy": strategy,
            "symbols": symbols,
            "timeframe": timeframe,
            "symbol_tf_overrides": symbol_tf_overrides,
            "name": name,
        })
        if symbol_tf_overrides:
            logger.info(f"Strategie incarcata: {name} | Simboluri: {symbols} | TF default: {timeframe} | Overrides: {symbol_tf_overrides}")
        else:
            logger.info(f"Strategie incarcata: {name} | Simboluri: {symbols} | TF: {timeframe}")

    return loaded


def poll_broker_closes(superbot, known_closed_tickets: set, magic: int, from_ts: float, symbol_last_close_time: dict = None, risk_manager=None) -> float:
    """Polls broker's deals history si feed-uieste pozitiile inchise noi.

    Detecteaza TOATE inchiderile (SL, TP, manual, trailing) — nu doar cele initiate de cod.
    Feed:
      - SuperBot (daca activ) — pentru performance_tracker
      - RiskManager (daca dat) — pentru per-symbol daily loss tracking

    FIX 2026-08-03: nu mai iese devreme daca superbot None/disabled — feed risk_manager oricum.
    Anterior: BNBUSD 03.07 a facut cascade -$163 pentru ca risk_manager nu primea PnL cand
    poll-ul iesea devreme (dar superbot era ok atunci — bug edge case pentru viitor).

    Returns: noul timestamp pentru urmatorul poll (now).
    """
    # Daca NICI superbot NICI risk_manager nu sunt disponibili → nimic de facut
    superbot_active = superbot is not None and getattr(superbot, "enabled", False)
    if not superbot_active and risk_manager is None:
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

            # Feed SuperBot (daca activ)
            if superbot_active:
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
                except Exception as e:
                    logger.error(f"on_trade_closed feed error: {e}")
            else:
                # Log ca sa avem vizibilitate chiar si fara SuperBot
                close_kind = "SL/TP" if pnl < 0 or "[" in (out_deal.comment or "") else "close"
                logger.info(
                    f"[Broker close] {close_kind}: #{out_deal.ticket} {symbol} "
                    f"[{strategy}] PnL={pnl:+.2f}"
                )

            # Cooldown per-simbol (bug fix 2026-05-11: time.time() local)
            if symbol_last_close_time is not None:
                symbol_last_close_time[symbol] = time.time()

            # NOU 2026-07-01: feed per-symbol PnL la risk_manager (independent de SuperBot)
            # FIX 2026-08-03: mutat AFARA din try SuperBot — ruleaza chiar daca superbot None
            if risk_manager is not None:
                try:
                    risk_manager.record_trade(pnl, symbol=symbol)
                except Exception as e:
                    logger.error(f"risk_manager.record_trade feed error: {e}")

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
    last_close_check_date = None  # date la care s-a evaluat ultima oara stocks close

    # TZ-aware: foloseste Europe/Bucharest pentru orice comparatie de timp pe stocks
    # (VPS poate fi pe UTC, altfel force-close se ageaza la ora gresita)
    try:
        from zoneinfo import ZoneInfo
        BUCHAREST_TZ = ZoneInfo("Europe/Bucharest")
    except Exception:
        try:
            import pytz
            BUCHAREST_TZ = pytz.timezone("Europe/Bucharest")
        except Exception:
            logger.warning("Nu pot importa zoneinfo/pytz - folosesc datetime.now() naive")
            BUCHAREST_TZ = None

    def _now_ro():
        """Returneaza datetime curent in timezone Europe/Bucharest, indiferent de TZ-ul serverului."""
        from datetime import datetime
        if BUCHAREST_TZ is not None:
            return datetime.now(BUCHAREST_TZ)
        return datetime.now()

    def _stocks_should_be_closed(current_dt):
        """Returneaza True daca pozitiile stocks ar trebui sa fie inchise la momentul curent.
        Inchidem stocks:
        - Dupa ora close_at (22:55 RO) in orice zi
        - In weekend (sambata, duminica)
        - Inainte de close_at = ramane in sesiune
        """
        if not stock_close_at:
            return False
        try:
            hh, mm = stock_close_at.split(":")
            close_t = current_dt.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            # Weekend = sambata (5) si duminica (6)
            if current_dt.weekday() in (5, 6):
                return True
            # Dupa ora close_at in ziua curenta
            if current_dt >= close_t:
                return True
            return False
        except Exception:
            return False

    # STARTUP CLEANUP: daca exista stocks deschise si suntem in afara sesiunii → force close imediat
    try:
        startup_now = _now_ro()
        if _stocks_should_be_closed(startup_now) and stock_symbols:
            open_pos = order_manager.get_open_positions()
            stocks_to_close = [p for p in open_pos if p["symbol"] in stock_symbols]
            if stocks_to_close:
                logger.warning(f"[STARTUP CLEANUP] {len(stocks_to_close)} pozitii stocks deschise in afara sesiunii — inchid acum")
                for pos in stocks_to_close:
                    result = order_manager.close_position(pos["ticket"], comment="startup_cleanup")
                    if result.success:
                        logger.info(f"  Cleanup #{pos['ticket']} {pos['symbol']} {pos['volume']}lot PnL {pos['profit']:+.2f}")
                        try:
                            notifier.notify_trade_closed(pos["symbol"], "startup_cleanup", pos["profit"])
                        except Exception:
                            pass
    except Exception as e:
        logger.error(f"Startup cleanup error: {e}")

    outside_hours_logged = False
    daily_report_sent = False

    # SuperBot broker-close polling state
    superbot_known_closed_tickets: set = set()
    superbot_last_poll_ts = time.time()
    superbot_magic = settings["account"].get("magic_number", 123456)

    # Per-symbol cooldown — evita re-entry rapid dupa close
    symbol_last_close_time: dict[str, float] = {}
    position_cooldown = settings.get("position_cooldown_seconds", 300)

    # NOU 2026-08-03: Break-Even stop tracking
    # Cand profit unrealized ajunge la sl_distance USD, mutam SL la entry
    # (transforma loss potentiala in breakeven daca pretul revine)
    be_moved_tickets: set[int] = set()
    be_config = settings.get("break_even_stop", {})
    be_enabled = be_config.get("enabled", True)
    be_trigger_multiplier = be_config.get("trigger_multiplier", 1.0)  # profit >= sl_dist * mult → move to BE
    be_offset_pips = be_config.get("offset_pips", 0)  # optional: mut SL la entry+offset (lock small profit)

    # NOU 2026-11-10: "let TP run" — strategii ale caror pozitii NU sunt atinse de
    # BE stop, trailing profit_manager si cycle-close. Iesire DOAR prin SL/TP setat la intrare.
    # Motiv: 3 luni AdaptiveMTF → castiguri taiate la +$8-20 (trailing), pierderi la SL complet
    # (-$80..-$136) → net ~0 desi WR > 60%. TP-ul R:R 2.5 nu apuca sa fie atins.
    let_tp_run = tuple(settings.get("let_tp_run_strategies", ["AdaptiveMTF"]) or ())

    def _is_let_tp_run(pos: dict) -> bool:
        comment = (pos.get("comment") or "")
        return bool(let_tp_run) and comment.startswith(let_tp_run)

    try:
        while running:
            try:
                # Stocks close-before-market-close — TZ-aware + continous fallback
                # (Bug 2026-05-11: INTC ramasa deschis peste weekend din cauza window 5min + TZ naive)
                if stock_close_at and stock_symbols:
                    try:
                        now_ro = _now_ro()
                        today = now_ro.date()
                        # Reset flag daca s-a schimbat ziua (zi noua → restart cycle)
                        if last_close_check_date != today:
                            stocks_closed_today = False
                            last_close_check_date = today

                        # Daca stocks ar trebui sa fie inchise SI sunt inca deschise → inchide
                        if _stocks_should_be_closed(now_ro):
                            open_list = order_manager.get_open_positions()
                            stocks_open = [p for p in open_list if p["symbol"] in stock_symbols]
                            if stocks_open:
                                wk = ["Lun", "Marti", "Mier", "Joi", "Vin", "Sam", "Dum"][now_ro.weekday()]
                                logger.warning(
                                    f"[STOCKS CLOSE {now_ro.strftime('%H:%M')} RO {wk}] "
                                    f"Inchid {len(stocks_open)} pozitii stocks (force-close in afara sesiunii)"
                                )
                                close_ts = time.time()
                                for pos in stocks_open:
                                    result = order_manager.close_position(pos["ticket"], comment="stock_mkt_close")
                                    if result.success:
                                        logger.info(f"  Inchis #{pos['ticket']} {pos['symbol']} {pos['volume']}lot PnL {pos['profit']:+.2f}")
                                        try:
                                            notifier.notify_trade_closed(pos["symbol"], "stock_mkt_close", pos["profit"])
                                        except Exception:
                                            pass
                                        if superbot is not None:
                                            try:
                                                superbot.on_trade_closed(symbol=pos["symbol"], strategy=pos.get("comment") or "unknown",
                                                                          pnl=pos["profit"], closed_at=close_ts)
                                            except Exception:
                                                pass
                                        symbol_last_close_time[pos["symbol"]] = close_ts
                                stocks_closed_today = True
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

                # Poll broker pt inchideri noi (SL/TP) — feed in performance_tracker + risk_manager
                # FIX 2026-08-03: mutat AFARA din if superbot — ruleaza intotdeauna pentru risk_manager
                # (anterior BNBUSD 03.07 cascade -$163 pt ca updates per-symbol daily loss se pierdeau)
                superbot_last_poll_ts = poll_broker_closes(
                    superbot,
                    superbot_known_closed_tickets,
                    superbot_magic,
                    superbot_last_poll_ts,
                    symbol_last_close_time=symbol_last_close_time,
                    risk_manager=risk_manager,
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
                # BREAK-EVEN STOP (NOU 2026-08-03)
                # Cand profit unrealized >= sl_distance USD → mut SL la entry
                # (transforma pierderi potentiale in breakeven daca pretul revine)
                # ============================================
                if be_enabled and open_positions:
                    for pos in open_positions:
                        ticket = pos["ticket"]
                        if ticket in be_moved_tickets:
                            continue  # Deja mutat la BE
                        if _is_let_tp_run(pos):
                            continue  # NOU 2026-11-10: fara BE stop — lasam TP sa fie atins

                        entry = pos.get("open_price", 0)
                        current_sl = pos.get("sl", 0)
                        symbol = pos["symbol"]
                        if entry == 0 or current_sl == 0:
                            continue

                        # Distanta SL in pret (SL vs entry)
                        sl_distance_price = abs(entry - current_sl)
                        if sl_distance_price == 0:
                            continue

                        # Calculam sl_distance in USD folosind tick_value
                        sym_info_be = mt5.symbol_info(symbol)
                        if sym_info_be is None or sym_info_be.trade_tick_size == 0:
                            continue
                        sl_distance_ticks = sl_distance_price / sym_info_be.trade_tick_size
                        sl_distance_usd = sl_distance_ticks * sym_info_be.trade_tick_value * pos["volume"]

                        # BE trigger: profit >= sl_distance_usd * multiplier
                        trigger_usd = sl_distance_usd * be_trigger_multiplier
                        if pos["profit"] >= trigger_usd and trigger_usd > 0:
                            # Muta SL la entry (+ optional offset)
                            offset_price = pips_to_price(symbol, be_offset_pips) if be_offset_pips else 0
                            if pos["type"] == "BUY":
                                new_sl = entry + offset_price
                                # Doar daca imbunatatim SL-ul (nu inrautatim)
                                if new_sl > current_sl:
                                    result = order_manager.modify_position(ticket, sl=new_sl)
                                    if result.success:
                                        be_moved_tickets.add(ticket)
                                        logger.info(
                                            f"[BE STOP] #{ticket} {symbol} BUY: profit +{pos['profit']:.2f} >= "
                                            f"trigger +{trigger_usd:.2f} → SL {current_sl} → {new_sl:.5f} (breakeven)"
                                        )
                            else:  # SELL
                                new_sl = entry - offset_price
                                if new_sl < current_sl:
                                    result = order_manager.modify_position(ticket, sl=new_sl)
                                    if result.success:
                                        be_moved_tickets.add(ticket)
                                        logger.info(
                                            f"[BE STOP] #{ticket} {symbol} SELL: profit +{pos['profit']:.2f} >= "
                                            f"trigger +{trigger_usd:.2f} → SL {current_sl} → {new_sl:.5f} (breakeven)"
                                        )

                # Curata be_moved_tickets pentru pozitii inchise
                open_tickets = {p["ticket"] for p in open_positions}
                be_moved_tickets = be_moved_tickets & open_tickets

                # ============================================
                # PROFIT MANAGER — trailing profit + cycle target
                # ============================================
                # NOU 2026-11-10: pozitiile "let TP run" sunt excluse din cycle-close si trailing
                managed_positions = [p for p in open_positions if not _is_let_tp_run(p)]
                if managed_positions:
                    total_unrealized = sum(p["profit"] for p in managed_positions)

                    # Verificare cycle target — inchide pozitiile gestionate (nu si cele let-TP-run)
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
                    for pos in managed_positions:
                        should_close = profit_manager.check_position_trailing(
                            pos["ticket"], pos["profit"]
                        )
                        if should_close:
                            result = order_manager.close_position(pos["ticket"], comment="trailing_profit")
                            if result.success:
                                profit_manager.record_position_close(pos["ticket"], pos["profit"])
                                # NOU 2026-07-01: track per-symbol PnL pentru daily loss limit
                                risk_manager.record_trade(pos["profit"], symbol=pos["symbol"])
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
                    default_timeframe = strat_info["timeframe"]
                    tf_overrides = strat_info.get("symbol_tf_overrides", {}) or {}

                    for symbol in symbols:
                        # NOU 2026-07-01: TF per simbol (override daca exista)
                        timeframe = tf_overrides.get(symbol, default_timeframe)

                        # NOU 2026-07-01: per-symbol daily loss check (izolare)
                        sym_disabled, sym_reason = risk_manager.is_symbol_disabled_today(symbol)
                        if sym_disabled:
                            logger.info(f"[SYMBOL DISABLED] {symbol}: {sym_reason} — skip azi")
                            continue

                        # Stock market hours check — stocks doar intre 16:30-22:55 RO
                        if symbol in stock_symbols:
                            if not is_trading_hours(stock_trading_hours, timezone_str):
                                logger.debug(f"[STOCKS HOURS] {symbol}: in afara {stock_trading_hours} — skip")
                                continue

                        # Cooldown check — nu re-intra pe simbol daca s-a inchis recent
                        last_close = symbol_last_close_time.get(symbol, 0)
                        if time.time() - last_close < position_cooldown:
                            remaining = int(position_cooldown - (time.time() - last_close))
                            # INFO (era DEBUG) pentru vizibilitate — bug INTC 2026-05-08 cooldown ignorat
                            logger.info(f"[COOLDOWN] {symbol}: astept inca {remaining}s (last close: {last_close:.0f})")
                            continue

                        # News blackout check
                        is_blackout, blackout_reason = news_calendar.is_news_blackout(symbol)
                        if is_blackout:
                            logger.info(f"[NEWS BLACKOUT] {symbol}: {blackout_reason} — skip")
                            continue

                        # Earnings blackout (doar pentru stocks cu earnings calendar)
                        if symbol in stock_symbols:
                            try:
                                from superbot.entry_filter import check_earnings_blackout
                                blackout_days = stocks_config.get("earnings_blackout_days", 2)
                                eb, eb_reason = check_earnings_blackout(symbol, blackout_days=blackout_days)
                                if eb:
                                    logger.info(f"[EARNINGS BLACKOUT] {symbol}: {eb_reason} — skip")
                                    continue
                            except Exception as e:
                                logger.debug(f"earnings blackout check error pe {symbol}: {e}")

                        # Fetch date
                        # NOU 2026-08-18: AdaptiveMTF fetch multi-TF (D1+H4+H1), altfel single TF
                        if isinstance(strategy, AdaptiveMTFStrategy):
                            data_d1 = data_fetcher.get_rates(symbol, "D1", bars=200)
                            data_h4 = data_fetcher.get_rates(symbol, "H4", bars=200)
                            data_h1 = data_fetcher.get_rates(symbol, "H1", bars=200)
                            if data_d1 is None or data_h4 is None or data_h1 is None:
                                continue
                            if len(data_d1) < 60 or len(data_h4) < 30 or len(data_h1) < 30:
                                continue
                            data = data_h1  # pentru compat downstream (SL calc etc)
                        else:
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

                        # ZoneBasedStrategy: seteaza simbolul pt lookup zone
                        if isinstance(strategy, ZoneBasedStrategy):
                            strategy.set_current_symbol(symbol)

                        # AdaptiveMTFStrategy: seteaza simbolul pt state intern
                        if isinstance(strategy, AdaptiveMTFStrategy):
                            strategy.set_current_symbol(symbol)

                        # Analiza
                        # NOU 2026-08-18: AdaptiveMTF foloseste analyze_mtf cu 3 TF-uri
                        if isinstance(strategy, AdaptiveMTFStrategy):
                            signal_result = strategy.analyze_mtf(data_d1, data_h4, data_h1, symbol)
                        else:
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
                        # NOU 2026-08-03: ZoneBasedStrategy override SL/TP cu zone-specific
                        # FIX 2026-08-05: HARD FAIL daca zone_sl lipseste — nu cade la ATR
                        # NOU 2026-08-18: AdaptiveMTFStrategy foloseste SL/TP dinamic (ATR H4)
                        sl = None
                        tp = None
                        if isinstance(strategy, (ZoneBasedStrategy, AdaptiveMTFStrategy)):
                            zsl, ztp = strategy.get_sl_tp(symbol, direction, current_price)
                            if zsl is None or ztp is None:
                                logger.error(f"[{strategy.name}] {symbol} {direction}: SL/TP LIPSA — SKIP trade")
                                continue
                            sl = zsl
                            tp = ztp
                            logger.info(f"[{strategy.name}] {symbol} {direction}: SL={sl:.5f} TP={tp:.5f}")
                        else:
                            # Non-adaptive: calcul default
                            sl = risk_manager.calculate_stop_loss(data, direction, current_price, symbol)
                            tp = risk_manager.calculate_take_profit(current_price, sl, direction, symbol)

                        sl_pips = abs(current_price - sl) / pips_to_price(symbol, 1)
                        volume = risk_manager.calculate_position_size(
                            account_info["balance"], sl_pips, symbol
                        )

                        # Aplica SuperBot size multiplier
                        # FIX 2026-08-05: BYPASS SuperBot multiplier pentru Zone/AdaptiveMTF
                        # Motiv: strategiile astea au logica proprie SL/TP + lot fix, nu vrem amplificare externa
                        if superbot_size_mult != 1.0 and not isinstance(strategy, (ZoneBasedStrategy, AdaptiveMTFStrategy)):
                            volume = round(volume * superbot_size_mult, 2)
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
                            risk_manager.record_trade(0, symbol=symbol)
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
