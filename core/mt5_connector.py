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
        """Initializeaza MT5 si face login pe cont."""
        logger.info("Initializez MetaTrader 5...")

        if not mt5.initialize():
            logger.error(f"MT5 initialize() a esuat: {mt5.last_error()}")
            return False

        account = self.account_config["number"]
        server = self.account_config["server"]
        password = self.account_config.get("password") or get_mt5_password()

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
