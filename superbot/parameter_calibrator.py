"""
Parameter Calibrator — ajusteaza dinamic size_multiplier si parametri per regim.

Pure function, no state. Primeste regimul curent + stats performanta, returneaza:
- size_multiplier (x base volume)
- parametri ajustati (atr_multiplier, etc.)
"""

from superbot.regime_detector import RegimeType


class ParameterCalibrator:
    """Calibrator pur, calculeaza multipliers pe baza regim + performanta."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.regime_size_mult = config.get("regime_size_mult", {
            "TRENDING": 1.3,
            "RANGING": 0.7,
            "VOLATILE": 0.5,
            "QUIET": 0.0,
            "UNKNOWN": 0.0,
        })
        self.skip_quiet = config.get("skip_quiet", True)
        self.low_perf_penalty = config.get("low_perf_penalty", 0.7)
        self.min_perf_win_rate = config.get("min_perf_win_rate", 45)

    def size_multiplier(
        self,
        regime: RegimeType,
        perf_stats: dict = None,
    ) -> float:
        """Calculeaza size multiplier combinand regim + performanta.

        Args:
            regime: RegimeType curent
            perf_stats: rezultate get_stats() de la PerformanceTracker (poate fi None)

        Returns:
            Multiplier float in [0, 1.5]
        """
        # Base multiplier din regim
        regime_name = regime.value if hasattr(regime, "value") else str(regime)
        base = self.regime_size_mult.get(regime_name, 0.0)

        # Skip quiet completely?
        if self.skip_quiet and regime == RegimeType.QUIET:
            return 0.0

        if regime == RegimeType.UNKNOWN:
            return 0.0

        # Adjust cu performanta recenta
        if perf_stats and perf_stats.get("trades", 0) >= 3:
            win_rate = perf_stats.get("win_rate", 0)
            if win_rate < self.min_perf_win_rate:
                base *= self.low_perf_penalty

        # Clamp
        return max(0.0, min(base, 1.5))

    def calibrate_params(self, regime: RegimeType, base_params: dict) -> dict:
        """Ajusteaza parametri strategiei pe baza regimului.

        Args:
            regime: RegimeType curent
            base_params: parametri de baza din strategies.yaml

        Returns:
            Dict cu parametri ajustati (poate fi identic cu base_params)
        """
        adjusted = dict(base_params)

        if regime == RegimeType.TRENDING:
            # Lasa winners run
            if "adx_threshold" in adjusted:
                adjusted["adx_threshold"] = max(22, adjusted["adx_threshold"] - 3)
        elif regime == RegimeType.RANGING:
            # SL mai strans, TP mai apropiat
            pass
        elif regime == RegimeType.VOLATILE:
            # SL mai larg pt evitare noise stops
            pass

        return adjusted

    def get_atr_multiplier(self, regime: RegimeType, base_atr_mult: float = 1.5) -> float:
        """Ajusteaza ATR multiplier pentru SL pe baza regim."""
        if regime == RegimeType.TRENDING:
            return base_atr_mult * 1.2  # SL mai larg, lasa trendul
        if regime == RegimeType.RANGING:
            return base_atr_mult * 0.8  # SL mai strans
        if regime == RegimeType.VOLATILE:
            return base_atr_mult * 1.5  # SL mai larg pt noise
        return base_atr_mult
