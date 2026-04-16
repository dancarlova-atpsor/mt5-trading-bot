"""
Modul de conexiune la MetaTrader 5.
Gestioneaza login, initializare si shutdown.
"""

import MetaTrader5 as mt5

from utils.logger import setup_logger
from utils.helpers import load_config, get_mt5_password

logger = setup_logger("mt5_connector")


class MT5Connector:
    """Gestioneaza conexiunea cu terminalul MetaTrader 5."""

    def __init__(self, config_path: str = "settings.yaml"):
        self.config = load_config(config_path)
        self.account_config = self.config["account"]
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Initializeaza MT5 si face login pe cont.

        Daca terminalul e deja logat pe contul nostru (parola salvata), saltam peste
        mt5.login() explicit — nu mai cere parola din env.

        Incearca 2 cai:
        1. mt5.initialize() — attach la terminalul default (functioneaza cand exista un singur terminal)
        2. mt5.initialize(path=...) — path explicit catre executabilul terminalului (fallback)
        """
        logger.info("Initializez MetaTrader 5...")

        import os
        account = self.account_config["number"]
        server = self.account_config["server"]
        terminal_path = self.account_config.get("terminal_path") or os.environ.get(
            "MT5_TERMINAL_PATH",
            r"C:\Program Files\Earn Broker MT5 Terminal\terminal64.exe",
        )

        # Incercam 3 strategii de initializare, in ordine:
        attempts = [
            ("default (running terminal)", {}),
            ("path explicit", {"path": terminal_path} if os.path.exists(terminal_path) else None),
            ("path + login + server (saved password)", {
                "path": terminal_path, "login": account, "server": server, "timeout": 60000
            } if os.path.exists(terminal_path) else None),
        ]

        initialized = False
        for label, kwargs in attempts:
            if kwargs is None:
                continue
            logger.info(f"Incerc: {label}")
            try:
                if mt5.initialize(**kwargs):
                    initialized = True
                    logger.info(f"MT5 initialize OK via: {label}")
                    break
                else:
                    logger.warning(f"  → esuat: {mt5.last_error()}")
            except Exception as e:
                logger.warning(f"  → exceptie: {e}")

        if not initialized:
            logger.error(
                f"MT5 initialize() a esuat prin toate metodele. Ultima eroare: {mt5.last_error()}\n"
                f"Verifica:\n"
                f"  1. MT5 terminal ruleaza si e logat pe {account}\n"
                f"  2. Unelte → Optiuni → Expert Advisors → 'Permite tranzactionare algoritmica' BIFAT\n"
                f"  3. Daca MT5 ruleaza ca Administrator, porneste Python tot ca Admin (PowerShell Run as Admin)"
            )
            return False

        account = self.account_config["number"]
        server = self.account_config["server"]

        # Verifica daca terminalul e deja logat pe contul dorit
        current = mt5.account_info()
        if current is not None and current.login == account:
            logger.info(f"Terminal deja logat pe contul {account} — skip login explicit")
        else:
            # Altfel, incearca login explicit (cere parola din env / config)
            try:
                password = self.account_config.get("password") or get_mt5_password()
            except EnvironmentError as e:
                logger.error(
                    f"Terminal NU e logat pe {account} si nu am parola: {e}\n"
                    f"Solutii: (1) logheaza-te manual in MT5 pe {account} cu Salveaza parola, "
                    f"sau (2) seteaza $env:MT5_PASSWORD"
                )
                mt5.shutdown()
                return False

            logger.info(f"Login pe contul {account} @ {server}...")
            if not mt5.login(account, password=password, server=server):
                logger.error(f"Login esuat: {mt5.last_error()}")
                mt5.shutdown()
                return False

        account_info = mt5.account_info()
        if account_info is None:
            logger.error("Nu pot obtine informatii despre cont.")
            mt5.shutdown()
            return False

        if account_info.login != account:
            logger.error(
                f"Terminal e pe alt cont ({account_info.login}), nu {account}. "
                f"Logheaza-te manual in MT5 pe contul corect si reporneste."
            )
            mt5.shutdown()
            return False

        self._connected = True
        mode = "DEMO" if account_info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"
        logger.info(
            f"Conectat! Mod: {mode} | "
            f"Balanta: {account_info.balance} {account_info.currency} | "
            f"Leverage: 1:{account_info.leverage}"
        )
        return True

    def disconnect(self):
        """Inchide conexiunea MT5."""
        if self._connected:
            mt5.shutdown()
            self._connected = False
            logger.info("Deconectat de la MT5.")

    def get_account_info(self) -> dict | None:
        """Returneaza informatii despre cont ca dict."""
        if not self._connected:
            logger.warning("Nu sunt conectat la MT5.")
            return None

        info = mt5.account_info()
        if info is None:
            return None

        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "profit": info.profit,
            "currency": info.currency,
            "leverage": info.leverage,
            "trade_mode": info.trade_mode,
        }

    def get_symbol_info(self, symbol: str) -> dict | None:
        """Returneaza informatii despre un simbol."""
        if not self._connected:
            return None

        info = mt5.symbol_info(symbol)
        if info is None:
            logger.warning(f"Simbolul {symbol} nu exista sau nu e vizibil.")
            return None

        # Activeaza simbolul daca nu e vizibil
        if not info.visible:
            mt5.symbol_select(symbol, True)

        return {
            "symbol": info.name,
            "bid": info.bid,
            "ask": info.ask,
            "spread": info.spread,
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
        }

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
