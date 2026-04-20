"""Backtest NASDAQ stocks cu EMA 20/50 ADX 25 RR 3.0 pe H1 + H4."""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout


STOCKS = ["CPRT.cfd", "QCOM.cfd", "CSCO.cfd", "FITB.cfd", "TROW.cfd"]
LOOKBACK_DAYS = 90
INITIAL_BALANCE = 100000.0

# Stock spread (in cents pt H1 — foarte mic vs forex)
# tick_value stocks = $1/lot, spread 1-7 cents = $0.01-0.07
# In "pips" nostri unde 1 pip = 1 cent pt stocks, spread = 1-7 pips
STOCK_SPREAD_PIPS = 3.0  # media pentru simulator
STOCK_COMMISSION_PER_TRADE = 0.5  # minimal pt stocks


def download(symbol, days, tf=mt5.TIMEFRAME_H1):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) < 100:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df


def backtest(sym, df, strategy_class, params, rr=3.0, tf_name="H1"):
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config["risk_reward_ratio"] = rr
    strategy = strategy_class(params=params)
    bt = Backtester(strategy=strategy, risk_manager=rm, symbol=sym,
                    timeframe=tf_name, initial_balance=INITIAL_BALANCE,
                    spread_pips=STOCK_SPREAD_PIPS)
    r = bt.run(df, lookback=60)
    r["commission"] = r["total_trades"] * STOCK_COMMISSION_PER_TRADE
    r["net_after_comm"] = r["net_profit"] - r["commission"]
    return r


def main():
    mt5.initialize()
    print(f"MT5: {mt5.account_info().login}")
    for s in STOCKS:
        mt5.symbol_select(s, True)

    tf_params = {"ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26,
                 "macd_signal": 9, "adx_threshold": 25, "adx_period": 14}
    br_params = {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 2.0}

    for tf_label, tf_mt5 in [("H1", mt5.TIMEFRAME_H1), ("H4", mt5.TIMEFRAME_H4)]:
        print(f"\n{'='*90}")
        print(f"  BACKTEST STOCKS — Timeframe {tf_label}, RR=3.0, 90 zile")
        print(f"{'='*90}")
        print(f"{'Simbol':10} {'Strat':12} {'Trades':7} {'WR%':6} {'PF':6} {'Gross':10} {'Comm':7} {'NET':10} {'DD%':6}")
        print('-'*90)
        for s in STOCKS:
            df = download(s, LOOKBACK_DAYS, tf_mt5)
            if df is None:
                print(f"{s}: no data for {tf_label}")
                continue
            r_tf = backtest(s, df, TrendFollowing, tf_params, tf_name=tf_label)
            r_br = backtest(s, df, Breakout, br_params, tf_name=tf_label)
            for strat_name, r in [("TrendFollow", r_tf), ("Breakout", r_br)]:
                net = r["net_after_comm"]
                color = "\033[92m" if net > 0 else "\033[91m"
                reset = "\033[0m"
                print(f"{s:10} {strat_name:12} {r['total_trades']:7} {r['win_rate']:5.1f}% {r['profit_factor']:5.2f} "
                      f"{r['net_profit']:+9.2f} {r['commission']:6.2f} {color}{net:+9.2f}{reset} {r['max_drawdown_pct']:5.2f}%")

    mt5.shutdown()

if __name__ == "__main__":
    main()
