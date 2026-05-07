"""
SuperBot — Top-level facade pentru adaptive layer.

Single entry point pt main.py:
- gate_trade() — consultat inainte de orice tranzactie
- on_trade_closed() — notificare dupa inchidere (update perf tracker)
- trigger_deep_analysis() — cere un ciclu nou de analiza
- start() / shutdown() — lifecycle
"""

from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from superbot.regime_detector import RegimeDetector, RegimeType
from superbot.performance_tracker import PerformanceTracker
from superbot.correlation_filter import CorrelationFilter
from superbot.parameter_calibrator import ParameterCalibrator
from superbot.deep_analyzer import DeepAnalyzer
from utils.logger import setup_logger

logger = setup_logger("superbot")


@dataclass
class SuperBotState:
    """Shared state intre main loop si DeepAnalyzer thread."""
    symbol_plan: dict = field(default_factory=dict)  # symbol -> {regime, strategy, size_mult, enabled}
    last_analysis: float = 0
    lock: Lock = field(default_factory=Lock)


class SuperBot:
    """Supervisory layer adaptiv pentru trading bot."""

    def __init__(
        self,
        config: dict,
        data_fetcher,
        risk_manager,
        strategies_map: dict,
        strategies_config: dict,
        symbols: list[str],
    ):
        sb_config = config.get("superbot", {})
        self.enabled = sb_config.get("enabled", False)
        self.deep_interval = sb_config.get("deep_analysis_interval_iterations", 30)

        # Componente
        self.regime_detector = RegimeDetector(sb_config.get("regime", {}))
        self.performance_tracker = PerformanceTracker(sb_config.get("performance", {}))
        self.correlation_filter = CorrelationFilter(sb_config.get("correlation", {}))
        self.calibrator = ParameterCalibrator(sb_config.get("calibrator", {}))

        # State partajat
        self.state = SuperBotState()

        # Deep analyzer thread
        self.deep_analyzer = DeepAnalyzer(
            state=self.state,
            data_fetcher=data_fetcher,
            correlation_filter=self.correlation_filter,
            performance_tracker=self.performance_tracker,
            regime_detector=self.regime_detector,
            calibrator=self.calibrator,
            strategies_map=strategies_map,
            strategies_config=strategies_config,
            risk_manager=risk_manager,
            symbols=symbols,
        )

        self.symbols = symbols
        self._iter_counter = 0

        # Per-symbol D1 filter list — extras din strategies.yaml symbol_overrides
        # Folosit selectiv: doar simbolurile cu d1_filter:true sunt verificate
        self.d1_filter_symbols: set = set()
        try:
            tf_cfg = strategies_config.get("trend_following", {}) if strategies_config else {}
            for sym, ovr in (tf_cfg.get("symbol_overrides") or {}).items():
                if isinstance(ovr, dict) and ovr.get("d1_filter"):
                    self.d1_filter_symbols.add(sym)
            if self.d1_filter_symbols:
                logger.info(f"D1 filter activ pentru: {sorted(self.d1_filter_symbols)}")
        except Exception as e:
            logger.error(f"D1 filter setup error: {e}")

        if self.enabled:
            logger.info(
                f"SuperBot INITIALIZED | {len(symbols)} simboluri | "
                f"deep analysis la {self.deep_interval} iteratii"
            )
        else:
            logger.info("SuperBot in SHADOW MODE (enabled=false)")

    def start(self):
        """Porneste deep analyzer thread."""
        if not self.enabled:
            return
        self.deep_analyzer.start()
        logger.info("SuperBot pornit")

    def shutdown(self):
        """Opreste deep analyzer + persist performance."""
        if not self.enabled:
            return
        try:
            self.deep_analyzer.stop()
            self.performance_tracker.persist()
            logger.info("SuperBot oprit")
        except Exception as e:
            logger.error(f"Eroare shutdown: {e}")

    def tick(self):
        """Apelat la fiecare iteratie main loop. Cere deep analysis la fiecare N iteratii."""
        if not self.enabled:
            return
        self._iter_counter += 1
        if self._iter_counter % self.deep_interval == 0:
            logger.info(f"Tick #{self._iter_counter} — trigger deep analysis")
            self.deep_analyzer.trigger()

    def gate_trade(
        self,
        symbol: str,
        strategy_name: str,
        direction: str,
        open_positions: list[dict],
        current_price: float = 0.0,
    ) -> tuple[bool, float, str]:
        """Single entry point pentru verificare deschidere trade.

        Returns:
            (ok, size_mult, reason)
            - ok=True si size_mult>0 → poate deschide cu volume * size_mult
            - ok=False → blocat, reason explica
        """
        if not self.enabled:
            return True, 1.0, "shadow_mode"

        # 1. Performance tracker — simbol dezactivat?
        disabled, reason = self.performance_tracker.is_symbol_disabled(symbol)
        if disabled:
            return False, 0.0, f"perf: {reason}"

        # 2. Correlation filter
        ok, reason = self.correlation_filter.can_open(symbol, direction, open_positions)
        if not ok:
            return False, 0.0, f"corr: {reason}"

        # 3. Entry filter — MTF trend + S/R + swap direction
        # Backtest 90d arata: filter SALVEAZA pe Breakout (+$1427 total),
        # dar STRICA pe TrendFollow (blocheaza prea multe trade-uri bune)
        # → Aplic filter DOAR pe breakout; TrendFollow merge liber
        FILTER_STRATEGIES = {"breakout"}
        if current_price > 0 and strategy_name in FILTER_STRATEGIES:
            from superbot.entry_filter import check_entry
            ok, reason = check_entry(symbol, direction, current_price)
            if not ok:
                return False, 0.0, f"entry: {reason}"
        # XAUUSD swap filter se aplica MEREU (nu doar pe breakout) — e critic
        if symbol == "XAUUSD.cfd" and direction.upper() == "BUY":
            return False, 0.0, "swap: XAUUSD BUY = -$61/noapte, blocat mereu"

        # D1 filter selectiv (din strategies.yaml symbol_overrides[sym].d1_filter:true)
        if symbol in self.d1_filter_symbols:
            from superbot.entry_filter import check_d1_alignment
            ok, reason = check_d1_alignment(symbol, direction)
            if not ok:
                return False, 0.0, f"d1_filter: {reason}"

        # 4. Verifica symbol_plan din deep analyzer
        with self.state.lock:
            plan = self.state.symbol_plan.get(symbol, {})

        if plan:
            # Plan exista din ultima analiza
            if not plan.get("enabled", True):
                return False, 0.0, f"plan: {plan.get('reason', 'disabled')}"

            # Strategy match — pe toate regime-urile forteaza strategia recomandata.
            # Asigura ca TF ruleaza doar pe TRENDING, Breakout pe volatile etc.
            plan_strategy = plan.get("strategy")
            if plan_strategy and plan_strategy != strategy_name:
                return False, 0.0, f"plan: wrong strategy (vreau {plan_strategy})"

            size_mult = plan.get("size_mult", 1.0)
            return True, size_mult, f"plan: {plan.get('regime', 'OK')}"

        # Fara plan inca (primul ciclu) — lasa sa treaca cu mult 1.0
        return True, 1.0, "no_plan_yet"

    def on_trade_closed(
        self,
        symbol: str,
        strategy: str,
        pnl: float,
        opened_at: float = None,
        closed_at: float = None,
    ):
        """Notificare cand un trade e inchis. Feed catre performance tracker."""
        if not self.enabled:
            return
        self.performance_tracker.record_trade(
            symbol=symbol,
            strategy=strategy,
            pnl=pnl,
            opened_at=opened_at,
            closed_at=closed_at,
        )

    def get_status(self) -> dict:
        """Returneaza status pentru monitorizare/log."""
        with self.state.lock:
            plan = dict(self.state.symbol_plan)
            last = self.state.last_analysis

        disabled = self.performance_tracker.get_all_disabled()

        return {
            "enabled": self.enabled,
            "iter_counter": self._iter_counter,
            "last_analysis": last,
            "symbol_plan": plan,
            "disabled_symbols": disabled,
        }
