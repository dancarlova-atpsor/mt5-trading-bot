"""
Backtest stocks US — TrendFollowing pe NVDA, TSLA, AAPL, MSFT, META, AMD etc.
Sesiune: 16:30-23:00 RO (= 13:30-20:00 UTC vara, 14:30-21:00 UTC iarna)
Inchidem fortat la 22:55 RO ca sa evitam gap overnight.

Compara R:R 3.0 vs 1.5 pe fiecare stock.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, time
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing

STOCKS = [
    "NVDA.cfd", "TSLA.cfd", "AAPL.cfd", "MSFT.cfd", "META.cfd",
    "AMD.cfd", "AMZN.cfd", "GOOGL.cfd", "NFLX.cfd", "INTC.cfd",
    "PLTR.cfd", "ORCL.cfd", "BABA.cfd", "UBER.cfd", "IBM.cfd"
]
LOOKBACK_DAYS = 90
INITIAL_BALANCE = 5700.0
COMMISSION_PER_TRADE = 0.30

# TF default params (no per-symbol overrides for stocks initial test)
DEFAULT_PARAMS = {
    "ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "adx_threshold": 25, "adx_period": 14
}


def init_mt5():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")
    info = mt5.account_info()
    print(f"MT5 conectat: cont {info.login if info else '?'}")


def download_h1_session_only(symbol, days):
    """Download H1 si filtreaza doar orele de sesiune NASDAQ (13:30-20:00 UTC)."""
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    # Filter session: NASDAQ open 13:30 UTC, close 20:00 UTC (DST)
    # Use range 13:00-21:00 UTC to be safe
    df = df.between_time("13:00", "21:00")
    return df


def run_stock(symbol, df, rr_ratio):
    """Run backtest on a stock symbol."""
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config["risk_reward_ratio"] = rr_ratio
    strategy = TrendFollowing(params=DEFAULT_PARAMS)

    bt = Backtester(
        strategy=strategy, risk_manager=rm, symbol=symbol,
        timeframe="H1", initial_balance=INITIAL_BALANCE, spread_pips=0.5
    )
    r = bt.run(df, lookback=80)
    commission = r["total_trades"] * COMMISSION_PER_TRADE
    r["commission"] = commission
    r["net_real"] = r["net_profit"] - commission
    return r


def main():
    init_mt5()
    print(f"\n{'='*100}")
    print(f"  STOCKS BACKTEST | {LOOKBACK_DAYS} zile | Balance ${INITIAL_BALANCE}")
    print(f"  Sesiune: 13:00-21:00 UTC (= 16:00-24:00 RO vara)")
    print(f"{'='*100}\n")

    results_30 = {}
    results_15 = {}

    for sym in STOCKS:
        df = download_h1_session_only(sym, LOOKBACK_DAYS)
        if len(df) < 100:
            print(f"  {sym}: SKIP ({len(df)} bare)")
            continue
        print(f"  {sym}: {len(df)} bare H1 (filtered session)", end="")

        r30 = run_stock(sym, df, rr_ratio=3.0)
        results_30[sym] = r30

        r15 = run_stock(sym, df, rr_ratio=1.5)
        results_15[sym] = r15

        print(f" | R:R 3.0 → {r30['total_trades']}tr WR{r30['win_rate']:.0f}% NET${r30['net_real']:+.2f}"
              f" | R:R 1.5 → {r15['total_trades']}tr WR{r15['win_rate']:.0f}% NET${r15['net_real']:+.2f}")

    # Sumar tabel
    print(f"\n{'='*100}")
    print(f"  TABEL FINAL — STOCKS pe {LOOKBACK_DAYS} zile")
    print(f"{'='*100}")
    print(f"{'Symbol':<14} {'#Tr30':>6} {'WR%':>5} {'NET 30':>10} {'#Tr15':>6} {'WR%':>5} {'NET 15':>10} {'Best':>6}")
    print(f"{'-'*100}")
    total_30 = 0
    total_15 = 0
    best_per_sym = {}
    for sym in STOCKS:
        if sym not in results_30:
            continue
        r30 = results_30[sym]
        r15 = results_15[sym]
        net30 = r30["net_real"]
        net15 = r15["net_real"]
        best = "R:R 3.0" if net30 >= net15 else "R:R 1.5"
        best_value = max(net30, net15)
        best_per_sym[sym] = (best, best_value)
        total_30 += net30
        total_15 += net15
        marker = "★" if best_value > 50 else (" " if best_value > 0 else "✗")
        print(f"{sym:<14} {r30['total_trades']:>6} {r30['win_rate']:>4.0f}% ${net30:>+8.2f} "
              f"{r15['total_trades']:>6} {r15['win_rate']:>4.0f}% ${net15:>+8.2f} {best:>7} {marker}")

    print(f"{'-'*100}")
    print(f"{'TOTAL':<14} {'':>6} {'':>5} ${total_30:>+8.2f} {'':>6} {'':>5} ${total_15:>+8.2f}")

    # Best combo (cherry-pick R:R per symbol)
    best_total = sum(v[1] for v in best_per_sym.values())
    print(f"\n  Best per symbol cherry-picked: ${best_total:+.2f}")

    # Top profitabile
    sorted_best = sorted(best_per_sym.items(), key=lambda x: x[1][1], reverse=True)
    print(f"\n  TOP 5 PROFITABILE:")
    for sym, (rr, val) in sorted_best[:5]:
        print(f"    {sym}: ${val:+.2f} ({rr})")

    print(f"\n  WORST 5:")
    for sym, (rr, val) in sorted_best[-5:]:
        print(f"    {sym}: ${val:+.2f} ({rr})")

    print(f"\n  AVG/zi (best combo): ${best_total/LOOKBACK_DAYS:+.2f}")
    print(f"  ROI 90d: {best_total/INITIAL_BALANCE*100:+.2f}%")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
