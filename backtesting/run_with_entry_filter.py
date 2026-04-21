"""
Backtest cu entry_filter activat — compara performanta WITH vs WITHOUT entry_filter.

Pentru fiecare simbol, ruleaza backtest normal + backtest cu entry filter care
blocheaza trade-urile ce NU indeplinesc MTF + S/R + swap.
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from backtesting.backtester import Backtester
from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout


SYMBOLS = ['EURUSD.cfd', 'AUDUSD.cfd', 'GBPUSD.cfd', 'EURGBP.cfd',
           'XAUUSD.cfd', 'USDCHF.cfd', 'USDCAD.cfd']
LOOKBACK_DAYS = 90

# Swap per trade (estimat per pozitie tinuta 1.5 zile, volum 0.1 lot)
SWAP_COST_PER_TRADE = {
    'EURUSD.cfd': 0.5,  'AUDUSD.cfd': 0.4,  'GBPUSD.cfd': 0.8,
    'EURGBP.cfd': 0.1,  'XAUUSD.cfd': 5.0,  # mediu BUY+SELL (daca bot e doar SELL: $3)
    'USDCHF.cfd': 2.0,  'USDCAD.cfd': 1.5,
}

COMMISSION_PER_TRADE = 1.5  # mediu forex


def download(symbol, tf, days):
    """Download istoric."""
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) < 100:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    df.rename(columns={'tick_volume': 'volume'}, inplace=True)
    return df


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def simulate_entry_filter(df_h1, df_h4, df_d1, idx, direction, sr_threshold=0.3):
    """Replica logica entry_filter.check_entry pe date istorice la indicele idx.

    Returns: (allowed, reason)
    """
    if idx < 50 or idx >= len(df_h1):
        return True, "edge"  # Nu avem date suficiente

    current_price = df_h1['close'].iloc[idx]
    current_time = df_h1.index[idx]

    # Gaseste H4 si D1 bara corespunzatoare (cea mai apropiata inainte de timestamp)
    h4_mask = df_h4.index <= current_time
    d1_mask = df_d1.index <= current_time
    if not h4_mask.any() or not d1_mask.any():
        return True, "no_data"
    h4_idx = h4_mask.sum() - 1
    d1_idx = d1_mask.sum() - 1
    if h4_idx < 50 or d1_idx < 50:
        return True, "warmup"

    # MTF trend
    h4_slice = df_h4.iloc[:h4_idx + 1]
    d1_slice = df_d1.iloc[:d1_idx + 1]

    d1_up = ema(d1_slice['close'], 20).iloc[-1] > ema(d1_slice['close'], 50).iloc[-1]
    h4_up = ema(h4_slice['close'], 20).iloc[-1] > ema(h4_slice['close'], 50).iloc[-1]

    required_up = (direction.upper() == 'BUY')
    if d1_up != required_up:
        return False, "D1 contra"
    if h4_up != required_up:
        return False, "H4 contra"

    # S/R proximity — folosim swing levels din D1
    highs = []
    lows = []
    lookback = 5
    d1_window = d1_slice.tail(60)  # ultimele 60 zile
    for i in range(lookback, len(d1_window) - lookback):
        wh = d1_window['high'].iloc[i - lookback: i + lookback + 1]
        wl = d1_window['low'].iloc[i - lookback: i + lookback + 1]
        if d1_window['high'].iloc[i] == wh.max():
            highs.append(d1_window['high'].iloc[i])
        if d1_window['low'].iloc[i] == wl.min():
            lows.append(d1_window['low'].iloc[i])

    if direction.upper() == 'BUY':
        r_above = [r for r in highs if r > current_price]
        if r_above:
            nearest = min(r_above)
            if (nearest - current_price) / current_price * 100 < sr_threshold:
                return False, "R close"
    else:
        s_below = [s for s in lows if s < current_price]
        if s_below:
            nearest = max(s_below)
            if (current_price - nearest) / current_price * 100 < sr_threshold:
                return False, "S close"

    return True, "OK"


class FilteredStrategy:
    """Wrapper peste o strategie care aplica entry_filter pe analyze()."""
    def __init__(self, base_strategy, df_h4, df_d1, df_h1, sr_threshold=0.3):
        self.base = base_strategy
        self.df_h4 = df_h4
        self.df_d1 = df_d1
        self.df_h1 = df_h1
        self.sr_threshold = sr_threshold
        self.blocked_count = 0
        self.block_reasons = {}
        self.name = base_strategy.name

    def analyze(self, data):
        signal = self.base.analyze(data)
        from strategies.base_strategy import Signal
        if signal == Signal.HOLD:
            return signal

        # Gaseste idx curent in df_h1
        current_time = data.index[-1]
        h1_mask = self.df_h1.index == current_time
        if not h1_mask.any():
            return signal  # not found, let pass
        idx = h1_mask.argmax()

        allowed, reason = simulate_entry_filter(
            self.df_h1, self.df_h4, self.df_d1, idx, signal.value, self.sr_threshold
        )
        if not allowed:
            self.blocked_count += 1
            self.block_reasons[reason] = self.block_reasons.get(reason, 0) + 1
            return Signal.HOLD
        return signal

    # Delegate other attributes to base
    def __getattr__(self, name):
        return getattr(self.base, name)


def run_single(sym, strategy_class, params, with_filter=False, sr_threshold=0.3):
    df_h1 = download(sym, mt5.TIMEFRAME_H1, LOOKBACK_DAYS)
    if df_h1 is None:
        return None

    rm = RiskManager()
    rm.take_profit_config = dict(rm.take_profit_config)
    rm.take_profit_config['risk_reward_ratio'] = 3.0
    strategy = strategy_class(params=params)

    filt = None
    if with_filter:
        df_h4 = download(sym, mt5.TIMEFRAME_H4, LOOKBACK_DAYS + 30)
        df_d1 = download(sym, mt5.TIMEFRAME_D1, LOOKBACK_DAYS + 60)
        filt = FilteredStrategy(strategy, df_h4, df_d1, df_h1, sr_threshold)
        strat_to_use = filt
    else:
        strat_to_use = strategy

    bt = Backtester(
        strategy=strat_to_use, risk_manager=rm, symbol=sym,
        timeframe='H1', initial_balance=100000.0, spread_pips=1.5,
    )
    r = bt.run(df_h1, lookback=60)

    # Cost real: comision + swap
    swap_cost = r['total_trades'] * SWAP_COST_PER_TRADE.get(sym, 1.0)
    comm = r['total_trades'] * COMMISSION_PER_TRADE
    r['swap_cost'] = swap_cost
    r['commission'] = comm
    r['net_real'] = r['net_profit'] - swap_cost - comm
    if filt:
        r['blocked'] = filt.blocked_count
        r['block_reasons'] = filt.block_reasons
    else:
        r['blocked'] = 0
    return r


def main():
    mt5.initialize()
    print(f'MT5: {mt5.account_info().login}')
    print(f'Backtesting 90 zile WITH vs WITHOUT entry_filter...\n')

    tf_params = {'ema_fast': 20, 'ema_slow': 50, 'macd_fast': 12, 'macd_slow': 26,
                 'macd_signal': 9, 'adx_threshold': 25, 'adx_period': 14}
    br_params = {'lookback_period': 50, 'atr_period': 14, 'min_atr_ratio': 0.8, 'min_vol_ratio': 2.0}

    print(f"\n{'='*115}")
    print(f"  COMPARATIE: fara filter vs cu filter (SR threshold 0.3%)")
    print(f"{'='*115}")
    print(f"{'Simbol':10} {'Strat':12} {'NOF Trades':10} {'NOF NET':10} {'NOF WR':7} | {'WF Trades':10} {'WF Block':10} {'WF NET':10} {'WF WR':7}")
    print('-' * 115)

    total_nof = 0
    total_wf = 0
    for sym in SYMBOLS:
        for strat_name, cls, params in [('TrendFollow', TrendFollowing, tf_params),
                                         ('Breakout', Breakout, br_params)]:
            r_no = run_single(sym, cls, params, with_filter=False)
            r_wf = run_single(sym, cls, params, with_filter=True, sr_threshold=0.3)
            if r_no is None or r_wf is None:
                continue
            print(f"{sym:10} {strat_name:12} {r_no['total_trades']:>10} {r_no['net_real']:>+10.2f} {r_no['win_rate']:>6.1f}% | "
                  f"{r_wf['total_trades']:>10} {r_wf['blocked']:>10} {r_wf['net_real']:>+10.2f} {r_wf['win_rate']:>6.1f}%")
            total_nof += r_no['net_real']
            total_wf += r_wf['net_real']

    print('-' * 115)
    print(f"TOTAL NET (cu swap+commission):")
    print(f"  FARA entry_filter: ${total_nof:+.2f}")
    print(f"  CU   entry_filter: ${total_wf:+.2f}")
    print(f"  Diferenta filtru:  ${total_wf - total_nof:+.2f}")

    mt5.shutdown()

if __name__ == '__main__':
    main()
