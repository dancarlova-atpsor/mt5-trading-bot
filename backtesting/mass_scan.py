"""
Scan MASIV — 50+ simboluri pe Breakout M30 + MeanReversion H1.
Foloseste PARAMS validati din stress test si compare_params (CURRENT config).

Categorii:
- Forex majors + crosses volatile
- Metale (toate variante XAU/XAG/XPT/XPD)
- Commodities (oil, sugar, coffee, cocoa)
- Indices (nikkei, dax, us500, us2000)
- Crypto majors (BTC, ETH, SOL, ADA, DOT, LTC, XRP)

Filtreaza si arata TOP 30 profitable.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
from strategies.breakout import Breakout
from strategies.mean_reversion import MeanReversion
from strategies.base_strategy import Signal

LOOKBACK = 120  # date brute
PERIOD = 90    # analiza ultima perioada

# 60+ simboluri — mix complet
SYMBOLS = [
    # Forex majors
    "EURUSD.cfd", "GBPUSD.cfd", "USDJPY.cfd", "USDCHF.cfd", "AUDUSD.cfd", "NZDUSD.cfd", "USDCAD.cfd",
    # Forex JPY crosses (volatile)
    "EURJPY.cfd", "GBPJPY.cfd", "AUDJPY.cfd", "NZDJPY.cfd", "CHFJPY.cfd", "CADJPY.cfd",
    # Forex crosses
    "EURGBP.cfd", "EURAUD.cfd", "GBPAUD.cfd", "AUDNZD.cfd", "AUDCAD.cfd", "NZDCAD.cfd",
    # Exotic (volatile dar swap toxic overnight)
    "USDSEK.cfd", "USDNOK.cfd", "USDPLN.cfd", "USDCZK.cfd",
    # Metale
    "XAUUSD.cfd", "XAGUSD.cfd", "XPTUSD.cfd", "XPDUSD.cfd",
    "XAUEUR.cfd", "XAGEUR.cfd",
    # Commodities
    "BRENT.cfd", "WTI.cfd", "COCOA.cfd", "COFFEE.cfd", "SUGAR.cfd",
    # Indices
    "NIKKEI.cfd", "US2000.cfd",
    # Crypto majors (test 24/7)
    "BTCUSD.cfd", "ETHUSD.cfd", "SOLUSD.cfd", "XRPUSD.cfd", "ADAUSD.cfd",
    "LTCUSD.cfd", "DOTUSD.cfd", "AVEUSD.cfd", "LNKUSD.cfd", "BCHUSD.cfd",
    "DOGUSD.cfd", "ETCUSD.cfd", "XLMUSD.cfd", "AVXUSD.cfd", "UNIUSD.cfd",
    "XMRUSD.cfd", "DSHUSD.cfd", "BNBUSD.cfd",
]

# Lot fix per simbol (conservator, backtest-uri anterioare validate)
# Pe cele necunoscute folosim 0.02 lot default
DEFAULT_LOT = 0.02
LOT_OVERRIDES = {
    "BRENT.cfd": 0.05, "WTI.cfd": 0.05,
    "XAUUSD.cfd": 0.02, "XAGUSD.cfd": 0.02, "XPTUSD.cfd": 0.02, "XPDUSD.cfd": 0.02,
    "XAUEUR.cfd": 0.02, "XAGEUR.cfd": 0.02,
    "COCOA.cfd": 0.02, "COFFEE.cfd": 0.02, "SUGAR.cfd": 0.02,
    "NIKKEI.cfd": 0.02, "US2000.cfd": 0.02,
    # Crypto — lot micut
    "BTCUSD.cfd": 0.01, "ETHUSD.cfd": 0.02, "SOLUSD.cfd": 0.05,
    "XRPUSD.cfd": 5.0, "ADAUSD.cfd": 5.0, "LTCUSD.cfd": 0.05,
    "DOTUSD.cfd": 1.0, "AVEUSD.cfd": 0.05, "LNKUSD.cfd": 0.5,
    "BCHUSD.cfd": 0.05, "DOGUSD.cfd": 100.0, "ETCUSD.cfd": 0.5,
    "XLMUSD.cfd": 10.0, "AVXUSD.cfd": 0.5, "UNIUSD.cfd": 1.0,
    "XMRUSD.cfd": 0.05, "DSHUSD.cfd": 0.1, "BNBUSD.cfd": 0.05,
}

TF_MAP = {"M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1}

BR_PARAMS = {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 1.5}
MR_PARAMS = {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, "bb_period": 20, "bb_std_dev": 2.0}

ATR_MULT = 1.5
RR = 3.0


def init_mt5():
    if not mt5.initialize(): sys.exit("MT5 nu se conecteaza")
    print(f"MT5: {mt5.account_info().login}")


def dl(sym, tf):
    r = mt5.copy_rates_range(sym, TF_MAP[tf], datetime.now()-timedelta(days=LOOKBACK), datetime.now())
    if r is None or len(r)==0: return None
    df = pd.DataFrame(r); df["time"]=pd.to_datetime(df["time"],unit="s"); df.set_index("time",inplace=True)
    return df


def pnl_calc(si, d, op, cp, lot):
    if si.trade_tick_size==0 or si.trade_tick_value==0: return 0
    s = 1 if d=="BUY" else -1
    return (s*(cp-op)/si.trade_tick_size)*si.trade_tick_value*lot


def atr_val(data, p=14):
    if len(data)<p+1: return None
    a = ta.atr(data["high"],data["low"],data["close"],length=p).iloc[-1]
    return float(a) if pd.notna(a) and a>0 else None


def bt(symbol, strategy, df, lot, si):
    trades = []
    pos = None
    for i in range(60, len(df)):
        bar = df.iloc[i]
        w = df.iloc[:i+1].copy()

        if pos:
            closed = False; ep = None; reason = None
            if pos["dir"] == "BUY":
                if bar["low"] <= pos["sl"]: ep = pos["sl"]; reason="SL"; closed=True
                elif bar["high"] >= pos["tp"]: ep = pos["tp"]; reason="TP"; closed=True
            else:
                if bar["high"] >= pos["sl"]: ep = pos["sl"]; reason="SL"; closed=True
                elif bar["low"] <= pos["tp"]: ep = pos["tp"]; reason="TP"; closed=True
            if closed:
                trades.append({"t": pos["t"], "pnl": pnl_calc(si, pos["dir"], pos["entry"], ep, lot)})
                pos = None

        if pos is None:
            try: sig = strategy.analyze(w)
            except: continue
            if sig == Signal.HOLD: continue
            direction = sig.value
            entry = float(bar["close"])
            atr = atr_val(w)
            if atr is None: continue
            sl_d = atr * ATR_MULT
            tp_d = sl_d * RR
            sl = entry - sl_d if direction == "BUY" else entry + sl_d
            tp = entry + tp_d if direction == "BUY" else entry - tp_d
            pos = {"t": df.index[i], "dir": direction, "entry": entry, "sl": sl, "tp": tp}

    return trades


def main():
    init_mt5()
    print(f"\nScan pe {len(SYMBOLS)} simboluri × 2 strategii × 2 TF = {len(SYMBOLS)*4} backtests\n")

    results = []
    count = 0
    total = len(SYMBOLS) * 4

    for sym in SYMBOLS:
        # Verifica disponibilitate
        info = mt5.symbol_info(sym)
        if info is None:
            print(f"  SKIP {sym}: nu exista pe broker")
            continue
        if not info.visible:
            try: mt5.symbol_select(sym, True); info = mt5.symbol_info(sym)
            except: pass

        lot = LOT_OVERRIDES.get(sym, DEFAULT_LOT)

        for tf in TF_MAP:
            df = dl(sym, tf)
            if df is None or len(df) < 100:
                count += 2  # skip both strategies
                continue

            for strat_name, strat_cls, strat_params in [
                ("Breakout", Breakout, BR_PARAMS),
                ("MeanRev", MeanReversion, MR_PARAMS),
            ]:
                count += 1
                strat = strat_cls(params=strat_params)
                trades = bt(sym, strat, df, lot, info)
                # Filter perioada 90 zile
                cutoff = df.index[-1] - pd.Timedelta(days=PERIOD)
                pt = [t for t in trades if t["t"] >= cutoff]
                if not pt:
                    results.append({"sym": sym, "strat": strat_name, "tf": tf, "lot": lot, "trades": 0, "wr": 0, "net": 0})
                    continue
                wins = [t for t in pt if t["pnl"] > 0]
                net = sum(t["pnl"] for t in pt)
                results.append({
                    "sym": sym, "strat": strat_name, "tf": tf, "lot": lot,
                    "trades": len(pt),
                    "wr": round(len(wins)/len(pt)*100, 1),
                    "net": round(net, 2),
                    "avg_win": round(sum(t["pnl"] for t in wins)/len(wins), 2) if wins else 0,
                    "avg_loss": round(sum(t["pnl"] for t in pt if t["pnl"]<=0)/max(len(pt)-len(wins), 1), 2) if len(pt)-len(wins)>0 else 0
                })
                if count % 20 == 0:
                    print(f"  {count}/{total}: {sym} {strat_name} {tf} → {len(pt)} trades, NET ${net:+.2f}")

    df_r = pd.DataFrame(results)
    df_r = df_r[df_r["trades"] >= 5]  # filter minim 5 trades
    df_r_sorted = df_r.sort_values("net", ascending=False)

    print("\n" + "="*130)
    print(f"  TOP 30 PROFITABILE (min 5 trades, 90 zile)")
    print("="*130)
    print(f"{'#':>3} {'Symbol':<14} {'Strategy':<10} {'TF':<5} {'Lot':>7} {'Trades':>7} {'WR%':>6} {'AvgW':>8} {'AvgL':>8} {'NET':>10} {'$/luna':>8}")
    for i, r in enumerate(df_r_sorted.head(30).iterrows(), 1):
        _, row = r
        per_month = row["net"] / 90 * 22
        print(f"{i:>3} {row['sym']:<14} {row['strat']:<10} {row['tf']:<5} {row['lot']:>6.2f} {row['trades']:>7} {row['wr']:>5.1f}% ${row['avg_win']:>+6.2f} ${row['avg_loss']:>+6.2f} ${row['net']:>+8.2f} ${per_month:>+7.2f}")

    # Save all
    df_r.to_csv("results/mass_scan.csv", index=False)

    # Best combinatie (top 5 selectate)
    top5 = df_r_sorted.head(5)
    total_month = sum(r["net"]/90*22 for _, r in top5.iterrows())
    print(f"\n  Top 5 combinate = ${total_month:+.2f}/luna estimat")

    mt5.shutdown()
    print("\nDone. CSV: results/mass_scan.csv")


if __name__ == "__main__":
    main()
