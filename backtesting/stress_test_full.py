"""
STRESS TEST cu risk constraints reale.

Pentru TOP 20 combinatii unice (sym × strategy):
- 5 timeframes: M15, M30, H1, H4, D1
- 9 loturi: 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10
- 6 perioade: 30, 60, 90, 120, 150, 180 zile

Constraints aplicate:
- Daily loss limit -$53 (bot opreste pe ziua aceea daca depaseste)
- Max DD 10% din cont $5700 = -$570 -> BLOW-UP (account dead)
- Spread real per simbol (din MT5)
- Commission $0.10/lot estimate

Output: results/stress_test_*.csv
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout
from strategies.mean_reversion import MeanReversion
from strategies.base_strategy import Signal

# ============================================
# CONFIG
# ============================================

BUCHAREST_TZ = ZoneInfo("Europe/Bucharest")
INITIAL_BALANCE = 5700.0
DAILY_LOSS_LIMIT = -53.0
MAX_DD_PCT = 10.0  # 10% din balance = blow-up
BLOW_UP_THRESHOLD = INITIAL_BALANCE * MAX_DD_PCT / 100.0  # -$570

COMMISSION_PER_LOT = 0.10  # $0.10 per trade per 0.10 lot ratio
CRYPTO_CLOSE_RO = 22

LOOKBACK_DAYS = 200  # buffer pentru perioada 180 + indicator warmup
PERIODS_DAYS = [30, 60, 90, 120, 150, 180]
LOTS = [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10]

# TOP 20 — unique (sym, strategy) combinations
TOP_COMBOS = [
    ("XPDUSD.cfd",  "MeanReversion"),
    ("XPTUSD.cfd",  "MeanReversion"),
    ("XPTUSD.cfd",  "Breakout"),
    ("XAUUSD.cfd",  "MeanReversion"),
    ("XAUUSD.cfd",  "Breakout"),
    ("XAGUSD.cfd",  "TrendFollowing"),
    ("XAGUSD.cfd",  "Breakout"),
    ("XAGEUR.cfd",  "MeanReversion"),
    ("XAGEUR.cfd",  "TrendFollowing"),
    ("BRENT.cfd",   "Breakout"),
    ("BRENT.cfd",   "TrendFollowing"),
    ("BRENT.cfd",   "MeanReversion"),
    ("WTI.cfd",     "Breakout"),
]

TF_MAP = {
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

STRATEGY_PARAMS = {
    "TrendFollowing": {"ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 25, "adx_period": 14},
    "Breakout":       {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 1.5},
    "MeanReversion":  {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, "bb_period": 20, "bb_std_dev": 2.0}
}


def init_mt5():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")
    info = mt5.account_info()
    print(f"MT5 conectat: cont {info.login if info else '?'}")


def pnl_usd(symbol_info, direction, open_price, close_price, lot):
    """PnL in USD folosind tick_value."""
    if symbol_info.trade_tick_size == 0 or symbol_info.trade_tick_value == 0:
        return 0
    sign = 1 if direction == "BUY" else -1
    diff = sign * (close_price - open_price)
    ticks = diff / symbol_info.trade_tick_size
    return ticks * symbol_info.trade_tick_value * lot


def get_atr(data, period=14):
    if len(data) < period + 1:
        return None
    atr = ta.atr(data["high"], data["low"], data["close"], length=period)
    val = atr.iloc[-1]
    return float(val) if pd.notna(val) and val > 0 else None


def calc_sl_tp(direction, entry, data, rr_ratio=2.0, atr_mult=1.5):
    atr = get_atr(data, 14)
    if atr is None:
        return None, None
    sl_d = atr * atr_mult
    tp_d = sl_d * rr_ratio
    if direction == "BUY":
        return entry - sl_d, entry + tp_d
    return entry + sl_d, entry - tp_d


def download_data(symbol, tf, days=LOOKBACK_DAYS):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, TF_MAP[tf], utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def create_strategy(name):
    if name == "TrendFollowing":
        return TrendFollowing(params=STRATEGY_PARAMS["TrendFollowing"])
    elif name == "Breakout":
        return Breakout(params=STRATEGY_PARAMS["Breakout"])
    elif name == "MeanReversion":
        return MeanReversion(params=STRATEGY_PARAMS["MeanReversion"])
    return None


def run_one_backtest(symbol, strategy_name, tf_name, lot, df, is_crypto=False):
    """Backtest pe TOATA istoria (180 zile), apoi calculam metrici pe perioade.
    Aplica daily_loss_limit + blow_up check.

    Returns dict: list of trades + per-period stats.
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return None

    strategy = create_strategy(strategy_name)
    if strategy is None:
        return None

    trades = []
    position = None
    lookback = 60

    # State pentru constraints
    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    daily_pnl = 0.0
    current_day = None
    blow_up = False
    blow_up_at = None
    daily_limit_hits = 0
    days_disabled = set()

    for i in range(lookback, len(df)):
        if blow_up:
            break

        bar = df.iloc[i]
        bar_time = df.index[i]
        bar_dt = bar_time.to_pydatetime().replace(tzinfo=ZoneInfo("UTC"))
        bar_dt_ro = bar_dt.astimezone(BUCHAREST_TZ)
        day_key = bar_dt_ro.date()

        # Reset daily PnL la zi noua
        if current_day != day_key:
            current_day = day_key
            daily_pnl = 0.0

        # Verifica daca ziua e disabled din cauza limitei zilnice
        skip_today = day_key in days_disabled

        # Verifica pozitie deschisa
        if position is not None:
            closed = False
            exit_price = None
            reason = None

            if position["direction"] == "BUY":
                if bar["low"] <= position["sl"]:
                    exit_price = position["sl"]; reason = "SL"; closed = True
                elif bar["high"] >= position["tp"]:
                    exit_price = position["tp"]; reason = "TP"; closed = True
            else:
                if bar["high"] >= position["sl"]:
                    exit_price = position["sl"]; reason = "SL"; closed = True
                elif bar["low"] <= position["tp"]:
                    exit_price = position["tp"]; reason = "TP"; closed = True

            # Intraday close crypto 22:00 RO
            if not closed and is_crypto and bar_dt_ro.hour >= CRYPTO_CLOSE_RO:
                exit_price = bar["close"]; reason = "INTRADAY"; closed = True

            if closed:
                pnl = pnl_usd(symbol_info, position["direction"], position["entry"], exit_price, lot)
                # Commission
                comm = COMMISSION_PER_LOT * (lot / 0.10)  # proportional
                pnl -= comm

                balance += pnl
                daily_pnl += pnl

                # Update peak/DD
                if balance > peak_balance:
                    peak_balance = balance
                dd = peak_balance - balance

                # Verifica BLOW-UP (DD > 10%)
                if dd >= BLOW_UP_THRESHOLD:
                    blow_up = True
                    blow_up_at = bar_time

                # Verifica daily loss limit
                if daily_pnl <= DAILY_LOSS_LIMIT:
                    days_disabled.add(day_key)
                    daily_limit_hits += 1

                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": bar_time,
                    "direction": position["direction"],
                    "entry": position["entry"],
                    "exit": exit_price,
                    "pnl": pnl,
                    "reason": reason,
                    "balance_after": balance,
                    "dd_after": dd,
                })
                position = None

        # Open new position only if no position and day not disabled
        if position is None and not skip_today:
            window = df.iloc[:i + 1].copy()
            try:
                signal = strategy.analyze(window)
            except Exception:
                continue
            if signal == Signal.HOLD:
                continue
            direction = signal.value
            entry = float(bar["close"])
            sl, tp = calc_sl_tp(direction, entry, window)
            if sl is None or tp is None:
                continue
            position = {
                "entry_time": bar_time,
                "direction": direction,
                "entry": entry,
                "sl": sl, "tp": tp,
            }

    return {
        "trades": trades,
        "blow_up": blow_up,
        "blow_up_at": blow_up_at,
        "daily_limit_hits": daily_limit_hits,
        "days_disabled_count": len(days_disabled),
        "final_balance": balance,
    }


def main():
    init_mt5()

    total = len(TOP_COMBOS) * len(TF_MAP) * len(LOTS)
    print(f"\nStress test: {len(TOP_COMBOS)} combinatii × {len(TF_MAP)} TF × {len(LOTS)} loturi = {total} backtests")
    print(f"Per backtest, calculam {len(PERIODS_DAYS)} perioade")
    print(f"Constraints: daily_loss_limit=${DAILY_LOSS_LIMIT}, blow_up_threshold=${-BLOW_UP_THRESHOLD}")
    print(f"="*100)

    # Pre-download data pentru toate (sym, TF) unice
    print(f"\nPre-download date (180+ zile) pentru toate (sym × TF)...")
    data_cache = {}
    unique_syms = list(set(s for s, _ in TOP_COMBOS))
    for sym in unique_syms:
        info = mt5.symbol_info(sym)
        if info is None or not info.visible:
            try: mt5.symbol_select(sym, True)
            except: pass
        for tf in TF_MAP:
            df = download_data(sym, tf)
            if df is not None and len(df) > 100:
                data_cache[(sym, tf)] = df
        loaded = sum(1 for k in data_cache if k[0] == sym)
        print(f"  {sym}: {loaded}/{len(TF_MAP)} TF")

    print(f"\nIncep backtest stress test...")
    results = []
    count = 0

    for sym, strat in TOP_COMBOS:
        is_crypto = sym.startswith(("BTC", "ETH", "SOL", "DSH", "AVE", "BNB", "DOG", "XRP"))
        for tf in TF_MAP:
            df = data_cache.get((sym, tf))
            if df is None:
                continue
            for lot in LOTS:
                count += 1
                res = run_one_backtest(sym, strat, tf, lot, df, is_crypto=is_crypto)
                if res is None:
                    continue

                # Per-period analytics
                now = df.index[-1]
                for period_days in PERIODS_DAYS:
                    cutoff = now - pd.Timedelta(days=period_days)
                    p_trades = [t for t in res["trades"] if t["entry_time"] >= cutoff]
                    if len(p_trades) == 0:
                        results.append({
                            "symbol": sym, "strategy": strat, "tf": tf, "lot": lot,
                            "period_days": period_days,
                            "trades": 0, "wins": 0, "wr": 0, "net_pnl": 0,
                            "max_dd": 0, "blow_up": False, "blow_up_day": None,
                            "daily_limit_hits": 0
                        })
                        continue

                    wins = sum(1 for t in p_trades if t["pnl"] > 0)
                    net = sum(t["pnl"] for t in p_trades)

                    # Period-specific DD
                    equity = 0; peak = 0; max_dd = 0
                    for t in p_trades:
                        equity += t["pnl"]
                        if equity > peak: peak = equity
                        if peak - equity > max_dd: max_dd = peak - equity

                    # Was period blow-up?
                    period_blow_up = res["blow_up"] and res["blow_up_at"] >= cutoff
                    blow_up_day = res["blow_up_at"].date() if period_blow_up else None

                    results.append({
                        "symbol": sym, "strategy": strat, "tf": tf, "lot": lot,
                        "period_days": period_days,
                        "trades": len(p_trades),
                        "wins": wins,
                        "wr": round(wins / len(p_trades) * 100, 1),
                        "net_pnl": round(net, 2),
                        "max_dd": round(max_dd, 2),
                        "blow_up": period_blow_up,
                        "blow_up_day": str(blow_up_day) if blow_up_day else "",
                        "daily_limit_hits": res["daily_limit_hits"]
                    })

                if count % 50 == 0:
                    bu = "BLOW-UP!" if res["blow_up"] else "OK"
                    print(f"  {count}/{total} - {sym} {strat} {tf} lot={lot}: {len(res['trades'])} trades, {bu}")

    df_res = pd.DataFrame(results)
    os.makedirs("results", exist_ok=True)
    csv = f"results/stress_test_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df_res.to_csv(csv, index=False)
    print(f"\nRezultate: {len(results)} rows in {csv}")

    # Analysis 1: blow-ups
    blow_ups = df_res[df_res["blow_up"]]
    print(f"\n{'='*100}")
    print(f"  BLOW-UPS DETECTATE: {len(blow_ups)}")
    print(f"{'='*100}")
    if len(blow_ups) > 0:
        bu_summary = blow_ups.groupby(["symbol", "strategy", "tf", "lot"]).size()
        print(bu_summary.head(20))

    # Analysis 2: TOP 20 net_pnl pe 90 zile, NO blow-up
    df_90 = df_res[(df_res["period_days"] == 90) & (~df_res["blow_up"]) & (df_res["trades"] >= 5)].copy()
    df_90 = df_90.sort_values("net_pnl", ascending=False).head(30)
    print(f"\n{'='*120}")
    print(f"  TOP 30 PROFITABILE (90 zile, fara blow-up, min 5 trades)")
    print(f"{'='*120}")
    print(f"{'#':>3} {'Symbol':<14} {'Strategy':<14} {'TF':<5} {'Lot':>6} {'Trades':>7} {'WR%':>6} {'NET':>9} {'DD':>9} {'DailyHits':>10}")
    for i, (_, r) in enumerate(df_90.iterrows(), 1):
        print(f"{i:>3} {r['symbol']:<14} {r['strategy']:<14} {r['tf']:<5} {r['lot']:>5.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net_pnl']:>+7.2f} ${r['max_dd']:>+7.2f} {r['daily_limit_hits']:>10}")

    # Analysis 3: Lot scaling per top combo
    df_lot_summary = df_res[df_res["period_days"] == 90].groupby(["symbol", "strategy", "tf", "lot"]).agg(
        net=("net_pnl", "mean"),
        dd=("max_dd", "mean"),
        blow_pct=("blow_up", lambda x: x.sum() / len(x) * 100)
    ).reset_index()

    mt5.shutdown()
    print(f"\nDone. CSV: {csv}")


if __name__ == "__main__":
    main()
