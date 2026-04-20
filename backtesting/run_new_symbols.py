"""
Backtest rapid pe simboluri noi — evaluare pentru inlocuirea XAGUSD.

Testeaza: XAUUSD, USDCAD, NZDUSD, USDCHF, EURJPY, GBPJPY, AUDJPY, US30
Parametri: EMA 20/50 ADX 25 RR 3.0 (setup-ul optim confirmat de backtest)
"""

import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout
from strategies.macd_divergence import MACDDivergence


CANDIDATE_SYMBOLS = [
    # Current (benchmark)
    "EURUSD.cfd", "AUDUSD.cfd", "GBPUSD.cfd", "EURGBP.cfd",
    # Noi de testat
    "XAUUSD.cfd",   # aur — SPREAD 0, com $0.10/lot — inlocuieste XAGUSD
    "USDCAD.cfd",   # stabil
    "NZDUSD.cfd",
    "USDCHF.cfd",
    "EURJPY.cfd",   # yen cross
    "GBPJPY.cfd",
    "AUDJPY.cfd",
    "US30.cfd",     # indice — spread 0 dar com $10
]
LOOKBACK_DAYS = 90
INITIAL_BALANCE = 100000.0
SPREAD_PIPS = 1.5  # default; pt unele simboluri cost-ul e diferit

# Comisioane estimate round-trip per tranzactie (bazat pe vol de 0.1 lot cu commision tick val)
# Valori extrase din analiza live cu info.trade_tick_value
COMMISSION_PER_TRADE = {
    "EURUSD.cfd": 2.0,  "AUDUSD.cfd": 2.0,  "GBPUSD.cfd": 2.0,  "EURGBP.cfd": 3.0,
    "XAUUSD.cfd": 0.5,  # MINIMAL!
    "USDCAD.cfd": 1.5,  "NZDUSD.cfd": 2.0,  "USDCHF.cfd": 2.5,
    "EURJPY.cfd": 1.5,  "GBPJPY.cfd": 1.5,  "AUDJPY.cfd": 1.5,
    "US30.cfd": 20.0,   # comision mare
}


def download(symbol, days):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates is None or len(rates) < 100:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df


def backtest_sym(sym, df, strategy_class, params, rr=3.0):
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config["risk_reward_ratio"] = rr
    strategy = strategy_class(params=params)
    bt = Backtester(
        strategy=strategy, risk_manager=rm, symbol=sym,
        timeframe="H1", initial_balance=INITIAL_BALANCE, spread_pips=SPREAD_PIPS,
    )
    r = bt.run(df, lookback=60)
    comm = r["total_trades"] * COMMISSION_PER_TRADE.get(sym, 2.0)
    r["commission"] = comm
    r["net_after_comm"] = r["net_profit"] - comm
    return r


def main():
    mt5.initialize()
    info = mt5.account_info()
    print(f"MT5 connected: {info.login}")

    # Select toate simbolurile
    for s in CANDIDATE_SYMBOLS:
        mt5.symbol_select(s, True)

    # Parametri TF optim (confirmat backtest anterior)
    tf_params = {"ema_fast": 20, "ema_slow": 50, "macd_fast": 12, "macd_slow": 26,
                 "macd_signal": 9, "adx_threshold": 25, "adx_period": 14}
    br_params = {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 2.0}
    mc_params = {"macd_fast": 12, "macd_slow": 26, "macd_signal": 9, "rsi_period": 14,
                 "rsi_oversold": 35, "rsi_overbought": 65, "divergence_lookback": 25}

    # Descarca toate datele
    print(f"\nDownloading {LOOKBACK_DAYS} zile pentru {len(CANDIDATE_SYMBOLS)} simboluri...")
    data = {}
    for s in CANDIDATE_SYMBOLS:
        df = download(s, LOOKBACK_DAYS)
        if df is not None:
            data[s] = df
            print(f"  {s}: {len(df)} bars")
        else:
            print(f"  {s}: NO DATA")

    # Ruleaza TF + Breakout + MACD pe toate
    all_results = []
    print(f"\nRulare backtest TF / Breakout / MACD pe fiecare simbol (RR=3.0)...")
    for sym, df in data.items():
        r_tf = backtest_sym(sym, df, TrendFollowing, tf_params)
        r_br = backtest_sym(sym, df, Breakout, br_params)
        r_mc = backtest_sym(sym, df, MACDDivergence, mc_params)
        all_results.append((sym, "TrendFollow", r_tf))
        all_results.append((sym, "Breakout", r_br))
        all_results.append((sym, "MACDDiv", r_mc))

    # Sort dupa net_after_comm
    all_results.sort(key=lambda x: -x[2]["net_after_comm"])

    print(f"\n{'='*95}")
    print(f"  TOP combinatii simbol + strategie (sortat dupa NET P&L dupa comisioane)")
    print(f"{'='*95}")
    print(f"{'#':3} {'Simbol':14} {'Strategie':13} {'Trades':7} {'WR%':6} {'PF':6} {'Gross':10} {'Comm':8} {'NET':10} {'DD%':6}")
    print('-' * 95)
    for i, (sym, strat, r) in enumerate(all_results[:30], 1):
        net = r["net_after_comm"]
        color = "\033[92m" if net > 0 else "\033[91m"
        reset = "\033[0m"
        print(f"{i:3} {sym:14} {strat:13} {r['total_trades']:7} {r['win_rate']:5.1f}% {r['profit_factor']:5.2f} "
              f"{r['net_profit']:+9.2f} {r['commission']:8.2f} {color}{net:+9.2f}{reset} {r['max_drawdown_pct']:5.2f}%")

    print(f"\n{'='*95}")
    print(f"  TOP 10 CASTIGATOARE")
    print(f"{'='*95}")
    winners = [x for x in all_results if x[2]["net_after_comm"] > 0][:10]
    for i, (sym, strat, r) in enumerate(winners, 1):
        print(f"  {i}. {sym:12} + {strat:12} → NET ${r['net_after_comm']:+.2f} | {r['total_trades']} trades | WR {r['win_rate']:.0f}% | PF {r['profit_factor']:.2f}")

    print(f"\n{'='*95}")
    print(f"  RECOMANDARE FINAL — top 5 simboluri diversificate")
    print(f"{'='*95}")
    # Cele mai bune simboluri, cate 1 per simbol (cel mai bun strategie al sau)
    best_per_sym = {}
    for sym, strat, r in all_results:
        if sym not in best_per_sym or r["net_after_comm"] > best_per_sym[sym][1]["net_after_comm"]:
            best_per_sym[sym] = (strat, r)
    sorted_best = sorted(best_per_sym.items(), key=lambda x: -x[1][1]["net_after_comm"])
    for i, (sym, (strat, r)) in enumerate(sorted_best[:5], 1):
        print(f"  {i}. {sym:14} - {strat:13} - NET ${r['net_after_comm']:+.2f} ({r['total_trades']} trades, WR {r['win_rate']:.0f}%)")

    mt5.shutdown()


if __name__ == "__main__":
    main()
