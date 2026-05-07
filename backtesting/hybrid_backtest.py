"""
Backtest HIBRID — configuratie per simbol bazata pe rezultate A-E:
  - XAU: R:R 3.0, fara D1 filter (+$813 in test A)
  - USDCHF: R:R 3.0, fara D1 filter (+$91 in test A)
  - USDCAD: R:R 1.5, CU D1 filter (+$151 in test D)
  - XAGUSD: R:R 1.5, fara D1 filter (+$6636 in test B)

Cu comisioane si estimat swap.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.base_strategy import Signal

LOOKBACK_DAYS = 90
INITIAL_BALANCE = 5700.0
SPREAD_PIPS = 1.5
COMMISSION_PER_TRADE = 0.30  # estimat $0.30 per trade per 0.05 lot

# Configuratie HIBRID per simbol
HYBRID_CONFIG = {
    "XAUUSD.cfd": {
        "params": {"ema_fast": 10, "ema_slow": 70, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 30, "adx_period": 14},
        "rr": 3.0, "d1_filter": False, "swap_per_night": -0.30
    },
    "USDCHF.cfd": {
        "params": {"ema_fast": 10, "ema_slow": 30, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 25, "adx_period": 14},
        "rr": 3.0, "d1_filter": False, "swap_per_night": -0.05
    },
    "USDCAD.cfd": {
        "params": {"ema_fast": 15, "ema_slow": 70, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 18, "adx_period": 14},
        "rr": 1.5, "d1_filter": True, "swap_per_night": -0.10
    },
    "XAGUSD.cfd": {
        "params": {"ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "adx_threshold": 25, "adx_period": 14},
        "rr": 1.5, "d1_filter": False, "swap_per_night": -0.50
    },
}


class TrendFollowingD1Filtered(TrendFollowing):
    def __init__(self, params=None, d1_data=None):
        super().__init__(params=params)
        self.d1_data = d1_data

    def analyze(self, data):
        signal = super().analyze(data)
        if signal == Signal.HOLD or self.d1_data is None:
            return signal
        current_time = data.index[-1]
        d1_before = self.d1_data[self.d1_data.index <= current_time]
        if len(d1_before) < 50:
            return signal
        last_d1 = d1_before.iloc[-1]
        d1_up = last_d1["ema20"] > last_d1["ema50"]
        if signal == Signal.BUY and not d1_up:
            return Signal.HOLD
        if signal == Signal.SELL and d1_up:
            return Signal.HOLD
        return signal


def init_mt5():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")
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
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days + 100)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_D1, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df["ema20"] = ta.ema(df["close"], 20)
    df["ema50"] = ta.ema(df["close"], 50)
    return df


def estimate_swap_cost(trades, swap_per_night):
    """Estimeaza swap pe baza duratei trade-urilor (in nopti)."""
    total = 0
    for t in trades:
        # exit_time - entry_time in days (rounded up)
        if hasattr(t["entry_time"], "to_pydatetime"):
            entry = t["entry_time"]
            exit = t["exit_time"]
            duration = (exit - entry).total_seconds() / 86400
            nights = max(0, int(duration))
            total += nights * abs(swap_per_night) * t["volume"] * 10  # rough: per 0.1 lot
    return total


def run_hybrid():
    init_mt5()
    print(f"\n{'='*100}")
    print(f"  BACKTEST HIBRID — {LOOKBACK_DAYS} zile | Balance ${INITIAL_BALANCE}")
    print(f"  Configuratie per simbol (din rezultate A-E):")
    for sym, cfg in HYBRID_CONFIG.items():
        print(f"    {sym}: R:R {cfg['rr']} | D1 filter: {cfg['d1_filter']}")
    print(f"{'='*100}\n")

    results = {}
    for symbol, cfg in HYBRID_CONFIG.items():
        print(f"  {symbol}: Descarc date...", end=" ")
        h1 = download_h1(symbol, LOOKBACK_DAYS)
        d1 = download_d1_with_emas(symbol, LOOKBACK_DAYS) if cfg["d1_filter"] else None
        if len(h1) == 0:
            print("SKIP")
            continue
        print(f"OK ({len(h1)} H1)")

        rm = RiskManager()
        rm.take_profit_config = dict(rm.take_profit_config)
        rm.take_profit_config["risk_reward_ratio"] = cfg["rr"]

        if cfg["d1_filter"]:
            strategy = TrendFollowingD1Filtered(params=cfg["params"], d1_data=d1)
        else:
            strategy = TrendFollowing(params=cfg["params"])

        bt = Backtester(strategy=strategy, risk_manager=rm, symbol=symbol,
                        timeframe="H1", initial_balance=INITIAL_BALANCE, spread_pips=SPREAD_PIPS)
        r = bt.run(h1, lookback=80)

        commission = r["total_trades"] * COMMISSION_PER_TRADE
        swap = estimate_swap_cost(bt.trades, cfg["swap_per_night"])
        gross = r["net_profit"]
        net_real = gross - commission - swap

        results[symbol] = {
            "trades": r["total_trades"], "wr": r["win_rate"],
            "gross": gross, "commission": commission, "swap": swap, "net_real": net_real,
            "max_dd": r.get("max_drawdown_pct", 0), "pf": r.get("profit_factor", 0)
        }
        print(f"    trades={r['total_trades']}, WR={r['win_rate']:.1f}%, "
              f"GROSS=${gross:+.2f}, COMM=${commission:.2f}, SWAP=${swap:.2f}, "
              f"NET=${net_real:+.2f}, DD={r.get('max_drawdown_pct',0):.1f}%")

    # Sumar
    print(f"\n{'='*100}")
    print(f"  REZULTATE HIBRID")
    print(f"{'='*100}")
    print(f"{'Symbol':<14} {'#Tr':>5} {'WR%':>6} {'PF':>5} {'GROSS':>10} {'COMM':>8} {'SWAP':>8} {'NET':>10} {'DD%':>6}")
    print(f"{'-'*100}")
    total_net = 0
    total_trades = 0
    for sym, r in results.items():
        print(f"{sym:<14} {r['trades']:>5} {r['wr']:>5.1f}% {r['pf']:>4.2f} ${r['gross']:>+8.2f} ${r['commission']:>6.2f} ${r['swap']:>6.2f} ${r['net_real']:>+8.2f} {r['max_dd']:>5.1f}%")
        total_net += r["net_real"]
        total_trades += r["trades"]
    print(f"{'-'*100}")
    print(f"{'TOTAL':<14} {total_trades:>5} {'':>6} {'':>5} {'':>10} {'':>8} {'':>8} ${total_net:>+8.2f}")
    print(f"\n  AVG / zi: ${total_net/LOOKBACK_DAYS:+.2f}")
    print(f"  AVG / luna: ${total_net/3:+.2f}")
    print(f"  ROI 90 zile: {total_net/INITIAL_BALANCE*100:+.2f}%")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    run_hybrid()
