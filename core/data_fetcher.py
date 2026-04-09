"""
Descarcarea datelor OHLCV de la MetaTrader 5.
"""

from datetime import datetime

import MetaTrader5 as mt5
import pandas as pd

from utils.logger import setup_logger

logger = setup_logger("data_fetcher")

# Mapare timeframe string -> constanta MT5
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


class DataFetcher:
    """Descarca date OHLCV pentru orice simbol si timeframe."""

    def get_rates(
        self, symbol: str, timeframe: str = "H1", bars: int = 500
    ) -> pd.DataFrame | None:
        """Descarca ultimele N bare OHLCV.

        Args:
            symbol: Simbolul (ex: "EURUSD")
            timeframe: Timeframe-ul (ex: "H1", "M5", "D1")
            bars: Numarul de bare de descarcat

        Returns:
            DataFrame cu coloanele: time, open, high, low, close, tick_volume, spread
        """
        tf = TIMEFRAME_MAP.get(timeframe.upper())
        if tf is None:
            logger.error(f"Timeframe invalid: {timeframe}. Valide: {list(TIMEFRAME_MAP.keys())}")
            return None

        # Asigura-te ca simbolul e selectat
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Nu pot selecta simbolul {symbol}")
            return None

        rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            logger.error(f"Nu am primit date pentru {symbol} {timeframe}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(
            columns={"tick_volume": "volume", "real_volume": "real_volume"},
            inplace=True,
        )

        logger.debug(f"Descarcat {len(df)} bare {symbol} {timeframe}")
        return df

    def get_rates_range(
        self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime
    ) -> pd.DataFrame | None:
        """Descarca date OHLCV intr-un interval de timp specific."""
        tf = TIMEFRAME_MAP.get(timeframe.upper())
        if tf is None:
            logger.error(f"Timeframe invalid: {timeframe}")
            return None

        if not mt5.symbol_select(symbol, True):
            logger.error(f"Nu pot selecta simbolul {symbol}")
            return None

        rates = mt5.copy_rates_range(symbol, tf, date_from, date_to)
        if rates is None or len(rates) == 0:
            logger.error(f"Nu am primit date range pentru {symbol}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)

        logger.debug(f"Descarcat {len(df)} bare {symbol} {timeframe} ({date_from} - {date_to})")
        return df

    def get_tick_data(self, symbol: str, count: int = 100) -> pd.DataFrame | None:
        """Descarca ultimele N tick-uri."""
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Nu pot selecta simbolul {symbol}")
            return None

        ticks = mt5.copy_ticks_from_pos(symbol, 0, count, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            logger.error(f"Nu am primit tick-uri pentru {symbol}: {mt5.last_error()}")
            return None

        df = pd.DataFrame(ticks)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df

    def get_current_price(self, symbol: str) -> dict | None:
        """Returneaza pretul curent bid/ask."""
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Nu pot obtine pretul pentru {symbol}")
            return None
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "time": datetime.fromtimestamp(tick.time),
        }
