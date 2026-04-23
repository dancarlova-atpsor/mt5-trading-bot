"""
Backtest: TrendFollowing CU MACD check vs FARA MACD check.
Pe toate 6 simboluri, 90 zile, cu params optimi per simbol.
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.base_strategy import Signal


SYMBOL_PARAMS = {
    'EURUSD.cfd': {'ema_fast': 20, 'ema_slow': 50, 'adx_threshold': 25},
    'AUDUSD.cfd': {'ema_fast': 15, 'ema_slow': 70, 'adx_threshold': 25},
    'EURGBP.cfd': {'ema_fast': 15, 'ema_slow': 70, 'adx_threshold': 30},
    'XAUUSD.cfd': {'ema_fast': 10, 'ema_slow': 70, 'adx_threshold': 30},
    'USDCHF.cfd': {'ema_fast': 10, 'ema_slow': 30, 'adx_threshold': 25},
    'USDCAD.cfd': {'ema_fast': 15, 'ema_slow': 70, 'adx_threshold': 18},
}
BASE_PARAMS = {'macd_fast': 12, 'macd_slow': 26, 'macd_signal': 9, 'adx_period': 14}
SWAP_COST = {'EURUSD.cfd': 0.5, 'AUDUSD.cfd': 0.4, 'EURGBP.cfd': 0.1,
             'XAUUSD.cfd': 3.0, 'USDCHF.cfd': 2.0, 'USDCAD.cfd': 1.5}
COMMISSION = 1.5


class TrendFollowingNoMACD(TrendFollowing):
    """Variant fara MACD check."""
    def analyze(self, data):
        df = self.calculate_indicators(data)
        last = df.iloc[-1]
        if last[["ema_fast", "ema_slow", "adx"]].isna().any():
            return Signal.HOLD
        adx_strong = last["adx"] > self._p("adx_threshold")
        # BUY: close>ema_slow, ema_fast>ema_slow, adx strong (FARA MACD)
        if last["close"] > last["ema_slow"] and last["ema_fast"] > last["ema_slow"] and adx_strong:
            return Signal.BUY
        if last["close"] < last["ema_slow"] and last["ema_fast"] < last["ema_slow"] and adx_strong:
            return Signal.SELL
        return Signal.HOLD


def download(sym, days):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    mt5.symbol_select(sym, True)
    rates = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates is None: return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.rename(columns={'tick_volume': 'volume'}, inplace=True)
    return df


def run_bt(sym, df, params, strat_class):
    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config['risk_reward_ratio'] = 3.0
    full = {**BASE_PARAMS, **params}
    strategy = strat_class(params=full)
    bt = Backtester(strategy=strategy, risk_manager=rm, symbol=sym,
                    timeframe='H1', initial_balance=100000.0, spread_pips=1.5)
    r = bt.run(df, lookback=60)
    swap = r['total_trades'] * SWAP_COST.get(sym, 1.0)
    comm = r['total_trades'] * COMMISSION
    r['net_real'] = r['net_profit'] - swap - comm
    return r


def main():
    mt5.initialize()
    print(f'MT5: {mt5.account_info().login}')
    print(f'\\nBacktest 90d: TF cu MACD vs TF FARA MACD check\\n')
    print(f'{"Simbol":14} {"Tr CU":7} {"WR CU":6} {"NET CU":>10} | {"Tr FARA":7} {"WR FARA":6} {"NET FARA":>10} | {"Castig":>10}')
    print('-' * 100)

    total_cu = 0
    total_fara = 0
    for sym, params in SYMBOL_PARAMS.items():
        df = download(sym, 90)
        if df is None: continue
        r_cu = run_bt(sym, df, params, TrendFollowing)
        r_fa = run_bt(sym, df, params, TrendFollowingNoMACD)
        total_cu += r_cu['net_real']
        total_fara += r_fa['net_real']
        diff = r_fa['net_real'] - r_cu['net_real']
        marker = '🟢' if diff > 0 else '🔴'
        print(f'{sym:14} {r_cu["total_trades"]:>7} {r_cu["win_rate"]:>5.1f}% {r_cu["net_real"]:>+10.2f} | '
              f'{r_fa["total_trades"]:>7} {r_fa["win_rate"]:>5.1f}% {r_fa["net_real"]:>+10.2f} | '
              f'{diff:>+10.2f}')

    print('-' * 100)
    print(f'{"TOTAL":14} {"":>7} {"":>6} {total_cu:>+10.2f} | {"":>7} {"":>6} {total_fara:>+10.2f} | {total_fara-total_cu:>+10.2f}')
    print()
    if total_fara > total_cu:
        print(f'VERDICT: FARA MACD e mai bun cu {total_fara-total_cu:+.2f} (+{(total_fara-total_cu)/abs(total_cu)*100:.1f}%)')
    else:
        print(f'VERDICT: CU MACD e mai bun cu {total_cu-total_fara:+.2f} — pastram MACD check')

    mt5.shutdown()

if __name__ == '__main__':
    main()
