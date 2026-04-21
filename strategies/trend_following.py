"""
Strategie de urmarire a trendului — EMA crossover, MACD, ADX.
Semnaleaza BUY cand trendul e puternic bullish, SELL cand e bearish.
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("trend_following")


class TrendFollowing(BaseStrategy):
    """Trend Following cu EMA, MACD si ADX. Suporta parametri per-simbol."""

    def __init__(self, params: dict):
        super().__init__("TrendFollowing", params)
        self._current_symbol = None
        self._symbol_overrides = params.get("symbol_overrides", {}) or {}

    def set_current_symbol(self, symbol: str):
        """Setare simbol curent pentru a aplica parametri specifici (override)."""
        self._current_symbol = symbol

    def _p(self, key: str):
        """Intoarce valoarea parametrului, cu override per simbol daca exista."""
        if self._current_symbol and self._current_symbol in self._symbol_overrides:
            override = self._symbol_overrides[self._current_symbol]
            if key in override:
                return override[key]
        return self.params[key]

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calculeaza EMA fast/slow, MACD histogram, ADX."""
        df = data.copy()

        # EMA
        df["ema_fast"] = ta.ema(df["close"], length=self._p("ema_fast"))
        df["ema_slow"] = ta.ema(df["close"], length=self._p("ema_slow"))

        # MACD
        macd_df = ta.macd(
            df["close"],
            fast=self._p("macd_fast"),
            slow=self._p("macd_slow"),
            signal=self._p("macd_signal"),
        )
        if macd_df is not None:
            # Coloana histogram contine "MACDh" in nume
            hist_col = [c for c in macd_df.columns if "MACDh" in c]
            df["macd_hist"] = macd_df[hist_col[0]] if hist_col else macd_df.iloc[:, 1]
        else:
            df["macd_hist"] = float("nan")

        # ADX
        adx_df = ta.adx(
            df["high"], df["low"], df["close"],
            length=self._p("adx_period"),
        )
        if adx_df is not None:
            df["adx"] = adx_df.iloc[:, 0]  # ADX
        else:
            df["adx"] = float("nan")

        return df

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Analizeaza datele si returneaza semnalul de trading."""
        df = self.calculate_indicators(data)
        last = df.iloc[-1]

        # Verificare NaN — nu avem suficiente bare
        if last[["ema_fast", "ema_slow", "macd_hist", "adx"]].isna().any():
            return Signal.HOLD

        adx_strong = last["adx"] > self._p("adx_threshold")

        # BUY: pret > EMA slow, EMA fast > EMA slow, MACD hist > 0, ADX puternic
        if (
            last["close"] > last["ema_slow"]
            and last["ema_fast"] > last["ema_slow"]
            and last["macd_hist"] > 0
            and adx_strong
        ):
            logger.info(
                f"SEMNAL BUY — EMA_fast={last['ema_fast']:.5f}, "
                f"EMA_slow={last['ema_slow']:.5f}, MACD_hist={last['macd_hist']:.5f}, "
                f"ADX={last['adx']:.1f}"
            )
            return Signal.BUY

        # SELL: pret < EMA slow, EMA fast < EMA slow, MACD hist < 0, ADX puternic
        if (
            last["close"] < last["ema_slow"]
            and last["ema_fast"] < last["ema_slow"]
            and last["macd_hist"] < 0
            and adx_strong
        ):
            logger.info(
                f"SEMNAL SELL — EMA_fast={last['ema_fast']:.5f}, "
                f"EMA_slow={last['ema_slow']:.5f}, MACD_hist={last['macd_hist']:.5f}, "
                f"ADX={last['adx']:.1f}"
            )
            return Signal.SELL

        return Signal.HOLD
