"""
Correlation Filter — blocheaza deschiderea pozitiilor pe simboluri corelate.

Calculeaza matricea de corelatie intre simboluri si blocheaza:
- BUY pe simbol nou daca e corelat pozitiv (>threshold) cu o pozitie BUY deja deschisa
- BUY pe simbol nou daca e corelat negativ (<-threshold) cu o pozitie SELL deschisa
- Idem pentru SELL (invers)

Motiv: evita expunere dubla pe acelasi risc (ex: BUY EURUSD + BUY GBPUSD = dublu EUR slab vs USD).
"""

from threading import Lock

import pandas as pd

from utils.logger import setup_logger

logger = setup_logger("correlation_filter")


class CorrelationFilter:
    """Filtru de corelatie pentru diversificare fortata."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.timeframe = config.get("timeframe", "M15")
        self.lookback_bars = config.get("lookback_bars", 120)
        self.max_abs_corr = config.get("max_abs_corr", 0.75)

        self._matrix: pd.DataFrame | None = None
        self._last_update: float = 0
        self._lock = Lock()

    def update(self, data_fetcher, symbols: list[str]):
        """Re-calculeaza matricea de corelatie.

        Args:
            data_fetcher: DataFetcher instance
            symbols: lista simboluri pt care calculam corelatia
        """
        try:
            # Fetch close prices pt toate simbolurile
            closes = {}
            for sym in symbols:
                data = data_fetcher.get_rates(sym, self.timeframe, bars=self.lookback_bars)
                if data is not None and len(data) >= self.lookback_bars // 2:
                    closes[sym] = data["close"].values[-self.lookback_bars:]

            if len(closes) < 2:
                logger.warning("Correlation filter: date insuficiente pt calcul")
                return

            # Aliniaza lungimile
            min_len = min(len(v) for v in closes.values())
            aligned = {k: v[-min_len:] for k, v in closes.items()}

            # DataFrame cu returns (pct_change)
            df = pd.DataFrame(aligned)
            returns = df.pct_change().dropna()

            if len(returns) < 10:
                logger.warning("Correlation filter: returns insuficiente")
                return

            # Matrice corelatie
            matrix = returns.corr()

            with self._lock:
                self._matrix = matrix
                import time
                self._last_update = time.time()

            logger.info(f"Correlation matrix updated: {len(matrix)} simboluri")

        except Exception as e:
            logger.error(f"Eroare update correlation: {e}")

    def can_open(
        self,
        symbol: str,
        direction: str,
        open_positions: list[dict],
    ) -> tuple[bool, str]:
        """Verifica daca se poate deschide o pozitie pe simbol.

        Args:
            symbol: simbolul dorit (ex: "EURUSD.cfd")
            direction: "BUY" sau "SELL"
            open_positions: lista pozitii curente din OrderManager

        Returns:
            (ok, reason) — ok=False daca e blocat
        """
        with self._lock:
            matrix = self._matrix

        if matrix is None or symbol not in matrix.index:
            return True, ""

        for pos in open_positions:
            pos_symbol = pos.get("symbol")
            pos_dir = pos.get("type")  # "BUY" or "SELL"

            if pos_symbol == symbol:
                continue  # acelasi simbol, gestionat de anti-stacking

            if pos_symbol not in matrix.columns:
                continue

            try:
                corr = float(matrix.loc[symbol, pos_symbol])
            except (KeyError, ValueError):
                continue

            # Corelatie pozitiva mare + aceeasi directie = dublare risc
            if corr >= self.max_abs_corr and direction == pos_dir:
                return False, (
                    f"Corelatie +{corr:.2f} cu {pos_symbol} ({pos_dir}) deschis"
                )

            # Corelatie negativa mare + directie opusa = tot dublare risc (hedge gresit)
            if corr <= -self.max_abs_corr and direction != pos_dir:
                return False, (
                    f"Corelatie {corr:.2f} cu {pos_symbol} ({pos_dir}) deschis"
                )

        return True, ""

    def get_matrix(self) -> pd.DataFrame | None:
        with self._lock:
            return self._matrix.copy() if self._matrix is not None else None
