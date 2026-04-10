"""
Tests unit pentru SuperBot — regime_detector, performance_tracker,
correlation_filter, parameter_calibrator.
"""

import time
import tempfile
import os

import numpy as np
import pandas as pd
import pytest

from superbot.regime_detector import RegimeDetector, RegimeType
from superbot.performance_tracker import PerformanceTracker
from superbot.correlation_filter import CorrelationFilter
from superbot.parameter_calibrator import ParameterCalibrator


# ============================================
# Helpers
# ============================================

def make_ohlcv(closes: list[float], noise: float = 0.0001) -> pd.DataFrame:
    n = len(closes)
    np.random.seed(42)
    noise_arr = np.random.normal(0, noise, n)
    data = {
        "open": [c + np.random.normal(0, noise) for c in closes],
        "high": [c + abs(np.random.normal(0, noise * 2)) for c in closes],
        "low": [c - abs(np.random.normal(0, noise * 2)) for c in closes],
        "close": closes,
        "volume": [1000 + i for i in range(n)],
    }
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(data, index=idx)


def make_trending_up(n: int = 300, start: float = 1.1000, step: float = 0.0003):
    np.random.seed(42)
    closes = [start + i * step + np.random.normal(0, 0.00005) for i in range(n)]
    return make_ohlcv(closes, noise=0.0001)


def make_ranging(n: int = 300, center: float = 1.1500, amplitude: float = 0.002):
    closes = [center + amplitude * np.sin(2 * np.pi * i / 30) for i in range(n)]
    return make_ohlcv(closes, noise=0.0001)


def make_volatile(n: int = 300):
    np.random.seed(42)
    closes = [1.1500 + np.random.normal(0, 0.005) for _ in range(n)]
    return make_ohlcv(closes, noise=0.001)


def make_quiet(n: int = 300):
    closes = [1.1500 + i * 0.00001 for i in range(n)]
    return make_ohlcv(closes, noise=0.00002)


# ============================================
# RegimeDetector
# ============================================

class TestRegimeDetector:

    @pytest.fixture
    def detector(self):
        return RegimeDetector()

    def test_trending_up(self, detector):
        data = make_trending_up(300)
        result = detector.detect(data, "TEST")
        assert result.regime in (RegimeType.TRENDING, RegimeType.QUIET)
        assert result.adx > 0

    def test_volatile(self, detector):
        data = make_volatile(300)
        result = detector.detect(data, "TEST")
        # Volatile sau trending pe zgomot
        assert result.regime in (RegimeType.VOLATILE, RegimeType.TRENDING, RegimeType.QUIET, RegimeType.RANGING)
        assert result.atr_pct > 0

    def test_insufficient_data(self, detector):
        data = make_ohlcv([1.1] * 10)
        result = detector.detect(data, "TEST")
        assert result.regime == RegimeType.UNKNOWN

    def test_returns_metrics(self, detector):
        data = make_trending_up(300)
        result = detector.detect(data, "TEST")
        d = result.to_dict()
        assert "regime" in d
        assert "adx" in d
        assert "atr_pct" in d
        assert "bb_width" in d
        assert "slope" in d
        assert "confidence" in d


# ============================================
# PerformanceTracker
# ============================================

class TestPerformanceTracker:

    @pytest.fixture
    def tracker(self, tmp_path):
        config = {
            "window_hours": 24,
            "min_trades_to_evaluate": 3,
            "min_win_rate": 40,
            "disable_hours": 2,
            "history_file": str(tmp_path / "perf.json"),
        }
        return PerformanceTracker(config)

    def test_record_trade(self, tracker):
        tracker.record_trade("EURUSD", "TF", +50.0)
        stats = tracker.get_stats("EURUSD")
        assert stats["trades"] == 1
        assert stats["wins"] == 1
        assert stats["pnl"] == 50.0
        assert stats["win_rate"] == 100.0

    def test_auto_disable_losing_symbol(self, tracker):
        # 4 trade-uri pierzatoare din 5 (win rate 20% < 40%)
        tracker.record_trade("GBPJPY", "MACD", +10.0)
        tracker.record_trade("GBPJPY", "MACD", -30.0)
        tracker.record_trade("GBPJPY", "MACD", -25.0)
        tracker.record_trade("GBPJPY", "MACD", -15.0)
        tracker.record_trade("GBPJPY", "MACD", -20.0)

        disabled, reason = tracker.is_symbol_disabled("GBPJPY")
        assert disabled is True
        assert "Auto-disable" in reason

    def test_good_symbol_not_disabled(self, tracker):
        # 4 din 5 castigatoare (80% WR)
        for i in range(4):
            tracker.record_trade("EURUSD", "TF", +25.0)
        tracker.record_trade("EURUSD", "TF", -10.0)

        disabled, _ = tracker.is_symbol_disabled("EURUSD")
        assert disabled is False

    def test_not_enough_trades(self, tracker):
        # Doar 2 trade-uri, sub min_trades_to_evaluate=3
        tracker.record_trade("XAUUSD", "TF", -50.0)
        tracker.record_trade("XAUUSD", "TF", -30.0)
        disabled, _ = tracker.is_symbol_disabled("XAUUSD")
        assert disabled is False

    def test_persistence(self, tmp_path):
        config = {
            "window_hours": 24,
            "min_trades_to_evaluate": 3,
            "min_win_rate": 40,
            "disable_hours": 2,
            "history_file": str(tmp_path / "perf.json"),
        }
        t1 = PerformanceTracker(config)
        t1.record_trade("EURUSD", "TF", +100)
        t1.persist()

        t2 = PerformanceTracker(config)
        stats = t2.get_stats("EURUSD")
        assert stats["trades"] == 1
        assert stats["pnl"] == 100.0


# ============================================
# CorrelationFilter
# ============================================

class TestCorrelationFilter:

    @pytest.fixture
    def cfilter(self):
        return CorrelationFilter({"max_abs_corr": 0.75})

    def test_no_matrix_allows_all(self, cfilter):
        # Fara matrice, nu blocheaza nimic
        ok, _ = cfilter.can_open("EURUSD", "BUY", [])
        assert ok is True

    def test_blocks_same_direction_correlated(self, cfilter):
        # Injecteaza o matrice manuala
        import numpy as np
        matrix = pd.DataFrame(
            [[1.0, 0.85], [0.85, 1.0]],
            index=["EURUSD", "GBPUSD"],
            columns=["EURUSD", "GBPUSD"],
        )
        cfilter._matrix = matrix

        # GBPUSD BUY deja deschis, EURUSD BUY trebuie blocat (corr 0.85)
        positions = [{"symbol": "GBPUSD", "type": "BUY"}]
        ok, reason = cfilter.can_open("EURUSD", "BUY", positions)
        assert ok is False
        assert "0.85" in reason or "Corelatie" in reason

    def test_allows_opposite_direction(self, cfilter):
        matrix = pd.DataFrame(
            [[1.0, 0.85], [0.85, 1.0]],
            index=["EURUSD", "GBPUSD"],
            columns=["EURUSD", "GBPUSD"],
        )
        cfilter._matrix = matrix
        # BUY EURUSD, SELL GBPUSD — directii opuse, OK (nu dubleaza risc)
        positions = [{"symbol": "GBPUSD", "type": "SELL"}]
        ok, _ = cfilter.can_open("EURUSD", "BUY", positions)
        assert ok is True

    def test_blocks_negative_correlation_opposite(self, cfilter):
        matrix = pd.DataFrame(
            [[1.0, -0.85], [-0.85, 1.0]],
            index=["AUDUSD", "USDCHF"],
            columns=["AUDUSD", "USDCHF"],
        )
        cfilter._matrix = matrix
        # AUDUSD BUY, USDCHF BUY (opuse -0.85) — blocheaza
        positions = [{"symbol": "AUDUSD", "type": "BUY"}]
        ok, _ = cfilter.can_open("USDCHF", "SELL", positions)
        assert ok is False


# ============================================
# ParameterCalibrator
# ============================================

class TestParameterCalibrator:

    @pytest.fixture
    def calibrator(self):
        return ParameterCalibrator()

    def test_trending_boost(self, calibrator):
        mult = calibrator.size_multiplier(RegimeType.TRENDING)
        assert mult >= 1.0
        assert mult <= 1.5

    def test_quiet_skip(self, calibrator):
        mult = calibrator.size_multiplier(RegimeType.QUIET)
        assert mult == 0.0

    def test_volatile_reduce(self, calibrator):
        mult = calibrator.size_multiplier(RegimeType.VOLATILE)
        assert 0 < mult < 1.0

    def test_unknown_returns_zero(self, calibrator):
        mult = calibrator.size_multiplier(RegimeType.UNKNOWN)
        assert mult == 0.0

    def test_bad_performance_penalty(self, calibrator):
        # Regime TRENDING dar performance slaba
        good_stats = {"trades": 10, "win_rate": 70, "pnl": 500}
        bad_stats = {"trades": 10, "win_rate": 30, "pnl": -200}

        mult_good = calibrator.size_multiplier(RegimeType.TRENDING, good_stats)
        mult_bad = calibrator.size_multiplier(RegimeType.TRENDING, bad_stats)
        assert mult_bad < mult_good

    def test_atr_multiplier_trending(self, calibrator):
        mult = calibrator.get_atr_multiplier(RegimeType.TRENDING, 1.5)
        assert mult > 1.5  # Mai larg pe trending

    def test_atr_multiplier_ranging(self, calibrator):
        mult = calibrator.get_atr_multiplier(RegimeType.RANGING, 1.5)
        assert mult < 1.5  # Mai strans pe ranging
