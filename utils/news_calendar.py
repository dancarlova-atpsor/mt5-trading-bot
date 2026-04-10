"""
Calendar Economic — detecteaza stiri cu impact mare si activeaza blackout trading.
Sursa: Forex Factory (scraping HTML).
"""

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from utils.logger import setup_logger
from utils.helpers import load_config

logger = setup_logger("news_calendar")

# Mapare moneda -> simboluri afectate
CURRENCY_SYMBOLS = {
    "USD": ["EURUSD", "GBPUSD", "XAUUSD", "XAGUSD", "USDJPY", "AUDUSD", "US500", "US30"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURCAD", "EURCHF"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY"],
    "AUD": ["AUDUSD", "AUDJPY"],
    "CAD": ["USDCAD", "EURCAD", "CADJPY"],
    "CHF": ["USDCHF", "EURCHF", "CHFJPY"],
    "NZD": ["NZDUSD"],
}


class NewsCalendar:
    """Monitorizare calendar economic cu blackout automat."""

    def __init__(self):
        config = load_config("settings.yaml")
        news_config = config.get("news", {})

        self.enabled = news_config.get("calendar_enabled", True)
        self.blackout_before = news_config.get("blackout_minutes_before", 30)
        self.blackout_after = news_config.get("blackout_minutes_after", 30)
        self.high_impact_only = news_config.get("high_impact_only", True)

        # Cache
        self._events_cache: list[dict] = []
        self._last_fetch: float = 0
        self._cache_ttl: int = 3600  # 1 ora

        if self.enabled:
            logger.info(
                f"News Calendar activ | Blackout: {self.blackout_before}min inainte / "
                f"{self.blackout_after}min dupa | High impact only: {self.high_impact_only}"
            )

    def get_upcoming_events(self, hours_ahead: int = 24) -> list[dict]:
        """Returneaza evenimentele economice din urmatoarele ore."""
        if not self.enabled:
            return []

        self._refresh_cache()

        now = datetime.now(ZoneInfo("UTC"))
        cutoff = now + timedelta(hours=hours_ahead)

        upcoming = []
        for event in self._events_cache:
            event_time = event.get("datetime")
            if event_time and now <= event_time <= cutoff:
                if self.high_impact_only and event.get("impact") != "high":
                    continue
                upcoming.append(event)

        return upcoming

    def is_news_blackout(self, symbol: str) -> tuple[bool, str]:
        """Verifica daca un simbol e in blackout din cauza stirilor.

        Returns:
            (is_blackout, reason) — ex: (True, "NFP in 25 min")
        """
        if not self.enabled:
            return False, ""

        self._refresh_cache()

        now = datetime.now(ZoneInfo("UTC"))
        symbol_clean = symbol.upper().replace(".CFD", "")

        for event in self._events_cache:
            event_time = event.get("datetime")
            if event_time is None:
                continue

            if self.high_impact_only and event.get("impact") != "high":
                continue

            # Verifica daca simbolul e afectat
            affected = self._get_affected_symbols(event.get("currency", ""))
            symbol_affected = any(s in symbol_clean for s in affected)
            if not symbol_affected:
                continue

            # Verifica fereastra de blackout
            blackout_start = event_time - timedelta(minutes=self.blackout_before)
            blackout_end = event_time + timedelta(minutes=self.blackout_after)

            if blackout_start <= now <= blackout_end:
                minutes_to = int((event_time - now).total_seconds() / 60)
                if minutes_to > 0:
                    reason = f"{event.get('title', 'Eveniment')} in {minutes_to} min"
                else:
                    reason = f"{event.get('title', 'Eveniment')} acum {abs(minutes_to)} min in urma"
                return True, reason

        return False, ""

    def _get_affected_symbols(self, currency: str) -> list[str]:
        """Returneaza simbolurile afectate de o moneda."""
        return CURRENCY_SYMBOLS.get(currency.upper(), [])

    def _refresh_cache(self):
        """Reimprospaateaza cache-ul daca e expirat."""
        if time.time() - self._last_fetch < self._cache_ttl:
            return

        try:
            events = self._fetch_forex_factory()
            # Cache mereu, chiar si cu lista goala (evita spam la fiecare apel)
            self._events_cache = events
            self._last_fetch = time.time()
            if events:
                logger.info(f"Calendar actualizat: {len(events)} evenimente")
            else:
                logger.info("Calendar gol — folosesc cache pana la urmatoarea verificare (1h)")
        except Exception as e:
            logger.error(f"Eroare la actualizare calendar: {e}")
            # Chiar si la eroare, update last_fetch ca sa nu spam-uim
            self._last_fetch = time.time()

    def _fetch_forex_factory(self) -> list[dict]:
        """Scrapeaza calendarul de pe Forex Factory."""
        url = "https://www.forexfactory.com/calendar"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"Nu pot accesa Forex Factory: {e}")
            return self._get_fallback_events()

        soup = BeautifulSoup(response.text, "html.parser")
        events = []

        rows = soup.select("tr.calendar__row")
        current_date = datetime.now(ZoneInfo("UTC")).date()

        for row in rows:
            try:
                # Impact
                impact_el = row.select_one(".calendar__impact span")
                if impact_el:
                    impact_class = " ".join(impact_el.get("class", []))
                    if "high" in impact_class:
                        impact = "high"
                    elif "medium" in impact_class:
                        impact = "medium"
                    else:
                        impact = "low"
                else:
                    continue

                # Moneda
                currency_el = row.select_one(".calendar__currency")
                currency = currency_el.get_text(strip=True) if currency_el else ""

                # Titlu
                title_el = row.select_one(".calendar__event-title")
                title = title_el.get_text(strip=True) if title_el else ""

                # Ora
                time_el = row.select_one(".calendar__time")
                time_str = time_el.get_text(strip=True) if time_el else ""

                event_dt = None
                if time_str and ":" in time_str:
                    try:
                        # Forex Factory foloseste EST
                        time_str_clean = time_str.replace("am", " AM").replace("pm", " PM")
                        est = ZoneInfo("America/New_York")
                        event_dt = datetime.combine(
                            current_date,
                            datetime.strptime(time_str_clean, "%I:%M %p").time(),
                        ).replace(tzinfo=est).astimezone(ZoneInfo("UTC"))
                    except ValueError:
                        pass

                if title and currency:
                    events.append({
                        "title": title,
                        "currency": currency,
                        "impact": impact,
                        "datetime": event_dt,
                        "time_str": time_str,
                    })

            except Exception:
                continue

        return events

    def _get_fallback_events(self) -> list[dict]:
        """Evenimente recurente cunoscute ca fallback cand scraping-ul nu merge."""
        now = datetime.now(ZoneInfo("UTC"))
        today = now.date()
        weekday = today.weekday()  # 0=Mon

        events = []

        # NFP — primul vineri din luna, 13:30 UTC
        if weekday == 4 and today.day <= 7:
            nfp_time = datetime.combine(today, datetime.strptime("13:30", "%H:%M").time())
            nfp_time = nfp_time.replace(tzinfo=ZoneInfo("UTC"))
            events.append({
                "title": "Non-Farm Payrolls",
                "currency": "USD",
                "impact": "high",
                "datetime": nfp_time,
            })

        # FOMC — de obicei miercuri, 19:00 UTC (8 ori pe an)
        if weekday == 2:
            fomc_time = datetime.combine(today, datetime.strptime("19:00", "%H:%M").time())
            fomc_time = fomc_time.replace(tzinfo=ZoneInfo("UTC"))
            events.append({
                "title": "FOMC Statement (potential)",
                "currency": "USD",
                "impact": "high",
                "datetime": fomc_time,
            })

        # ECB — de obicei joi, 13:15 UTC
        if weekday == 3:
            ecb_time = datetime.combine(today, datetime.strptime("13:15", "%H:%M").time())
            ecb_time = ecb_time.replace(tzinfo=ZoneInfo("UTC"))
            events.append({
                "title": "ECB Rate Decision (potential)",
                "currency": "EUR",
                "impact": "high",
                "datetime": ecb_time,
            })

        return events
