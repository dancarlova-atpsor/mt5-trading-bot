"""
Performance Tracker — urmareste PnL per simbol + strategie in rolling window de 24h.
Auto-dezactiveaza simbolurile care pierd in mod constant.
"""

import json
import os
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from utils.logger import setup_logger

logger = setup_logger("performance_tracker")


@dataclass
class TradeRecord:
    symbol: str
    strategy: str
    pnl: float
    opened_at: float  # timestamp
    closed_at: float

    def to_dict(self) -> dict:
        return asdict(self)


class PerformanceTracker:
    """Tracker de performante pt auto-disable simboluri perdante."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.window_hours = config.get("window_hours", 24)
        self.min_trades = config.get("min_trades_to_evaluate", 5)
        self.min_win_rate = config.get("min_win_rate", 35)
        self.disable_hours = config.get("disable_hours", 4)
        self.history_file = config.get("history_file", "logs/superbot/performance.json")

        self._trades: deque[TradeRecord] = deque(maxlen=10000)
        self._disabled_until: dict[str, float] = {}  # symbol -> timestamp
        self._lock = Lock()

        self._load()

    def _load(self):
        """Incarca istoricul din JSON daca exista."""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r") as f:
                    data = json.load(f)
                for t in data.get("trades", []):
                    self._trades.append(TradeRecord(**t))
                self._disabled_until = data.get("disabled_until", {})
                logger.info(f"Performance tracker: {len(self._trades)} trades incarcate")
        except Exception as e:
            logger.warning(f"Nu pot incarca {self.history_file}: {e}")

    def persist(self):
        """Salveaza istoricul in JSON."""
        try:
            Path(self.history_file).parent.mkdir(parents=True, exist_ok=True)
            self._trim_old_trades()
            with self._lock:
                data = {
                    "trades": [t.to_dict() for t in self._trades],
                    "disabled_until": self._disabled_until,
                    "last_update": time.time(),
                }
            with open(self.history_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Eroare persist: {e}")

    def _trim_old_trades(self):
        """Elimina trade-uri mai vechi de window_hours × 2 (pt istoric)."""
        cutoff = time.time() - (self.window_hours * 2 * 3600)
        with self._lock:
            while self._trades and self._trades[0].closed_at < cutoff:
                self._trades.popleft()

    def record_trade(
        self,
        symbol: str,
        strategy: str,
        pnl: float,
        opened_at: float = None,
        closed_at: float = None,
    ):
        """Inregistreaza un trade inchis."""
        now = time.time()
        record = TradeRecord(
            symbol=symbol,
            strategy=strategy,
            pnl=pnl,
            opened_at=opened_at or now,
            closed_at=closed_at or now,
        )
        with self._lock:
            self._trades.append(record)

        logger.info(
            f"Trade inregistrat: {symbol} [{strategy}] PnL={pnl:+.2f}"
        )

        # Re-evalueaza disable
        self._evaluate_disable(symbol)
        self.persist()

    def get_stats(self, symbol: str = None, window_hours: int = None) -> dict:
        """Returneaza statistici pentru un simbol (sau toate)."""
        window = window_hours or self.window_hours
        cutoff = time.time() - (window * 3600)

        with self._lock:
            trades = [
                t for t in self._trades
                if t.closed_at >= cutoff
                and (symbol is None or t.symbol == symbol)
            ]

        if not trades:
            return {
                "symbol": symbol,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "pnl": 0.0,
                "profit_factor": 0.0,
            }

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0

        return {
            "symbol": symbol,
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "pnl": round(sum(t.pnl for t in trades), 2),
            "profit_factor": round(pf, 2),
        }

    def _evaluate_disable(self, symbol: str):
        """Verifica daca un simbol trebuie dezactivat dupa ultimul trade."""
        stats = self.get_stats(symbol)

        if stats["trades"] < self.min_trades:
            return  # nu avem date suficiente

        if stats["win_rate"] < self.min_win_rate and stats["pnl"] < 0:
            disable_until = time.time() + (self.disable_hours * 3600)
            self._disabled_until[symbol] = disable_until

            until_str = datetime.fromtimestamp(disable_until).strftime("%H:%M")
            logger.warning(
                f"AUTO-DISABLE {symbol}: "
                f"WR={stats['win_rate']}% (min {self.min_win_rate}), "
                f"PnL={stats['pnl']:+.2f}, "
                f"trades={stats['trades']} — dezactivat pana la {until_str}"
            )

    def is_symbol_disabled(self, symbol: str) -> tuple[bool, str]:
        """Verifica daca un simbol e dezactivat."""
        now = time.time()
        if symbol in self._disabled_until:
            if self._disabled_until[symbol] > now:
                remaining_min = int((self._disabled_until[symbol] - now) / 60)
                return True, f"Auto-disable performance slaba (mai sunt {remaining_min} min)"
            else:
                # Expirat, sterge
                del self._disabled_until[symbol]
                logger.info(f"{symbol}: auto-disable expirat, re-enabled")
        return False, ""

    def get_all_disabled(self) -> dict[str, int]:
        """Returneaza simbolurile dezactivate si cate minute raman."""
        now = time.time()
        result = {}
        for sym, until in list(self._disabled_until.items()):
            if until > now:
                result[sym] = int((until - now) / 60)
            else:
                del self._disabled_until[sym]
        return result
