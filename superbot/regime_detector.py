"""
Market Regime Detector — clasifica starea pietei in TRENDING/RANGING/VOLATILE/QUIET.

Foloseste:
- ADX(14) pentru trend strength
- ATR(14)/close percentila pentru volatilitate
- Bollinger Band width pentru range detection
- Slope regresie liniara pentru confirmare trend
"""

from enum import Enum
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta

from utils.logger import setup_logger

logger = setup_logger("regime_detector")


class RegimeType(Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    QUIET = "QUIET"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeResult:
    regime: RegimeType
    adx: float
    atr_pct: float
    bb_width: float
    slope: float
    confidence: float  # 0..1

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "adx": round(self.adx, 2),
            "atr_pct": round(self.atr_pct, 4),
            "bb_width": round(self.bb_width, 4),
            "slope": round(self.slope, 6),
            "confidence": round(self.confidence, 2),
        }


class RegimeDetector:
    """Detecteaza regimul pietei pe baza indicatorilor tehnici."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.adx_period = config.get("adx_period", 14)
        self.adx_trend_threshold = config.get("adx_trend_threshold", 25)
        self.atr_period = config.get("atr_period", 14)
        self.bb_period = config.get("bb_period", 20)
        self.vol_high_pct = config.get("volatility_percentile_high", 80)
        self.quiet_low_pct = config.get("quiet_percentile_low", 20)

    def detect(self, data: pd.DataFrame, symbol: str = "") -> RegimeResult:
        """Analizeaza datele si returneaza regimul pietei.

        Args:
            data: DataFrame cu OHLCV (minim 200 bare recomandate)
            symbol: Simbol (pt log, optional)

        Returns:
            RegimeResult cu regim si metrici
        """
        if data is None or len(data) < max(self.adx_period * 2, 50):
            return RegimeResult(
                regime=RegimeType.UNKNOWN,
                adx=0, atr_pct=0, bb_width=0, slope=0, confidence=0,
            )

        try:
            # ADX — trend strength
            adx_df = ta.adx(data["high"], data["low"], data["close"], length=self.adx_period)
            adx_col = [c for c in adx_df.columns if c.startswith("ADX")][0] if adx_df is not None else None
            adx_value = float(adx_df[adx_col].iloc[-1]) if adx_col else 0.0

            # ATR normalized (as % of price)
            atr = ta.atr(data["high"], data["low"], data["close"], length=self.atr_period)
            atr_current = float(atr.iloc[-1])
            atr_pct = atr_current / float(data["close"].iloc[-1])

            # ATR percentile over last 200 bars
            atr_pct_series = atr / data["close"]
            lookback = min(200, len(atr_pct_series.dropna()))
            atr_pct_values = atr_pct_series.dropna().tail(lookback).values
            if len(atr_pct_values) > 10:
                atr_percentile_high = float(np.percentile(atr_pct_values, self.vol_high_pct))
                atr_percentile_low = float(np.percentile(atr_pct_values, self.quiet_low_pct))
            else:
                atr_percentile_high = atr_pct * 1.5
                atr_percentile_low = atr_pct * 0.5

            # Bollinger Bands width
            bbands = ta.bbands(data["close"], length=self.bb_period, std=2.0)
            if bbands is not None:
                upper_col = [c for c in bbands.columns if c.startswith("BBU")][0]
                lower_col = [c for c in bbands.columns if c.startswith("BBL")][0]
                middle_col = [c for c in bbands.columns if c.startswith("BBM")][0]
                bb_upper = float(bbands[upper_col].iloc[-1])
                bb_lower = float(bbands[lower_col].iloc[-1])
                bb_middle = float(bbands[middle_col].iloc[-1])
                bb_width = (bb_upper - bb_lower) / bb_middle if bb_middle > 0 else 0
            else:
                bb_width = 0.01

            # BB width percentile
            if bbands is not None:
                bb_width_series = (bbands[upper_col] - bbands[lower_col]) / bbands[middle_col]
                bb_width_values = bb_width_series.dropna().tail(lookback).values
                bb_width_low = float(np.percentile(bb_width_values, 30)) if len(bb_width_values) > 10 else bb_width * 0.8
            else:
                bb_width_low = bb_width * 0.8

            # Linear regression slope on last 30 closes (normalized)
            n_slope = min(30, len(data))
            y = data["close"].tail(n_slope).values
            x = np.arange(n_slope)
            if len(y) > 5:
                slope_raw = np.polyfit(x, y, 1)[0]
                slope_normalized = slope_raw / float(data["close"].iloc[-1])
            else:
                slope_normalized = 0.0

            # ============================================
            # Decision logic
            # ============================================
            regime = RegimeType.QUIET
            confidence = 0.5

            # 1. VOLATILE — override pt siguranta
            if atr_pct >= atr_percentile_high:
                regime = RegimeType.VOLATILE
                confidence = min(1.0, (atr_pct / atr_percentile_high) - 0.5)
            # 2. TRENDING — ADX puternic + slope semnificativ
            elif adx_value >= self.adx_trend_threshold and abs(slope_normalized) > 0.00005:
                regime = RegimeType.TRENDING
                confidence = min(1.0, (adx_value - self.adx_trend_threshold) / 25 + 0.5)
            # 3. RANGING — BB mic + ADX slab
            elif bb_width <= bb_width_low and adx_value < 20:
                regime = RegimeType.RANGING
                confidence = 0.6
            # 4. QUIET — ATR mic
            elif atr_pct <= atr_percentile_low:
                regime = RegimeType.QUIET
                confidence = 0.7
            # 5. Default — nedefinit, treat as QUIET (no trading)
            else:
                regime = RegimeType.QUIET
                confidence = 0.4

            result = RegimeResult(
                regime=regime,
                adx=adx_value,
                atr_pct=atr_pct,
                bb_width=bb_width,
                slope=slope_normalized,
                confidence=confidence,
            )

            if symbol:
                logger.debug(
                    f"{symbol}: {regime.value} | ADX={adx_value:.1f} "
                    f"ATR%={atr_pct:.4f} BB_w={bb_width:.4f} "
                    f"slope={slope_normalized:.6f} conf={confidence:.2f}"
                )

            return result

        except Exception as e:
            logger.error(f"Eroare la detectare regim {symbol}: {e}")
            return RegimeResult(
                regime=RegimeType.UNKNOWN,
                adx=0, atr_pct=0, bb_width=0, slope=0, confidence=0,
            )
