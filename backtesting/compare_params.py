"""
Compara configuratii STRICT vs RELAXAT pe BRENT + WTI.
Scop: gaseste params care cresc frecventa trade-urilor fara sa strice profitul.

Configuratii testate:
- STRICT (actual): MeanRev RSI 30/70, Breakout vol 1.5 lookback 50
- RELAXAT: MeanRev RSI 35/65, Breakout vol 1.2 lookback 30
- RELAXAT++: MeanRev RSI 40/60, Breakout vol 1.0 lookback 20
- +TrendFollowing: adauga TF pe BRENT+WTI

TF: M15 (breakout), H1 (mean_reversion + trend)
Perioade: 30, 60, 90 zile
Lot: BRENT 0.08, WTI 0.07
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout
from strategies.mean_reversion import MeanReversion
from strategies.base_strategy import Signal

LOOKBACK_DAYS = 100
PERIODS = [30, 60, 90]
LOTS = {"BRENT.cfd": 0.08, "WTI.cfd": 0.07}

TF_MAP = {"M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1}

# Configuratii de testat
CONFIGS = {
    "STRICT (actual)": {
        "mean_reversion": {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, "bb_period": 20, "bb_std_dev": 2.0},
        "breakout": {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 1.5},
    },
    "RELAXAT": {
        "mean_reversion": {"rsi_period": 14, "rsi_oversold": 35, "rsi_overbought": 65, "bb_period": 20, "bb_std_dev": 2.0},
        "breakout": {"lookback_period": 30, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 1.2},
    },
    "RELAXAT++": {
        "mean_reversion": {"rsi_period": 14, "rsi_oversold": 40, "rsi_overbought": 60, "bb_period": 20, "bb_std_dev": 1.8},
        "breakout": {"lookback_period": 20, "atr_period": 14, "min_atr_ratio": 0.7, "min_vol_ratio": 1.0},
    },
}

TF_PARAMS = {"ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 25, "adx_period": 14}


def init_mt5():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")
    print(f"MT5 conectat: {mt5.account_info().login}")


def download(symbol, tf):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=LOOKBACK_DAYS)
    rates = mt5.copy_rates_range(symbol, TF_MAP[tf], utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def pnl_usd(si, direction, op, cp, lot):
    if si.trade_tick_size == 0 or si.trade_tick_value == 0:
        return 0
    sign = 1 if direction == "BUY" else -1
    return (sign * (cp - op) / si.trade_tick_size) * si.trade_tick_value * lot


def get_atr(data, period=14):
    if len(data) < period + 1:
        return None
    atr = ta.atr(data["high"], data["low"], data["close"], length=period)
    v = atr.iloc[-1]
    return float(v) if pd.notna(v) and v > 0 else None


def sl_tp(direction, entry, data, rr=2.0, mult=1.5):
    atr = get_atr(data)
    if atr is None:
        return None, None
    sl_d = atr * mult
    tp_d = sl_d * rr
    if direction == "BUY":
        return entry - sl_d, entry + tp_d
    return entry + sl_d, entry - tp_d


def backtest(symbol, strategy, df, lot):
    si = mt5.symbol_info(symbol)
    trades = []
    pos = None
    for i in range(60, len(df)):
        bar = df.iloc[i]
        if pos:
            closed = False; ep = None
            if pos["dir"] == "BUY":
                if bar["low"] <= pos["sl"]: ep = pos["sl"]; closed = True
                elif bar["high"] >= pos["tp"]: ep = pos["tp"]; closed = True
            else:
                if bar["high"] >= pos["sl"]: ep = pos["sl"]; closed = True
                elif bar["low"] <= pos["tp"]: ep = pos["tp"]; closed = True
            if closed:
                trades.append({"entry_time": pos["t"], "pnl": pnl_usd(si, pos["dir"], pos["entry"], ep, lot)})
                pos = None
        if pos is None:
            w = df.iloc[:i+1].copy()
            try: sig = strategy.analyze(w)
            except: continue
            if sig == Signal.HOLD: continue
            d = sig.value
            entry = float(bar["close"])
            sl, tp = sl_tp(d, entry, w)
            if sl is None: continue
            pos = {"t": df.index[i], "dir": d, "entry": entry, "sl": sl, "tp": tp}
    return trades


def main():
    init_mt5()
    # Pre-download
    data = {}
    for sym in LOTS:
        for tf in TF_MAP:
            d = download(sym, tf)
            if d is not None: data[(sym, tf)] = d
    print(f"Date incarcate: {len(data)}\n")

    results = []
    for config_name, cfg in CONFIGS.items():
        for sym in LOTS:
            lot = LOTS[sym]
            # Breakout pe M15
            br = Breakout(params=cfg["breakout"])
            trades_br = backtest(sym, br, data[(sym, "M15")], lot)
            # MeanReversion pe H1
            mr = MeanReversion(params=cfg["mean_reversion"])
            trades_mr = backtest(sym, mr, data[(sym, "H1")], lot)
            for strat_name, trades in [("Breakout-M15", trades_br), ("MeanRev-H1", trades_mr)]:
                now = data[(sym, "M15")].index[-1]
                for pd_days in PERIODS:
                    cutoff = now - pd.Timedelta(days=pd_days)
                    pt = [t for t in trades if t["entry_time"] >= cutoff]
                    if not pt:
                        results.append({"config": config_name, "symbol": sym, "strat": strat_name, "period": pd_days, "trades": 0, "wr": 0, "net": 0})
                        continue
                    wins = sum(1 for t in pt if t["pnl"] > 0)
                    net = sum(t["pnl"] for t in pt)
                    results.append({"config": config_name, "symbol": sym, "strat": strat_name, "period": pd_days,
                                    "trades": len(pt), "wr": round(wins/len(pt)*100, 1), "net": round(net, 2)})

    # Test si TrendFollowing relaxat
    for sym in LOTS:
        lot = LOTS[sym]
        tf_strat = TrendFollowing(params=TF_PARAMS)
        trades_tf = backtest(sym, tf_strat, data[(sym, "H1")], lot)
        now = data[(sym, "H1")].index[-1]
        for pd_days in PERIODS:
            cutoff = now - pd.Timedelta(days=pd_days)
            pt = [t for t in trades_tf if t["entry_time"] >= cutoff]
            if not pt:
                results.append({"config": "+TrendFollow", "symbol": sym, "strat": "TrendFollow-H1", "period": pd_days, "trades": 0, "wr": 0, "net": 0})
                continue
            wins = sum(1 for t in pt if t["pnl"] > 0)
            net = sum(t["pnl"] for t in pt)
            results.append({"config": "+TrendFollow", "symbol": sym, "strat": "TrendFollow-H1", "period": pd_days,
                            "trades": len(pt), "wr": round(wins/len(pt)*100, 1), "net": round(net, 2)})

    df_r = pd.DataFrame(results)

    # Sumar pe 90 zile
    print("="*110)
    print("  REZULTATE 90 ZILE — per configuratie + simbol + strategie")
    print("="*110)
    print(f"{'Config':<18} {'Symbol':<12} {'Strategy':<16} {'Trades':>7} {'WR%':>6} {'NET':>10} {'Tr/zi':>7}")
    print("-"*110)
    d90 = df_r[df_r["period"] == 90].sort_values(["config", "symbol", "strat"])
    for _, r in d90.iterrows():
        tr_zi = r["trades"] / 90
        print(f"{r['config']:<18} {r['symbol']:<12} {r['strat']:<16} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net']:>+8.2f} {tr_zi:>6.2f}")

    # Total per config (90 zile)
    print("\n" + "="*70)
    print("  TOTAL NET per configuratie (90 zile, BRENT+WTI combinat)")
    print("="*70)
    for config_name in list(CONFIGS.keys()) + ["+TrendFollow"]:
        sub = d90[d90["config"] == config_name]
        if len(sub) == 0: continue
        total_net = sub["net"].sum()
        total_trades = sub["trades"].sum()
        tr_zi = total_trades / 90
        print(f"  {config_name:<18}: NET ${total_net:>+8.2f} | {total_trades} trades ({tr_zi:.2f}/zi)")

    df_r.to_csv("results/compare_params.csv", index=False)
    mt5.shutdown()
    print("\nDone. CSV: results/compare_params.csv")


if __name__ == "__main__":
    main()
