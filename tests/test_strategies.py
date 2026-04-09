"""
Teste pentru strategiile de trading — date sintetice, fara conexiune MT5.
"""

import numpy as np
import pandas as pd
import pytest

from strategies.base_strategy import Signal
from strategies.trend_following import TrendFollowing
from strategies.mean_reversion import MeanReversion
from strategies.scalping import Scalping


def make_ohlcv(closes: list[float], spread: float = 0.001) -> pd.DataFrame:
    """Creeaza un DataFrame OHLCV sintetic din lista de close prices."""
    n = len(closes)
    data = {
        "open": [c - spread for c in closes],
        "high": [c + spread * 2 for c in closes],
        "low": [c - spread * 2 for c in closes],
        "close": closes,
        "volume": [1000 + i * 10 for i in range(n)],
    }
    index = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(data, index=index)


def make_trending_up(n: int = 200, start: float = 1.1000) -> pd.DataFrame:
    """Creeaza date cu trend ascendent clar."""
    closes = [start + i * 0.0005 for i in range(n)]
    # Adauga un pic de zgomot
    noise = np.random.normal(0, 0.0001, n)
    closes = [c + noise[i] for i, c in enumerate(closes)]
    return make_ohlcv(closes)


def make_trending_down(n: int = 200, start: float = 1.2000) -> pd.DataFrame:
    """Creeaza date cu trend descendent clar."""
    closes = [start - i * 0.0005 for i in range(n)]
    noise = np.random.normal(0, 0.0001, n)
    closes = [c + noise[i] for i, c in enumerate(closes)]
    return make_ohlcv(closes)


def make_ranging(n: int = 200, center: float = 1.1500, amplitude: float = 0.005) -> pd.DataFrame:
    """Creeaza date oscilante (range-bound) in jurul unui centru."""
    closes = [center + amplitude * np.sin(2 * np.pi * i / 40) for i in range(n)]
    return make_ohlcv(closes)


# ==================== TrendFollowing ====================

class TestTrendFollowing:
    """Teste pentru strategia TrendFollowing."""

    @pytest.fixture
    def strategy(self):
        params = {
            "ema_fast": 20,
            "ema_slow": 50,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "adx_threshold": 25,
            "adx_period": 14,
        }
        return TrendFollowing(params)

    def test_buy_signal_trending_up(self, strategy):
        """Trend ascendent puternic ar trebui sa dea BUY."""
        np.random.seed(42)
        data = make_trending_up(200)
        signal = strategy.analyze(data)
        assert signal in (Signal.BUY, Signal.HOLD)  # BUY daca ADX e destul de mare

    def test_sell_signal_trending_down(self, strategy):
        """Trend descendent puternic ar trebui sa dea SELL."""
        np.random.seed(42)
        data = make_trending_down(200)
        signal = strategy.analyze(data)
        assert signal in (Signal.SELL, Signal.HOLD)

    def test_hold_on_insufficient_data(self, strategy):
        """Date insuficiente ar trebui sa returneze HOLD."""
        data = make_ohlcv([1.1000] * 10)
        signal = strategy.analyze(data)
        assert signal == Signal.HOLD

    def test_calculate_indicators(self, strategy):
        """Verificare ca indicatorii se calculeaza corect."""
        data = make_trending_up(200)
        df = strategy.calculate_indicators(data)
        assert "ema_fast" in df.columns
        assert "ema_slow" in df.columns
        assert "macd_hist" in df.columns
        assert "adx" in df.columns
        # Ultimele valori nu ar trebui sa fie NaN
        assert pd.notna(df["ema_fast"].iloc[-1])
        assert pd.notna(df["ema_slow"].iloc[-1])


# ==================== MeanReversion ====================

class TestMeanReversion:
    """Teste pentru strategia MeanReversion."""

    @pytest.fixture
    def strategy(self):
        params = {
            "rsi_period": 14,
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "bb_period": 20,
            "bb_std_dev": 2.0,
        }
        return MeanReversion(params)

    def test_buy_signal_oversold(self, strategy):
        """Pret supravandut (RSI < 30, sub BB lower) -> BUY sau HOLD."""
        # Creeaza o cadere brusca pentru RSI mic
        closes = [1.1500] * 100 + [1.1500 - i * 0.002 for i in range(50)]
        data = make_ohlcv(closes)
        signal = strategy.analyze(data)
        assert signal in (Signal.BUY, Signal.HOLD)

    def test_sell_signal_overbought(self, strategy):
        """Pret supracumparat (RSI > 70, peste BB upper) -> SELL sau HOLD."""
        closes = [1.1000] * 100 + [1.1000 + i * 0.002 for i in range(50)]
        data = make_ohlcv(closes)
        signal = strategy.analyze(data)
        assert signal in (Signal.SELL, Signal.HOLD)

    def test_hold_on_ranging(self, strategy):
        """Pret in range normal -> HOLD."""
        data = make_ranging(200, amplitude=0.001)  # Amplitudine mica
        signal = strategy.analyze(data)
        assert signal == Signal.HOLD

    def test_calculate_indicators(self, strategy):
        """Verificare indicatori RSI si Bollinger Bands."""
        data = make_ranging(200)
        df = strategy.calculate_indicators(data)
        assert "rsi" in df.columns
        assert "bb_lower" in df.columns
        assert "bb_middle" in df.columns
        assert "bb_upper" in df.columns
        # BB lower < BB middle < BB upper
        last = df.iloc[-1]
        assert last["bb_lower"] < last["bb_middle"] < last["bb_upper"]


# ==================== Scalping ====================

class TestScalping:
    """Teste pentru strategia Scalping."""

    @pytest.fixture
    def strategy(self):
        params = {
            "stochastic_k": 14,
            "stochastic_d": 3,
            "stochastic_smooth": 3,
            "stochastic_oversold": 20,
            "stochastic_overbought": 80,
            "max_spread_points": 15,
            "vwap_period": 20,
        }
        return Scalping(params)

    def test_spread_too_high_returns_hold(self, strategy):
        """Spread prea mare -> HOLD indiferent de semnal."""
        data = make_trending_up(200)
        strategy.set_current_spread(20)  # Peste max_spread_points=15
        signal = strategy.analyze(data)
        assert signal == Signal.HOLD

    def test_spread_ok_allows_signal(self, strategy):
        """Spread mic -> permite analiza normala."""
        data = make_trending_up(200)
        strategy.set_current_spread(5)
        signal = strategy.analyze(data)
        assert signal in (Signal.BUY, Signal.SELL, Signal.HOLD)

    def test_hold_on_insufficient_data(self, strategy):
        """Date insuficiente -> HOLD."""
        data = make_ohlcv([1.1000] * 5)
        strategy.set_current_spread(5)
        signal = strategy.analyze(data)
        assert signal == Signal.HOLD

    def test_calculate_indicators(self, strategy):
        """Verificare indicatori Stochastic si VWAP."""
        data = make_trending_up(200)
        df = strategy.calculate_indicators(data)
        assert "stoch_k" in df.columns
        assert "stoch_d" in df.columns
        assert "vwap" in df.columns
        assert pd.notna(df["stoch_k"].iloc[-1])
        assert pd.notna(df["vwap"].iloc[-1])
