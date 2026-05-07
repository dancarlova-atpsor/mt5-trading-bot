"""
Compara optiunile A-G discutate cu userul:
  A = Status quo (R:R 3.0, fara filtru D1)
  B = R:R 1.5, fara filtru D1
  C = R:R 3.0, CU filtru D1
  D = R:R 1.5, CU filtru D1 (combo 1+2)
  E = R:R 2.0, CU filtru D1 (compromis)

Strategia testata: TrendFollowing pe XAU, USDCHF, USDCAD
Date: ultimele 90 zile din MT5
Output: tabel comparativ + recomandare

Usage:
    .venv/Scripts/python.exe backtesting/compare_options.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from copy import deepcopy
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.base_strategy import Signal

# ============================================
# CONFIG
# ============================================
SYMBOLS = ["XAUUSD.cfd", "USDCHF.cfd", "USDCAD.cfd", "XAGUSD.cfd"]
LOOKBACK_DAYS = 90
INITIAL_BALANCE = 5700.0  # Real test 5300 EUR
SPREAD_PIPS = 1.5
COMMISSION_PER_LOT_RT = 7.0

# Per-symbol params (din strategies.yaml)
SYMBOL_PARAMS = {
    "XAUUSD.cfd": {"ema_fast": 10, "ema_slow": 70, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 30, "adx_period": 14},
    "USDCHF.cfd": {"ema_fast": 10, "ema_slow": 30, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 25, "adx_period": 14},
    "USDCAD.cfd": {"ema_fast": 15, "ema_slow": 70, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 18, "adx_period": 14},
    "XAGUSD.cfd": {"ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 25, "adx_period": 14},
}


# ============================================
# WRAPPER: Strategie cu filtru D1 optional
# ============================================
class TrendFollowingD1Filtered(TrendFollowing):
    """TrendFollowing cu filtru EMA20/50 pe D1."""

    def __init__(self, params=None, d1_data=None, h1_index=None):
        super().__init__(params=params)
        self.d1_data = d1_data  # DataFrame D1 pre-calculat
        self.h1_index = h1_index  # mapping H1 timestamp → D1 trend

    def analyze(self, data):
        # Semnal de baza
        signal = super().analyze(data)
        if signal == Signal.HOLD:
            return signal

        # Verifica D1 trend la timestamp curent
        if self.d1_data is None:
            return signal

        current_time = data.index[-1]
        # Gaseste cea mai recenta bara D1 inainte de current_time
        d1_before = self.d1_data[self.d1_data.index <= current_time]
        if len(d1_before) < 50:
            return signal  # nu am date suficiente, las semnalul

        last_d1 = d1_before.iloc[-1]
        d1_up = last_d1["ema20"] > last_d1["ema50"]

        # Filtru: BUY doar daca D1 UP, SELL doar daca D1 DOWN
        if signal == Signal.BUY and not d1_up:
            return Signal.HOLD
        if signal == Signal.SELL and d1_up:
            return Signal.HOLD

        return signal


def init_mt5():
    if not mt5.initialize():
        print("ERROR: MT5 nu se conecteaza")
        sys.exit(1)
    info = mt5.account_info()
    print(f"MT5 conectat: cont {info.login if info else '?'}")


def download_h1(symbol, days):
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def download_d1_with_emas(symbol, days):
    """Descarca D1 + calculeaza EMA20/50."""
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days + 100)  # buffer pentru EMA
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_D1, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df["ema20"] = ta.ema(df["close"], 20)
    df["ema50"] = ta.ema(df["close"], 50)
    return df


def run_backtest(symbol, h1_data, d1_data, params, rr_ratio, use_d1_filter):
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config["risk_reward_ratio"] = rr_ratio

    if use_d1_filter:
        strategy = TrendFollowingD1Filtered(params=params, d1_data=d1_data)
    else:
        strategy = TrendFollowing(params=params)

    bt = Backtester(
        strategy=strategy,
        risk_manager=rm,
        symbol=symbol,
        timeframe="H1",
        initial_balance=INITIAL_BALANCE,
        spread_pips=SPREAD_PIPS,
    )
    results = bt.run(h1_data, lookback=80)
    total_comm = results["total_trades"] * COMMISSION_PER_LOT_RT * 0.05  # ~0.05 lot mediu
    results["total_commission"] = round(total_comm, 2)
    results["net_profit_after_commission"] = round(results["net_profit"] - total_comm, 2)
    return results


def main():
    init_mt5()
    print(f"\n{'='*100}")
    print(f"  BACKTEST COMPARATIE OPTIUNI A-E pe {LOOKBACK_DAYS} zile")
    print(f"  Balance start: ${INITIAL_BALANCE} | Simboluri: {', '.join(SYMBOLS)}")
    print(f"{'='*100}\n")

    # Download data
    h1_cache = {}
    d1_cache = {}
    for sym in SYMBOLS:
        print(f"  Descarc {sym} H1+D1...", end=" ")
        h1 = download_h1(sym, LOOKBACK_DAYS)
        d1 = download_d1_with_emas(sym, LOOKBACK_DAYS)
        if len(h1) > 0 and len(d1) > 0:
            h1_cache[sym] = h1
            d1_cache[sym] = d1
            print(f"OK ({len(h1)} H1, {len(d1)} D1)")
        else:
            print("SKIP")

    if not h1_cache:
        print("ERROR: nu am date")
        return

    # Optiuni
    OPTIONS = [
        ("A", "Status quo: R:R=3.0, fara D1 filter", 3.0, False),
        ("B", "Op 1: R:R=1.5, fara D1 filter", 1.5, False),
        ("C", "Op 2: R:R=3.0 + D1 filter", 3.0, True),
        ("D", "Op 3 (1+2): R:R=1.5 + D1 filter", 1.5, True),
        ("E", "Compromis: R:R=2.0 + D1 filter", 2.0, True),
    ]

    all_results = {}  # opt_id -> {symbol -> results}
    for opt_id, opt_name, rr, d1_filt in OPTIONS:
        print(f"\n--- {opt_id}: {opt_name} ---")
        all_results[opt_id] = {}
        for sym in h1_cache:
            params = SYMBOL_PARAMS[sym]
            r = run_backtest(sym, h1_cache[sym], d1_cache[sym], params, rr, d1_filt)
            all_results[opt_id][sym] = r
            print(f"  {sym}: trades={r['total_trades']:>3}, WR={r['win_rate']:>5.1f}%, "
                  f"PF={r.get('profit_factor', 0):.2f}, NET=${r['net_profit_after_commission']:+.2f}, "
                  f"DD={r.get('max_drawdown_pct', 0):.1f}%")

    # Tabel sumar
    print(f"\n\n{'='*100}")
    print(f"  TABEL FINAL — NET P&L (dupa comisioane)")
    print(f"{'='*100}")
    print(f"{'Optiune':<48} {'XAU':>10} {'CHF':>10} {'CAD':>10} {'XAG':>10} {'TOTAL':>10} {'#Tr':>6}")
    print(f"{'-'*110}")

    best_total = None
    best_opt = None
    for opt_id, opt_name, rr, d1_filt in OPTIONS:
        r = all_results[opt_id]
        net_xau = r.get("XAUUSD.cfd", {}).get("net_profit_after_commission", 0)
        net_chf = r.get("USDCHF.cfd", {}).get("net_profit_after_commission", 0)
        net_cad = r.get("USDCAD.cfd", {}).get("net_profit_after_commission", 0)
        net_xag = r.get("XAGUSD.cfd", {}).get("net_profit_after_commission", 0)
        total = net_xau + net_chf + net_cad + net_xag
        n_trades = sum(r[s]["total_trades"] for s in r)
        if best_total is None or total > best_total:
            best_total = total
            best_opt = (opt_id, opt_name)
        print(f"{opt_id}: {opt_name:<45} ${net_xau:>+8.2f} ${net_chf:>+8.2f} ${net_cad:>+8.2f} ${net_xag:>+8.2f} ${total:>+8.2f} {n_trades:>6}")

    print(f"{'='*100}")
    print(f"\n  CASTIGATOR: {best_opt[0]} → {best_opt[1]} (NET ${best_total:+.2f})\n")

    # Detalii: win rate compare
    print(f"\n  WIN RATES per Optiune:")
    print(f"{'Optiune':<48} {'XAU':>10} {'USDCHF':>10} {'USDCAD':>10}")
    for opt_id, opt_name, rr, d1_filt in OPTIONS:
        r = all_results[opt_id]
        wr_x = r.get("XAUUSD.cfd", {}).get("win_rate", 0)
        wr_c = r.get("USDCHF.cfd", {}).get("win_rate", 0)
        wr_d = r.get("USDCAD.cfd", {}).get("win_rate", 0)
        print(f"{opt_id}: {opt_name:<45} {wr_x:>9.1f}% {wr_c:>9.1f}% {wr_d:>9.1f}%")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
