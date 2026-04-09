"""
Profit Manager — trailing profit per pozitie, cicluri repetitive, limite zilnice.

Logica:
  Per pozitie: trailing profit (activare la X EUR, trail Y%)
  Per ciclu: profit target (inchide tot la +Z EUR)
  Per zi: max profit (+300 EUR) si max loss (-53 EUR)
"""

import time
from datetime import date

from utils.logger import setup_logger
from utils.helpers import load_config

logger = setup_logger("profit_manager")


class ProfitManager:
    """Gestioneaza trailing profit, cicluri si limite zilnice."""

    def __init__(self):
        config = load_config("settings.yaml")
        pm = config.get("profit_manager", {})

        # Per pozitie — trailing profit
        self.trailing_activation = pm.get("trailing_activation_eur", 30)
        self.trailing_percent = pm.get("trailing_percent", 30)

        # Per ciclu
        self.cycle_target = pm.get("cycle_target_eur", 100)
        self.cycle_pause_seconds = pm.get("cycle_pause_seconds", 300)

        # Per zi
        self.max_daily_profit = pm.get("max_daily_profit_eur", 300)
        self.max_daily_loss = pm.get("max_daily_loss_eur", 53)

        # State
        self._peak_profit: dict[int, float] = {}  # ticket -> max profit atins
        self._trailing_active: dict[int, bool] = {}  # ticket -> trailing activ
        self._daily_realized: float = 0.0
        self._cycle_count: int = 0
        self._current_date: date = date.today()
        self._cycle_paused_until: float = 0  # timestamp cand se termina pauza

        logger.info(
            f"ProfitManager activ | Trailing: activare +{self.trailing_activation} EUR, "
            f"trail {self.trailing_percent}% | Ciclu target: +{self.cycle_target} EUR | "
            f"Daily: +{self.max_daily_profit} / -{self.max_daily_loss} EUR"
        )

    def _reset_daily_if_needed(self):
        """Reset zilnic."""
        today = date.today()
        if today != self._current_date:
            logger.info(
                f"Zi noua ({today}) — reset. Ieri: {self._cycle_count} cicluri, "
                f"PnL realizat: {self._daily_realized:+.2f} EUR"
            )
            self._daily_realized = 0.0
            self._cycle_count = 0
            self._current_date = today
            self._peak_profit.clear()
            self._trailing_active.clear()
            self._cycle_paused_until = 0

    def is_cycle_paused(self) -> tuple[bool, str]:
        """Verifica daca suntem in pauza intre cicluri."""
        self._reset_daily_if_needed()

        if time.time() < self._cycle_paused_until:
            remaining = int(self._cycle_paused_until - time.time())
            return True, f"Pauza intre cicluri ({remaining}s ramase)"
        return False, ""

    def is_daily_limit_reached(self) -> tuple[bool, str]:
        """Verifica daca limita zilnica e atinsa."""
        self._reset_daily_if_needed()

        if self._daily_realized >= self.max_daily_profit:
            return True, f"Profit zilnic maxim atins: +{self._daily_realized:.2f} EUR"
        if self._daily_realized <= -self.max_daily_loss:
            return True, f"Pierdere zilnica maxima atinsa: {self._daily_realized:.2f} EUR"
        return False, ""

    def check_position_trailing(self, ticket: int, current_profit: float) -> bool:
        """Verifica daca o pozitie trebuie inchisa pe trailing profit.

        Args:
            ticket: Ticket-ul pozitiei
            current_profit: Profitul curent in EUR/USD

        Returns:
            True daca pozitia trebuie inchisa
        """
        # Actualizeaza peak profit
        prev_peak = self._peak_profit.get(ticket, 0)
        if current_profit > prev_peak:
            self._peak_profit[ticket] = current_profit

        peak = self._peak_profit.get(ticket, 0)

        # Activeaza trailing daca profitul a atins pragul
        if current_profit >= self.trailing_activation and not self._trailing_active.get(ticket):
            self._trailing_active[ticket] = True
            logger.info(
                f"Trailing ACTIVAT pe #{ticket} — profit: +{current_profit:.2f} EUR "
                f"(prag: +{self.trailing_activation} EUR)"
            )

        # Daca trailing e activ, verifica daca profitul a scazut prea mult
        if self._trailing_active.get(ticket) and peak > 0:
            trail_level = peak * (1 - self.trailing_percent / 100)
            if current_profit <= trail_level:
                logger.info(
                    f"TRAILING CLOSE #{ticket} — profit: +{current_profit:.2f} EUR "
                    f"(peak: +{peak:.2f}, trail level: +{trail_level:.2f})"
                )
                return True

        return False

    def check_cycle_target(self, total_unrealized_profit: float) -> bool:
        """Verifica daca profitul total deschis a atins target-ul ciclului.

        Args:
            total_unrealized_profit: Suma profiturilor tuturor pozitiilor deschise

        Returns:
            True daca trebuie inchise toate pozitiile (ciclu complet)
        """
        if total_unrealized_profit >= self.cycle_target:
            logger.info(
                f"CICLU COMPLET — profit total: +{total_unrealized_profit:.2f} EUR "
                f"(target: +{self.cycle_target} EUR)"
            )
            return True
        return False

    def record_cycle_close(self, realized_profit: float):
        """Inregistreaza inchiderea unui ciclu."""
        self._daily_realized += realized_profit
        self._cycle_count += 1
        self._cycle_paused_until = time.time() + self.cycle_pause_seconds
        self._peak_profit.clear()
        self._trailing_active.clear()

        logger.info(
            f"Ciclu #{self._cycle_count} inchis — profit ciclu: +{realized_profit:.2f} EUR | "
            f"Total zi: {self._daily_realized:+.2f} EUR | "
            f"Pauza {self.cycle_pause_seconds}s"
        )

    def record_position_close(self, ticket: int, profit: float):
        """Inregistreaza inchiderea unei pozitii individuale (trailing)."""
        self._daily_realized += profit
        self._peak_profit.pop(ticket, None)
        self._trailing_active.pop(ticket, None)

        logger.info(
            f"Pozitie #{ticket} inchisa trailing — profit: {profit:+.2f} EUR | "
            f"Total zi: {self._daily_realized:+.2f} EUR"
        )

    def get_daily_stats(self) -> dict:
        """Statistici zilnice."""
        self._reset_daily_if_needed()
        return {
            "realized_pnl": self._daily_realized,
            "cycles": self._cycle_count,
            "date": self._current_date.isoformat(),
        }
