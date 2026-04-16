"""
Backtesting serios — compara parametri vechi vs noi pe 3 luni date istorice.

Usage (din root mt5-trading-bot, cu MT5 terminal deschis):
    .venv/Scripts/python.exe backtesting/run_backtest.py

Necesita: MT5 terminal deschis si logat (pt descarcarea datelor).
"""

import sys
import os
import time
from datetime import datetime, timedelta
from itertools import product

# Adauga root dir la path ca sa importam modulele
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout
from strategies.macd_divergence import MACDDivergence


# ============================================
# CONFIG
# ============================================

SYMBOLS = ["EURUSD.cfd", "AUDUSD.cfd", "GBPUSD.cfd", "EURGBP.cfd"]
TIMEFRAME = "H1"
LOOKBACK_DAYS = 90  # 3 luni
INITIAL_BALANCE = 100000.0
SPREAD_PIPS = 1.5  # Spread mediu pt majors pe EarnBroker
COMMISSION_PER_LOT_RT = 7.0  # $7 round-trip per lot (estimare)

MT5_TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def init_mt5():
    """Initializeaza MT5 — terminal trebuie sa fie deschis si logat."""
    terminal_path = r"C:\Program Files\Earn Broker MT5 Terminal\terminal64.exe"

    if not mt5.initialize():
        if os.path.exists(terminal_path):
            mt5.initialize(path=terminal_path)

    if not mt5.initialize():
        print("ERROR: Nu pot conecta la MT5. Deschide terminalul si logheaza-te.")
        sys.exit(1)

    info = mt5.account_info()
    if info:
        print(f"MT5 conectat: cont {info.login} | Balance: {info.balance}")
    else:
        print("MT5 conectat (fara account info)")


def download_data(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Descarca date istorice din MT5."""
    tf = MT5_TIMEFRAMES.get(timeframe, mt5.TIMEFRAME_H1)
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days)

    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        print(f"  WARNING: Nu pot descarca date pentru {symbol} {timeframe}")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={
        "open": "open", "high": "high", "low": "low",
        "close": "close", "tick_volume": "volume",
    }, inplace=True)

    return df


def run_single_backtest(
    symbol: str,
    data: pd.DataFrame,
    strategy_class,
    strategy_params: dict,
    rr_ratio: float,
    balance: float = INITIAL_BALANCE,
) -> dict:
    """Ruleaza un singur backtest cu parametri specifici."""
    # RiskManager se incarca din config/risk_management.yaml (default)
    # Override doar RR ratio pt comparatie
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config["risk_reward_ratio"] = rr_ratio
    strategy = strategy_class(params=strategy_params)

    bt = Backtester(
        strategy=strategy,
        risk_manager=rm,
        symbol=symbol,
        timeframe=TIMEFRAME,
        initial_balance=balance,
        spread_pips=SPREAD_PIPS,
    )

    results = bt.run(data, lookback=60)

    # Adjust for commissions
    total_commission = results["total_trades"] * COMMISSION_PER_LOT_RT
    results["total_commission"] = round(total_commission, 2)
    results["net_profit_after_commission"] = round(results["net_profit"] - total_commission, 2)
    results["rr_ratio"] = rr_ratio

    return results


def print_results_table(results_list: list[dict], title: str):
    """Printeaza tabel formatat cu rezultate."""
    print(f"\n{'=' * 100}")
    print(f"  {title}")
    print(f"{'=' * 100}")
    print(f"{'Symbol':<14} {'RR':>4} {'Trades':>7} {'WR%':>6} {'PF':>6} "
          f"{'Gross P&L':>10} {'Commis.':>8} {'NET P&L':>10} {'MaxDD%':>7} {'Sharpe':>7}")
    print(f"{'-' * 100}")

    for r in results_list:
        symbol = r.get("symbol", "?")
        rr = r.get("rr_ratio", 0)
        trades = r.get("total_trades", 0)
        wr = r.get("win_rate", 0)
        pf = r.get("profit_factor", 0)
        gross = r.get("net_profit", 0)
        comm = r.get("total_commission", 0)
        net = r.get("net_profit_after_commission", 0)
        dd = r.get("max_drawdown_pct", 0)
        sharpe = r.get("sharpe_ratio", 0)

        # Color coding (ANSI)
        net_color = "\033[92m" if net > 0 else "\033[91m"
        reset = "\033[0m"

        print(f"{symbol:<14} {rr:>4.1f} {trades:>7} {wr:>5.1f}% {pf:>5.2f} "
              f"${gross:>+9.2f} ${comm:>7.2f} {net_color}${net:>+9.2f}{reset} {dd:>6.2f}% {sharpe:>6.2f}")

    print(f"{'=' * 100}")

    # Totaluri
    total_net = sum(r.get("net_profit_after_commission", 0) for r in results_list)
    total_trades = sum(r.get("total_trades", 0) for r in results_list)
    total_comm = sum(r.get("total_commission", 0) for r in results_list)
    net_color = "\033[92m" if total_net > 0 else "\033[91m"
    reset = "\033[0m"
    print(f"{'TOTAL':<14} {'':>4} {total_trades:>7} {'':>6} {'':>6} "
          f"{'':>10} ${total_comm:>7.2f} {net_color}${total_net:>+9.2f}{reset}")


def run_comparison():
    """Compara parametri VECHI (RR=2.0) vs NOU (RR=3.0) pe toate simbolurile."""
    print("\n" + "=" * 60)
    print("  BACKTESTING: VECHI (RR=2.0) vs NOU (RR=3.0)")
    print(f"  Perioada: {LOOKBACK_DAYS} zile | TF: {TIMEFRAME} | Balance: ${INITIAL_BALANCE:,.0f}")
    print("=" * 60)

    # Descarca date
    data_cache = {}
    for sym in SYMBOLS:
        print(f"  Descarc {sym} {TIMEFRAME} ({LOOKBACK_DAYS} zile)...", end=" ")
        df = download_data(sym, TIMEFRAME, LOOKBACK_DAYS)
        if len(df) > 0:
            data_cache[sym] = df
            print(f"OK ({len(df)} bare)")
        else:
            print("SKIP (no data)")

    if not data_cache:
        print("ERROR: Nu am date. Verifica MT5.")
        return

    # Parametri TrendFollowing default
    tf_params = {
        "ema_fast": 20, "ema_slow": 50,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "adx_threshold": 25, "adx_period": 14,
    }

    # Test VECHI (RR=2.0)
    results_old = []
    for sym, df in data_cache.items():
        r = run_single_backtest(sym, df, TrendFollowing, tf_params, rr_ratio=2.0)
        r["symbol"] = sym
        results_old.append(r)

    # Test NOU (RR=3.0)
    results_new = []
    for sym, df in data_cache.items():
        r = run_single_backtest(sym, df, TrendFollowing, tf_params, rr_ratio=3.0)
        r["symbol"] = sym
        results_new.append(r)

    # Test NOU+ (RR=3.5)
    results_new35 = []
    for sym, df in data_cache.items():
        r = run_single_backtest(sym, df, TrendFollowing, tf_params, rr_ratio=3.5)
        r["symbol"] = sym
        results_new35.append(r)

    print_results_table(results_old, "PARAMETRI VECHI — Risk:Reward = 2.0")
    print_results_table(results_new, "PARAMETRI NOI — Risk:Reward = 3.0")
    print_results_table(results_new35, "PARAMETRI NOI+ — Risk:Reward = 3.5")

    # Recomandare
    total_old = sum(r["net_profit_after_commission"] for r in results_old)
    total_new = sum(r["net_profit_after_commission"] for r in results_new)
    total_new35 = sum(r["net_profit_after_commission"] for r in results_new35)

    print(f"\n{'=' * 60}")
    print(f"  RECOMANDARE")
    print(f"{'=' * 60}")
    best = max([(total_old, "RR=2.0 (vechi)"), (total_new, "RR=3.0"), (total_new35, "RR=3.5")], key=lambda x: x[0])
    print(f"  Cel mai bun: {best[1]} cu net P&L = ${best[0]:+,.2f}")
    print(f"  RR=2.0: ${total_old:+,.2f} | RR=3.0: ${total_new:+,.2f} | RR=3.5: ${total_new35:+,.2f}")


def run_grid_search():
    """Grid search pe parametri cheie: RR ratio × EMA × ADX threshold."""
    print("\n" + "=" * 60)
    print("  GRID SEARCH — TrendFollowing pe AUDUSD")
    print("=" * 60)

    # Doar AUDUSD (cel mai activ simbol)
    symbol = "AUDUSD.cfd"
    df = download_data(symbol, TIMEFRAME, LOOKBACK_DAYS)
    if len(df) == 0:
        print("ERROR: Nu am date AUDUSD")
        return

    print(f"  Date: {len(df)} bare H1 ({LOOKBACK_DAYS} zile)")

    # Grid params
    rr_ratios = [2.0, 2.5, 3.0, 3.5]
    ema_pairs = [(10, 30), (15, 40), (20, 50), (25, 60)]
    adx_thresholds = [20, 25, 30]

    total = len(rr_ratios) * len(ema_pairs) * len(adx_thresholds)
    print(f"  Combinatii: {total}")

    all_results = []
    count = 0

    for rr, (ema_f, ema_s), adx_th in product(rr_ratios, ema_pairs, adx_thresholds):
        count += 1
        params = {
            "ema_fast": ema_f, "ema_slow": ema_s,
            "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
            "adx_threshold": adx_th, "adx_period": 14,
        }
        r = run_single_backtest(symbol, df, TrendFollowing, params, rr_ratio=rr)
        r["symbol"] = symbol
        r["ema"] = f"{ema_f}/{ema_s}"
        r["adx_th"] = adx_th
        all_results.append(r)

        if count % 10 == 0:
            print(f"  ... {count}/{total} done")

    # Sort by net profit after commission
    all_results.sort(key=lambda x: x["net_profit_after_commission"], reverse=True)

    # Print top 15
    print(f"\n{'=' * 120}")
    print(f"  TOP 15 COMBINATII — AUDUSD TrendFollowing (din {total} testate)")
    print(f"{'=' * 120}")
    print(f"{'#':>3} {'EMA':>8} {'ADX':>5} {'RR':>4} {'Trades':>7} {'WR%':>6} {'PF':>6} "
          f"{'Gross':>10} {'Comm':>8} {'NET':>10} {'DD%':>6} {'Sharpe':>7}")
    print(f"{'-' * 120}")

    for i, r in enumerate(all_results[:15]):
        net = r["net_profit_after_commission"]
        color = "\033[92m" if net > 0 else "\033[91m"
        reset = "\033[0m"
        print(f"{i+1:>3} {r['ema']:>8} {r['adx_th']:>5} {r['rr_ratio']:>4.1f} "
              f"{r['total_trades']:>7} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['net_profit']:>+9.2f} ${r['total_commission']:>7.2f} "
              f"{color}${net:>+9.2f}{reset} {r['max_drawdown_pct']:>5.2f}% {r['sharpe_ratio']:>6.2f}")

    # Print worst 5 too
    print(f"\n  WORST 5:")
    for i, r in enumerate(all_results[-5:]):
        net = r["net_profit_after_commission"]
        print(f"  {r['ema']:>8} ADX{r['adx_th']:>3} RR{r['rr_ratio']:.1f} "
              f"→ {r['total_trades']} trades, WR {r['win_rate']:.1f}%, "
              f"NET ${net:+.2f}")

    print(f"\n  BEST: EMA {all_results[0]['ema']}, ADX {all_results[0]['adx_th']}, "
          f"RR {all_results[0]['rr_ratio']} → NET ${all_results[0]['net_profit_after_commission']:+.2f}")


# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    init_mt5()

    print("\nCe vrei sa rulezi?")
    print("  1 = Comparatie VECHI vs NOU (RR 2.0 vs 3.0 vs 3.5)")
    print("  2 = Grid Search pe AUDUSD (48 combinatii)")
    print("  3 = Ambele")

    choice = input("\nAlege (1/2/3): ").strip()

    if choice in ("1", "3"):
        run_comparison()

    if choice in ("2", "3"):
        run_grid_search()

    mt5.shutdown()
    print("\nDone.")
