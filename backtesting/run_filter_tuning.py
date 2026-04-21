"""
Tuning entry_filter: testeaza praguri S/R diferite (0.1%, 0.3%, 0.5%, 0.8%)
SUB Breakout only (pentru ca backtest-ul anterior a aratat filter strica TF).

Scop: gasim pragul optim pt Breakout pe toate simbolurile.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.run_with_entry_filter import run_single, SYMBOLS, SWAP_COST_PER_TRADE, COMMISSION_PER_TRADE
from strategies.breakout import Breakout
import MetaTrader5 as mt5


def main():
    mt5.initialize()
    print(f'MT5: {mt5.account_info().login}')

    br_params = {'lookback_period': 50, 'atr_period': 14, 'min_atr_ratio': 0.8, 'min_vol_ratio': 2.0}

    thresholds = [0.0, 0.1, 0.3, 0.5, 0.8]  # % distanta la S/R
    results_by_threshold = {t: {} for t in thresholds}

    print(f"\n{'='*110}")
    print(f"  TUNING ENTRY FILTER pe BREAKOUT — praguri S/R diferite")
    print(f"{'='*110}")

    # Fara filter (baseline)
    print("\nBaseline (FARA filter):")
    print(f"{'Simbol':12} {'Trades':7} {'WR%':7} {'NET':10}")
    baseline_total = 0
    for sym in SYMBOLS:
        r = run_single(sym, Breakout, br_params, with_filter=False)
        if r:
            print(f"  {sym:12} {r['total_trades']:>7} {r['win_rate']:>6.1f}% {r['net_real']:>+10.2f}")
            baseline_total += r['net_real']
    print(f"  {'TOTAL':12} {'':>7} {'':>7} {baseline_total:>+10.2f}")

    # Cu filter la diferite praguri
    for threshold in thresholds:
        print(f"\n--- SR threshold = {threshold}% ---")
        print(f"{'Simbol':12} {'Trades':7} {'Block':6} {'WR%':7} {'NET':10}")
        total_net = 0
        total_trades = 0
        total_blocked = 0
        for sym in SYMBOLS:
            r = run_single(sym, Breakout, br_params, with_filter=True, sr_threshold=threshold)
            if r:
                print(f"  {sym:12} {r['total_trades']:>7} {r['blocked']:>6} {r['win_rate']:>6.1f}% {r['net_real']:>+10.2f}")
                total_net += r['net_real']
                total_trades += r['total_trades']
                total_blocked += r['blocked']
        results_by_threshold[threshold] = {
            'net': total_net, 'trades': total_trades, 'blocked': total_blocked
        }
        print(f"  {'TOTAL':12} {total_trades:>7} {total_blocked:>6} {'':>7} {total_net:>+10.2f}")

    # Sumar
    print(f"\n{'='*80}")
    print(f"  SUMAR — cel mai bun prag S/R pentru Breakout")
    print(f"{'='*80}")
    print(f"{'Prag S/R':10} {'Trades':8} {'Blocked':8} {'NET':>12}")
    print('-' * 50)
    print(f"{'FARA':10} {'':<8} {'':<8} {baseline_total:>+12.2f}  (baseline)")
    for t, r in results_by_threshold.items():
        marker = '  <-- best' if r['net'] == max(x['net'] for x in results_by_threshold.values()) else ''
        print(f"{t:<10.1f} {r['trades']:<8} {r['blocked']:<8} {r['net']:>+12.2f}{marker}")

    mt5.shutdown()

if __name__ == '__main__':
    main()
