"""
Grid search pentru TrendFollowing pe toate 6 simboluri.
Testeaza combinatii de EMA fast/slow + ADX threshold.
Scop: gasim parametri optimi per simbol.
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd
import itertools

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing


SYMBOLS = ['EURUSD.cfd', 'AUDUSD.cfd', 'EURGBP.cfd', 'XAUUSD.cfd', 'USDCHF.cfd', 'USDCAD.cfd']
LOOKBACK_DAYS = 90

# Parametri TrendFollow de testat
EMA_FAST_OPTS = [10, 15, 20]
EMA_SLOW_OPTS = [30, 50, 70]
ADX_OPTS = [18, 22, 25, 30]

# Cost real
SWAP_COST = {'EURUSD.cfd': 0.5, 'AUDUSD.cfd': 0.4, 'EURGBP.cfd': 0.1,
             'XAUUSD.cfd': 3.0, 'USDCHF.cfd': 2.0, 'USDCAD.cfd': 1.5}
COMMISSION = 1.5


def download(symbol, days):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates is None or len(rates) < 100:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.rename(columns={'tick_volume': 'volume'}, inplace=True)
    return df


def run_bt(symbol, df, params):
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config['risk_reward_ratio'] = 3.0
    strategy = TrendFollowing(params=params)
    bt = Backtester(strategy=strategy, risk_manager=rm, symbol=symbol,
                    timeframe='H1', initial_balance=100000.0, spread_pips=1.5)
    r = bt.run(df, lookback=60)
    swap = r['total_trades'] * SWAP_COST.get(symbol, 1.0)
    comm = r['total_trades'] * COMMISSION
    r['net_real'] = r['net_profit'] - swap - comm
    return r


def main():
    mt5.initialize()
    print(f'MT5: {mt5.account_info().login}')

    # Download data pt fiecare simbol
    data = {}
    for s in SYMBOLS:
        df = download(s, LOOKBACK_DAYS)
        if df is not None:
            data[s] = df
            print(f'  {s}: {len(df)} bars')

    # Genereaza combinatii valid (ef < es)
    combos = [(ef, es, adx) for ef, es, adx in itertools.product(EMA_FAST_OPTS, EMA_SLOW_OPTS, ADX_OPTS) if ef < es]
    print(f'\nTest {len(combos)} combinatii × {len(data)} simboluri = {len(combos)*len(data)} backtests...')

    # Rezultate per simbol
    best_per_sym = {}
    for sym, df in data.items():
        print(f'\n{"="*85}')
        print(f'  {sym}')
        print(f'{"="*85}')
        print(f'{"EMA":12} {"ADX":5} {"Trades":7} {"WR%":7} {"PF":6} {"NET":10}')
        print('-' * 85)
        results = []
        for ef, es, adx in combos:
            params = {'ema_fast': ef, 'ema_slow': es, 'macd_fast': 12, 'macd_slow': 26,
                      'macd_signal': 9, 'adx_threshold': adx, 'adx_period': 14}
            r = run_bt(sym, df, params)
            r['ef'], r['es'], r['adx'] = ef, es, adx
            results.append(r)

        # Sort by NET
        results.sort(key=lambda x: -x['net_real'])
        for r in results[:5]:
            print(f'  {r["ef"]}/{r["es"]:<9} {r["adx"]:<5} {r["total_trades"]:>7} {r["win_rate"]:>6.1f}% {r["profit_factor"]:>5.2f} {r["net_real"]:>+10.2f}')
        best = results[0]
        best_per_sym[sym] = best
        print(f'\n  CURRENT (20/50 ADX25): ', end='')
        cur = [r for r in results if r['ef']==20 and r['es']==50 and r['adx']==25]
        if cur:
            print(f'NET {cur[0]["net_real"]:+.2f}')
        else:
            print('n/a')
        print(f'  BEST: EMA {best["ef"]}/{best["es"]} ADX{best["adx"]} → NET {best["net_real"]:+.2f}')

    # Sumar total
    print(f'\n\n{"="*85}')
    print(f'  BEST PARAMETRII PER SIMBOL')
    print(f'{"="*85}')
    total_current = 0
    total_best = 0
    for sym, best in best_per_sym.items():
        df = data[sym]
        cur_params = {'ema_fast': 20, 'ema_slow': 50, 'macd_fast': 12, 'macd_slow': 26,
                      'macd_signal': 9, 'adx_threshold': 25, 'adx_period': 14}
        cur_r = run_bt(sym, df, cur_params)
        total_current += cur_r['net_real']
        total_best += best['net_real']
        print(f'  {sym:14} | Current (20/50/25): {cur_r["net_real"]:>+10.2f} | Best ({best["ef"]}/{best["es"]}/{best["adx"]}): {best["net_real"]:>+10.2f} | Castig: {best["net_real"]-cur_r["net_real"]:>+10.2f}')

    print(f'\nTOTAL TF profit (90 zile):')
    print(f'  Cu parametri CURENTI (20/50/25): {total_current:+.2f}')
    print(f'  Cu parametri OPTIMI per simbol:  {total_best:+.2f}')
    print(f'  Imbunatatire:                    {total_best-total_current:+.2f}')

    mt5.shutdown()

if __name__ == '__main__':
    main()
