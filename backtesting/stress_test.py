"""
Stress test: valideaza ca params per-symbol nu sunt overfit pe 90 zile.
Testam pe 30, 60, 90, 180 zile sa vedem stabilitatea.
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing


SYMBOL_PARAMS = {
    'EURUSD.cfd': {'ema_fast': 20, 'ema_slow': 50, 'adx_threshold': 25},
    'AUDUSD.cfd': {'ema_fast': 15, 'ema_slow': 70, 'adx_threshold': 25},
    'EURGBP.cfd': {'ema_fast': 15, 'ema_slow': 70, 'adx_threshold': 30},
    'XAUUSD.cfd': {'ema_fast': 10, 'ema_slow': 70, 'adx_threshold': 30},
    'USDCHF.cfd': {'ema_fast': 10, 'ema_slow': 30, 'adx_threshold': 25},
    'USDCAD.cfd': {'ema_fast': 15, 'ema_slow': 70, 'adx_threshold': 18},
}
DEFAULT_PARAMS = {'ema_fast': 20, 'ema_slow': 50, 'adx_threshold': 25}
BASE_PARAMS = {'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9, 'adx_period': 14}

PERIODS = [30, 60, 90, 180]

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
    full_params = {**BASE_PARAMS, **params}
    strategy = TrendFollowing(params=full_params)
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
    print(f'\nSTRESS TEST: comparat params OPTIMI (per-symbol) vs DEFAULT (20/50/25)')
    print(f'Pe 4 perioade: 30, 60, 90, 180 zile')

    for period in PERIODS:
        print(f'\n{"="*90}')
        print(f'  Perioada: {period} zile')
        print(f'{"="*90}')
        print(f'{"Simbol":14} {"Default NET":>13} {"Optim NET":>12} {"Diferenta":>12} {"Trades def/opt":>16}')
        print('-' * 90)

        total_def = 0
        total_opt = 0
        for sym in SYMBOL_PARAMS.keys():
            df = download(sym, period)
            if df is None:
                print(f'{sym}: no data')
                continue
            r_def = run_bt(sym, df, DEFAULT_PARAMS)
            r_opt = run_bt(sym, df, SYMBOL_PARAMS[sym])
            diff = r_opt['net_real'] - r_def['net_real']
            total_def += r_def['net_real']
            total_opt += r_opt['net_real']
            print(f'{sym:14} {r_def["net_real"]:>+13.2f} {r_opt["net_real"]:>+12.2f} {diff:>+12.2f} {r_def["total_trades"]:>8}/{r_opt["total_trades"]:<5}')

        print('-' * 90)
        total_diff = total_opt - total_def
        print(f'{"TOTAL":14} {total_def:>+13.2f} {total_opt:>+12.2f} {total_diff:>+12.2f}')

    mt5.shutdown()

if __name__ == '__main__':
    main()
