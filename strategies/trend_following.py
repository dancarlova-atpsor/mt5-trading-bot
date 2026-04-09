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
    """Trend Following cu EMA, MACD si ADX."""

    def __init__(self, params: dict):
        super().__init__("TrendFollowing", params)

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calculeaza EMA fast/slow, MACD histogram, ADX."""
        df = data.copy()

        # EMA
        df["ema_fast"] = ta.ema(df["close"], length=self.params["ema_fast"])
        df["ema_slow"] = ta.ema(df["close"], length=self.params["ema_slow"])

        # MACD
        macd_df = ta.macd(
            df["close"],
            fast=self.params["macd_fast"],
            slow=self.params["macd_slow"],
            signal=self.params["macd_signal"],
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
            length=self.params["adx_period"],
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

        adx_strong = last["adx"] > self.params["adx_threshold"]

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
