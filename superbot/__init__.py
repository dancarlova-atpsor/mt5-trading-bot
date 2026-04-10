"""
SuperBot — Adaptive supervisory layer for MT5 Trading Bot.

Detects market regimes, tracks per-symbol performance, filters correlations,
runs deep analysis every 30 minutes in a background thread.
"""

from superbot.regime_detector import RegimeDetector, RegimeType
from superbot.performance_tracker import PerformanceTracker
from superbot.correlation_filter import CorrelationFilter
from superbot.parameter_calibrator import ParameterCalibrator
from superbot.deep_analyzer import DeepAnalyzer
from superbot.superbot import SuperBot, SuperBotState

__all__ = [
    "SuperBot",
    "SuperBotState",
    "RegimeDetector",
    "RegimeType",
    "PerformanceTracker",
    "CorrelationFilter",
    "ParameterCalibrator",
    "DeepAnalyzer",
]
