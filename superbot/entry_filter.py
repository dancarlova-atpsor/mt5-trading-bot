"""
Entry Filter — filtre critice inainte de entry:
1. Multi-TF trend alignment (D1 + H4 trebuie sa matcheze directia) — BREAKOUT only
2. Support/Resistance proximity — BREAKOUT only
3. Swap-aware direction restrictions (ex: XAUUSD doar SELL)
4. D1 filter selectiv pe simboluri configurate (ex: USDCAD din strategies.yaml)
5. Earnings filter pentru stocks (skip ±N zile fata de earnings)
6. Stocks session window (skip primele 30 min dupa open + ultimele 25 min)
"""

from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
import MetaTrader5 as mt5

from utils.logger import setup_logger

logger = setup_logger("entry_filter")


# Cache earnings calendar (load once, refresh on demand)
_EARNINGS_CACHE: dict | None = None
_EARNINGS_LOAD_TIME: float = 0.0


def _load_earnings_calendar() -> dict:
    """Incarca earnings_calendar.yaml. Cache 1h."""
    global _EARNINGS_CACHE, _EARNINGS_LOAD_TIME
    import time as _time
    now = _time.time()
    if _EARNINGS_CACHE is not None and (now - _EARNINGS_LOAD_TIME) < 3600:
        return _EARNINGS_CACHE
    try:
        path = Path(__file__).parent.parent / "config" / "earnings_calendar.yaml"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                _EARNINGS_CACHE = yaml.safe_load(f) or {}
        else:
            _EARNINGS_CACHE = {}
    except Exception as e:
        logger.error(f"earnings calendar load error: {e}")
        _EARNINGS_CACHE = {}
    _EARNINGS_LOAD_TIME = now
    return _EARNINGS_CACHE


def check_earnings_blackout(symbol: str, blackout_days: int = 2) -> tuple[bool, str]:
    """Verifica daca simbolul e in fereastra ±N zile fata de earnings.
    Accepta ambele formate: string YYYY-MM-DD sau datetime.date (din YAML).
    Returns (is_blackout, reason)
    """
    from datetime import date as _date
    cal = _load_earnings_calendar()
    dates = cal.get(symbol)
    if not dates:
        return False, ""
    today = datetime.now().date()
    for d_raw in dates:
        if isinstance(d_raw, _date):
            d = d_raw
        else:
            try:
                d = datetime.strptime(str(d_raw), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
        delta = abs((today - d).days)
        if delta <= blackout_days:
            return True, f"earnings {d.isoformat()} ({delta} zile distanta, blackout ±{blackout_days})"
    return False, ""


def check_d1_alignment(symbol: str, direction: str) -> tuple[bool, str]:
    """Verifica daca D1 trend (EMA20/50) e aliniat cu directia.
    Folosit selectiv (din strategies.yaml symbol_overrides[symbol].d1_filter = true).
    """
    df = _fetch_rates(symbol, mt5.TIMEFRAME_D1, bars=200)
    if df is None:
        return True, "d1: nu pot obtine date, las sa treaca"
    e20 = _ema(df["close"], 20).iloc[-1]
    e50 = _ema(df["close"], 50).iloc[-1]
    d1_up = e20 > e50
    direction = direction.upper()
    if direction == "BUY" and not d1_up:
        return False, f"d1: trend DOWN, refuz BUY"
    if direction == "SELL" and d1_up:
        return False, f"d1: trend UP, refuz SELL"
    return True, f"d1: aliniat ({'UP' if d1_up else 'DOWN'})"


def check_stocks_session(symbol: str, stock_symbols: set, trading_window: str = "17:00-22:30") -> tuple[bool, str]:
    """Verifica daca suntem in fereastra de tranzactionare stocks (RO time).
    Pentru non-stocks returneaza True imediat.
    """
    if symbol not in stock_symbols:
        return True, ""
    try:
        start_str, end_str = trading_window.split("-")
        start = dtime.fromisoformat(start_str.strip())
        end = dtime.fromisoformat(end_str.strip())
        now = datetime.now().time()
        if start <= now <= end:
            return True, ""
        return False, f"stocks_session: in afara ferestrei {trading_window} (now={now.strftime('%H:%M')})"
    except Exception as e:
        logger.error(f"stocks_session parse error: {e}")
        return True, ""


# Swap preferences per simbol: directii permise (None = orice permis)
# Calculat din swap_long vs swap_short analysis
SWAP_DIRECTION_PREFS = {
    "XAUUSD.cfd": ["SELL"],       # BUY = -$61/night per 0.1 lot — toxic
    # Altele permit ambele, dar bot va prefera directia cu swap mic natural prin alte filtre
}


def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def _fetch_rates(symbol: str, tf, bars: int = 200):
    """Fetch rates din MT5, returneaza DataFrame."""
    try:
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) < 50:
            return None
        df = pd.DataFrame(rates)
        return df
    except Exception as e:
        logger.error(f"{symbol} fetch rates error: {e}")
        return None


def _trend_direction(df: pd.DataFrame) -> str:
    """Returneaza 'UP' sau 'DOWN' bazat pe EMA20 vs EMA50."""
    e20 = _ema(df["close"], 20).iloc[-1]
    e50 = _ema(df["close"], 50).iloc[-1]
    return "UP" if e20 > e50 else "DOWN"


def _find_swing_levels(df: pd.DataFrame, lookback: int = 5) -> tuple[list, list]:
    """Gaseste swing highs si lows in date."""
    highs, lows = [], []
    for i in range(lookback, len(df) - lookback):
        window_h = df["high"].iloc[i - lookback : i + lookback + 1]
        window_l = df["low"].iloc[i - lookback : i + lookback + 1]
        if df["high"].iloc[i] == window_h.max():
            highs.append(df["high"].iloc[i])
        if df["low"].iloc[i] == window_l.min():
            lows.append(df["low"].iloc[i])
    return highs, lows


def _cluster_prices(prices: list, threshold_pct: float = 0.3) -> list[tuple[float, int]]:
    """Grupeaza preturi apropiate. Returneaza [(pret_mediu, nr_touches)] sortat dupa touches."""
    if not prices:
        return []
    prices = sorted(prices)
    clusters = []
    current = [prices[0]]
    for p in prices[1:]:
        if abs(p - current[-1]) / current[-1] * 100 < threshold_pct:
            current.append(p)
        else:
            clusters.append((float(np.mean(current)), len(current)))
            current = [p]
    clusters.append((float(np.mean(current)), len(current)))
    clusters.sort(key=lambda x: -x[1])
    return clusters


def check_entry(symbol: str, direction: str, current_price: float,
                sr_proximity_pct: float = 0.3) -> tuple[bool, str]:
    """
    Verifica daca e OK sa deschidem pozitia pe simbol in directie.

    Returns: (allowed, reason)
    """
    direction = direction.upper()

    # Filter 1: Swap-aware direction
    allowed_dirs = SWAP_DIRECTION_PREFS.get(symbol)
    if allowed_dirs and direction not in allowed_dirs:
        return False, f"swap: {symbol} permis doar {allowed_dirs} (swap prea mare pt {direction})"

    # Filter 2: Multi-TF trend alignment (D1 + H4)
    df_d1 = _fetch_rates(symbol, mt5.TIMEFRAME_D1, bars=200)
    df_h4 = _fetch_rates(symbol, mt5.TIMEFRAME_H4, bars=200)
    if df_d1 is None or df_h4 is None:
        return False, "mtf: nu pot obtine date D1/H4"

    d1_trend = _trend_direction(df_d1)
    h4_trend = _trend_direction(df_h4)

    required_trend = "UP" if direction == "BUY" else "DOWN"
    if d1_trend != required_trend:
        return False, f"mtf: D1 trend {d1_trend}, necesar {required_trend}"
    if h4_trend != required_trend:
        return False, f"mtf: H4 trend {h4_trend}, necesar {required_trend} (D1 OK)"

    # Filter 3: Support/Resistance proximity (pe D1 — nivele majore)
    highs_d1, lows_d1 = _find_swing_levels(df_d1, lookback=5)
    threshold = 0.3 if "XAU" not in symbol else 0.5
    resistances = _cluster_prices(highs_d1, threshold_pct=threshold)
    supports = _cluster_prices(lows_d1, threshold_pct=threshold)

    if direction == "BUY":
        # Nu cumpara daca e rezistenta apropiata deasupra
        r_above = [r for r in resistances if r[0] > current_price]
        if r_above:
            r_above.sort(key=lambda x: x[0])
            nearest = r_above[0]
            dist_pct = (nearest[0] - current_price) / current_price * 100
            if dist_pct < sr_proximity_pct:
                return False, f"sr: rezistenta {nearest[0]:.5f} la {dist_pct:.2f}% deasupra ({nearest[1]} touches)"
    else:
        # Nu vinde daca e suport apropiat dedesubt
        s_below = [s for s in supports if s[0] < current_price]
        if s_below:
            s_below.sort(key=lambda x: -x[0])
            nearest = s_below[0]
            dist_pct = (current_price - nearest[0]) / current_price * 100
            if dist_pct < sr_proximity_pct:
                return False, f"sr: suport {nearest[0]:.5f} la {dist_pct:.2f}% dedesubt ({nearest[1]} touches)"

    return True, f"OK (D1 {d1_trend}, H4 {h4_trend}, no S/R conflict)"
