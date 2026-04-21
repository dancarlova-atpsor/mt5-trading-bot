"""
Analiza multi-timeframe pe toate simbolurile active.
Pentru fiecare simbol:
- D1: trend major, nivele S/R majore (ultimele 90 zile)
- H4: trend intermediate, S/R intermediare
- H1: entry precision, S/R scurte
- Comportament: cum a reactionat la S/R in trecut (respinge, penetreaza, retest)
- Concluzie: zona de cumparare / vanzare / evita
"""
import sys, os
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np


SYMBOLS = ['EURUSD.cfd', 'AUDUSD.cfd', 'GBPUSD.cfd', 'EURGBP.cfd',
           'XAUUSD.cfd', 'USDCHF.cfd', 'USDCAD.cfd']


def download(symbol, tf, bars=500):
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) < 50:
        return None
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    return df


def find_swing_levels(df, lookback=5, top_n=5):
    """Gaseste swing highs si lows recente (ultimele TOP N)."""
    highs = []
    lows = []
    for i in range(lookback, len(df) - lookback):
        window_h = df['high'].iloc[i-lookback:i+lookback+1]
        window_l = df['low'].iloc[i-lookback:i+lookback+1]
        if df['high'].iloc[i] == window_h.max():
            highs.append((df['time'].iloc[i], df['high'].iloc[i]))
        if df['low'].iloc[i] == window_l.min():
            lows.append((df['time'].iloc[i], df['low'].iloc[i]))
    highs.sort(key=lambda x: x[0], reverse=True)
    lows.sort(key=lambda x: x[0], reverse=True)
    return highs[:top_n], lows[:top_n]


def cluster_levels(levels, threshold_pct=0.3):
    """Grupeaza nivele apropiate (in % din pret). Returneaza [(pret_mediu, numar_touches)]."""
    if not levels:
        return []
    prices = [l[1] for l in levels]
    prices.sort()
    clusters = []
    current = [prices[0]]
    for p in prices[1:]:
        if abs(p - current[-1]) / current[-1] * 100 < threshold_pct:
            current.append(p)
        else:
            clusters.append((np.mean(current), len(current)))
            current = [p]
    clusters.append((np.mean(current), len(current)))
    clusters.sort(key=lambda x: -x[1])  # cele mai touch-ate intai
    return clusters


def calc_atr(df, period=14):
    """ATR."""
    h = df['high']
    l = df['low']
    c_prev = df['close'].shift(1)
    tr = pd.concat([h-l, abs(h-c_prev), abs(l-c_prev)], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def analyze_symbol(sym):
    """Analiza completa pentru un simbol."""
    print(f'\n{"="*85}')
    print(f'  {sym}')
    print(f'{"="*85}')

    info = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    price = tick.bid
    print(f'Pret curent: {price:.5f} | Spread: {info.spread} pts')

    # 3 timeframes
    tfs = [
        ('D1', mt5.TIMEFRAME_D1, 90),   # 90 zile
        ('H4', mt5.TIMEFRAME_H4, 90),   # 90 zile = ~540 bare
        ('H1', mt5.TIMEFRAME_H1, 30),   # 30 zile = ~720 bare
    ]

    for tf_name, tf_mt5, days in tfs:
        bars_target = days * (24 if tf_name=='H1' else 6 if tf_name=='H4' else 1)
        df = download(sym, tf_mt5, min(bars_target, 720))
        if df is None:
            print(f'\n{tf_name}: NO DATA')
            continue

        # Trend: EMA 20 vs EMA 50
        df['ema20'] = ema(df['close'], 20)
        df['ema50'] = ema(df['close'], 50)
        trend = 'UP' if df['ema20'].iloc[-1] > df['ema50'].iloc[-1] else 'DOWN'
        trend_strength = abs(df['ema20'].iloc[-1] - df['ema50'].iloc[-1]) / df['ema50'].iloc[-1] * 100

        # ATR
        atr = calc_atr(df)
        atr_pct = atr / price * 100

        # Swing levels
        highs, lows = find_swing_levels(df, lookback=5, top_n=10)
        resistances = cluster_levels(highs, threshold_pct=0.3 if 'XAU' not in sym else 0.5)
        supports = cluster_levels(lows, threshold_pct=0.3 if 'XAU' not in sym else 0.5)

        print(f'\n[{tf_name}] Trend: {trend} (forta {trend_strength:.2f}%) | ATR: {atr:.5f} ({atr_pct:.2f}%)')

        # Gaseste cel mai apropiat support si resistance
        r_above = [r for r in resistances if r[0] > price]
        s_below = [s for s in supports if s[0] < price]

        if r_above:
            r_above.sort(key=lambda x: x[0])
            nearest_r = r_above[0]
            dist_r_pct = (nearest_r[0] - price) / price * 100
            print(f'  Rezistenta apropiata: {nearest_r[0]:.5f} ({dist_r_pct:+.2f}%, {nearest_r[1]} touches)')

        if s_below:
            s_below.sort(key=lambda x: -x[0])
            nearest_s = s_below[0]
            dist_s_pct = (nearest_s[0] - price) / price * 100
            print(f'  Suport apropiat: {nearest_s[0]:.5f} ({dist_s_pct:+.2f}%, {nearest_s[1]} touches)')

        # Top 3 nivele majore
        top_r = resistances[:3]
        top_s = supports[:3]
        if top_r:
            r_str = ', '.join(f'{r[0]:.4f}({r[1]}x)' for r in top_r)
            print(f'  TOP rezistente: {r_str}')
        if top_s:
            s_str = ', '.join(f'{s[0]:.4f}({s[1]}x)' for s in top_s)
            print(f'  TOP suporturi: {s_str}')


def verdict_per_symbol(sym):
    """Verdict scurt pentru fiecare: buy zone / sell zone / wait."""
    df_d1 = download(sym, mt5.TIMEFRAME_D1, 90)
    df_h4 = download(sym, mt5.TIMEFRAME_H4, 360)
    df_h1 = download(sym, mt5.TIMEFRAME_H1, 500)
    if df_d1 is None or df_h4 is None:
        return "NO DATA"

    info = mt5.symbol_info_tick(sym)
    price = info.bid

    # Trend D1 si H4
    d1_trend = 'UP' if ema(df_d1['close'], 20).iloc[-1] > ema(df_d1['close'], 50).iloc[-1] else 'DOWN'
    h4_trend = 'UP' if ema(df_h4['close'], 20).iloc[-1] > ema(df_h4['close'], 50).iloc[-1] else 'DOWN'
    h1_trend = 'UP' if ema(df_h1['close'], 20).iloc[-1] > ema(df_h1['close'], 50).iloc[-1] else 'DOWN'

    # S/R D1
    highs, lows = find_swing_levels(df_d1, lookback=5, top_n=10)
    resistances = cluster_levels(highs, threshold_pct=0.3 if 'XAU' not in sym else 0.5)
    supports = cluster_levels(lows, threshold_pct=0.3 if 'XAU' not in sym else 0.5)

    r_above = sorted([r for r in resistances if r[0] > price], key=lambda x: x[0])
    s_below = sorted([s for s in supports if s[0] < price], key=lambda x: -x[0])

    nearest_r_pct = (r_above[0][0] - price) / price * 100 if r_above else 99
    nearest_s_pct = (price - s_below[0][0]) / price * 100 if s_below else 99

    align = d1_trend == h4_trend == h1_trend

    if not align:
        return f"WAIT — trenduri conflictuale (D1:{d1_trend}, H4:{h4_trend}, H1:{h1_trend})"

    if d1_trend == 'UP':
        if nearest_r_pct < 0.3:
            return f"WAIT — TF UP dar la rezistenta ({nearest_r_pct:.2f}% below R)"
        if nearest_s_pct < 0.5:
            return f"BUY ZONE — UP aliniat, aproape de suport ({nearest_s_pct:.2f}% above S)"
        return f"BUY OK — UP aliniat, mijloc de range (R at {nearest_r_pct:.1f}%, S at {nearest_s_pct:.1f}%)"
    else:
        if nearest_s_pct < 0.3:
            return f"WAIT — TF DOWN dar la suport ({nearest_s_pct:.2f}% above S)"
        if nearest_r_pct < 0.5:
            return f"SELL ZONE — DOWN aliniat, aproape de rezistenta ({nearest_r_pct:.2f}% below R)"
        return f"SELL OK — DOWN aliniat, mijloc de range"


def main():
    mt5.initialize()
    print(f'MT5: {mt5.account_info().login}')

    # Analiza detaliata pe fiecare simbol
    for sym in SYMBOLS:
        analyze_symbol(sym)

    # Sumar verdicte
    print(f'\n\n{"="*85}')
    print(f'  VERDICT FINAL PER SIMBOL')
    print(f'{"="*85}')
    for sym in SYMBOLS:
        verdict = verdict_per_symbol(sym)
        print(f'  {sym:14} → {verdict}')

    mt5.shutdown()


if __name__ == '__main__':
    main()
