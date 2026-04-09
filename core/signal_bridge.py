"""
Signal Bridge — comunicare Python ↔ MQL5 EA prin fisiere partajate.
Python scrie signal.json, EA citeste si executa, apoi scrie status.json.
"""

import json
import os
import time
from pathlib import Path

from utils.logger import setup_logger

logger = setup_logger("signal_bridge")

# Numele fisierelor de comunicare
SIGNAL_FILE = "python_signal.json"
STATUS_FILE = "ea_status.json"


class SignalBridge:
    """Interfata de comunicare cu MQL5 EA prin fisiere partajate."""

    def __init__(self, mt5_files_path: str = None):
        """
        Args:
            mt5_files_path: Calea catre directorul MQL5/Files/ al terminalului MT5.
                           Daca None, incearca auto-detectare.
        """
        self.files_path = self._resolve_path(mt5_files_path)
        logger.info(f"Signal bridge initializat: {self.files_path}")

    def _resolve_path(self, custom_path: str = None) -> Path:
        """Determina calea catre MQL5/Files/."""
        if custom_path:
            p = Path(custom_path)
            p.mkdir(parents=True, exist_ok=True)
            return p

        # Auto-detectare: cauta in locatiile standard MT5 pe Windows
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            mt5_base = Path(appdata) / "MetaQuotes" / "Terminal"
            if mt5_base.exists():
                # Cauta primul terminal cu directorul MQL5/Files
                for terminal_dir in mt5_base.iterdir():
                    files_dir = terminal_dir / "MQL5" / "Files"
                    if files_dir.exists():
                        logger.info(f"Auto-detectat MQL5/Files: {files_dir}")
                        return files_dir

        # Fallback: directorul local al proiectului
        fallback = Path(__file__).parent.parent / "mql5" / "files"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(f"Nu am gasit MQL5/Files, folosesc fallback: {fallback}")
        return fallback

    @property
    def signal_file(self) -> Path:
        return self.files_path / SIGNAL_FILE

    @property
    def status_file(self) -> Path:
        return self.files_path / STATUS_FILE

    def write_signal(
        self,
        symbol: str,
        action: str,
        volume: float,
        sl: float,
        tp: float,
        magic: int = 123456,
        comment: str = "",
    ) -> bool:
        """Scrie un semnal de trading pentru EA.

        Args:
            symbol: Simbolul (ex: "EURUSD")
            action: "BUY" sau "SELL"
            volume: Lot size
            sl: Stop Loss price
            tp: Take Profit price
            magic: Magic number
            comment: Comentariu (numele strategiei)

        Returns:
            True daca semnalul a fost scris cu succes
        """
        signal = {
            "timestamp": int(time.time()),
            "symbol": symbol,
            "action": action.upper(),
            "volume": volume,
            "sl": sl,
            "tp": tp,
            "magic": magic,
            "comment": comment,
        }

        try:
            with open(self.signal_file, "w", encoding="utf-8") as f:
                json.dump(signal, f, indent=2)
            logger.info(f"Semnal scris: {action} {volume} {symbol} SL={sl:.5f} TP={tp:.5f}")
            return True
        except OSError as e:
            logger.error(f"Eroare la scriere signal.json: {e}")
            return False

    def read_status(self, max_age_seconds: int = 60) -> dict | None:
        """Citeste statusul ultimei executii de la EA.

        Args:
            max_age_seconds: Ignora statusuri mai vechi de X secunde

        Returns:
            Dict cu statusul sau None daca nu exista/e vechi
        """
        if not self.status_file.exists():
            return None

        try:
            with open(self.status_file, "r", encoding="utf-8") as f:
                status = json.load(f)

            # Verifica timestamp
            age = int(time.time()) - status.get("timestamp", 0)
            if age > max_age_seconds:
                logger.debug(f"Status prea vechi ({age}s > {max_age_seconds}s)")
                return None

            return status
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Eroare la citire status.json: {e}")
            return None

    def clear_signal(self):
        """Sterge fisierul de semnal dupa confirmare."""
        try:
            if self.signal_file.exists():
                self.signal_file.unlink()
                logger.debug("Signal file sters")
        except OSError as e:
            logger.warning(f"Nu pot sterge signal file: {e}")

    def clear_status(self):
        """Sterge fisierul de status."""
        try:
            if self.status_file.exists():
                self.status_file.unlink()
        except OSError as e:
            logger.warning(f"Nu pot sterge status file: {e}")

    def wait_for_execution(self, timeout_seconds: int = 30, poll_interval: float = 0.5) -> dict | None:
        """Asteapta confirmarea executiei de la EA.

        Args:
            timeout_seconds: Timp maxim de asteptare
            poll_interval: Interval de polling in secunde

        Returns:
            Dict cu statusul executiei sau None daca a expirat
        """
        start = time.time()
        signal_ts = None

        # Citeste timestamp-ul semnalului curent
        if self.signal_file.exists():
            try:
                with open(self.signal_file, "r", encoding="utf-8") as f:
                    sig = json.load(f)
                signal_ts = sig.get("timestamp")
            except (json.JSONDecodeError, OSError):
                pass

        while time.time() - start < timeout_seconds:
            status = self.read_status(max_age_seconds=timeout_seconds)
            if status is not None:
                # Verifica ca statusul e pentru semnalul nostru
                if signal_ts and status.get("timestamp", 0) >= signal_ts:
                    logger.info(f"Executie confirmata: ticket={status.get('ticket')}, status={status.get('status')}")
                    return status
            time.sleep(poll_interval)

        logger.warning(f"Timeout asteptare executie ({timeout_seconds}s)")
        return None
