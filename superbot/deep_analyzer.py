"""
Deep Analyzer — job background care analizeaza profund piata la fiecare 30 min.

Ruleaza in thread separat ca sa nu blocheze main loop-ul.
La fiecare ciclu:
1. Detecteaza regim per simbol (H1)
2. Update correlation matrix
3. Mini-backtest pe ultimele bare M15 pt a alege strategia optima per simbol
4. Actualizeaza SuperBotState cu symbol_plan nou
"""

import threading
import time
import traceback

from superbot.regime_detector import RegimeDetector, RegimeType
from superbot.correlation_filter import CorrelationFilter
from superbot.parameter_calibrator import ParameterCalibrator
from utils.logger import setup_logger

logger = setup_logger("deep_analyzer")


class DeepAnalyzer(threading.Thread):
    """Thread de analiza profunda la 30 min."""

    def __init__(
        self,
        state,                    # SuperBotState
        data_fetcher,
        correlation_filter: CorrelationFilter,
        performance_tracker,
        regime_detector: RegimeDetector,
        calibrator: ParameterCalibrator,
        strategies_map: dict,     # {strategy_name: instance}
        strategies_config: dict,  # dict cu parametri per strategie
        risk_manager,
        backtester_factory=None,  # callable(strategy, risk_manager, symbol) -> Backtester
        symbols: list[str] = None,
    ):
        super().__init__(daemon=True, name="SuperBot-DeepAnalyzer")
        self.state = state
        self.data_fetcher = data_fetcher
        self.correlation_filter = correlation_filter
        self.performance_tracker = performance_tracker
        self.regime_detector = regime_detector
        self.calibrator = calibrator
        self.strategies_map = strategies_map
        self.strategies_config = strategies_config
        self.risk_manager = risk_manager
        self.backtester_factory = backtester_factory
        self.symbols = symbols or []

        self._trigger = threading.Event()
        self._stop = threading.Event()
        self._running = False

    def trigger(self):
        """Cere rularea unui ciclu de analiza."""
        self._trigger.set()

    def stop(self):
        """Opreste thread-ul."""
        self._stop.set()
        self._trigger.set()  # unblock wait

    def run(self):
        """Main loop — asteapta trigger-uri si ruleaza analiza."""
        self._running = True
        logger.info("DeepAnalyzer thread pornit")

        # Ruleaza primul ciclu imediat la start
        try:
            self.run_cycle()
        except Exception as e:
            logger.error(f"Eroare primul ciclu: {e}")

        while not self._stop.is_set():
            self._trigger.wait(timeout=600)  # max 10 min fara trigger
            if self._stop.is_set():
                break
            if self._trigger.is_set():
                self._trigger.clear()
                try:
                    self.run_cycle()
                except Exception as e:
                    logger.error(f"Eroare ciclu deep analysis: {e}\n{traceback.format_exc()}")

        self._running = False
        logger.info("DeepAnalyzer thread oprit")

    def run_cycle(self):
        """Ruleaza un ciclu complet de analiza profunda."""
        start = time.time()
        logger.info("=== DeepAnalyzer: START ciclu ===")

        if not self.symbols:
            logger.warning("Nu sunt simboluri configurate")
            return

        # 1. Update correlation matrix
        try:
            self.correlation_filter.update(self.data_fetcher, self.symbols)
        except Exception as e:
            logger.error(f"Eroare update correlation: {e}")

        # 2. Build symbol_plan
        new_plan = {}

        for symbol in self.symbols:
            try:
                # Fetch H1 pt regime
                h1_data = self.data_fetcher.get_rates(symbol, "H1", bars=300)
                if h1_data is None or len(h1_data) < 100:
                    logger.warning(f"{symbol}: date H1 insuficiente")
                    new_plan[symbol] = {
                        "regime": "UNKNOWN",
                        "strategy": None,
                        "size_mult": 0.0,
                        "enabled": False,
                        "reason": "no_data",
                    }
                    continue

                # Detect regime
                regime_result = self.regime_detector.detect(h1_data, symbol)
                regime = regime_result.regime

                # Get performance stats
                perf_stats = self.performance_tracker.get_stats(symbol)

                # Size multiplier pe baza regim + performance
                size_mult = self.calibrator.size_multiplier(regime, perf_stats)

                # Daca regim e QUIET/UNKNOWN sau size mult = 0, skip simbol
                if size_mult <= 0:
                    new_plan[symbol] = {
                        "regime": regime.value,
                        "strategy": None,
                        "size_mult": 0.0,
                        "enabled": False,
                        "reason": f"regime={regime.value}",
                    }
                    continue

                # 3. Mini-backtest (optional) pt a alege cea mai buna strategie
                best_strategy_name = self._pick_best_strategy(symbol, h1_data, regime)

                new_plan[symbol] = {
                    "regime": regime.value,
                    "strategy": best_strategy_name,
                    "size_mult": round(size_mult, 2),
                    "enabled": True,
                    "adx": regime_result.adx,
                    "atr_pct": regime_result.atr_pct,
                    "confidence": regime_result.confidence,
                    "reason": "ok",
                }

                logger.info(
                    f"{symbol}: {regime.value} | strategy={best_strategy_name} | "
                    f"size_mult={size_mult:.2f} | perf={perf_stats['trades']} trades"
                )

            except Exception as e:
                logger.error(f"Eroare analiza {symbol}: {e}")
                new_plan[symbol] = {
                    "regime": "UNKNOWN",
                    "strategy": None,
                    "size_mult": 0.0,
                    "enabled": False,
                    "reason": f"error: {e}",
                }

        # 3. Atomic swap in state
        with self.state.lock:
            self.state.symbol_plan = new_plan
            self.state.last_analysis = time.time()

        elapsed = time.time() - start
        enabled_count = sum(1 for p in new_plan.values() if p.get("enabled"))
        logger.info(
            f"=== DeepAnalyzer: ciclu COMPLET in {elapsed:.1f}s | "
            f"{enabled_count}/{len(new_plan)} simboluri active ==="
        )

    def _pick_best_strategy(self, symbol: str, data, regime: RegimeType) -> str | None:
        """Alege cea mai potrivita strategie pe baza regim + strategiile care au simbolul in config.

        Pt ciclu deep simplu (nu mini-backtest acum — pentru sanatate si viteza),
        folosim regim-based matching:
        - TRENDING → trend_following sau breakout
        - RANGING → mean_reversion (daca activat) sau macd_divergence
        - VOLATILE → breakout (catch momentum)
        - QUIET → None (skip)
        """
        if regime == RegimeType.QUIET or regime == RegimeType.UNKNOWN:
            return None

        # Candidati pe baza regim
        if regime == RegimeType.TRENDING:
            candidates = ["trend_following", "breakout", "macd_divergence"]
        elif regime == RegimeType.RANGING:
            candidates = ["macd_divergence", "mean_reversion", "news_sentiment"]
        elif regime == RegimeType.VOLATILE:
            candidates = ["breakout", "trend_following"]
        else:
            candidates = list(self.strategies_map.keys())

        # Gaseste primul candidat care e (1) in active strategies_map, (2) enabled, (3) are simbolul
        for cand in candidates:
            if cand not in self.strategies_map:
                continue  # Strategia nu e activa (nu e in active_strategies)
            cfg = self.strategies_config.get(cand, {})
            if not cfg.get("enabled", False):
                continue
            sym_list = cfg.get("symbols", [])
            if symbol in sym_list:
                return cand

        return None
