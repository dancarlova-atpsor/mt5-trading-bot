"""
Strategie de revenire la medie — RSI si Bollinger Bands.
Semnaleaza BUY cand pretul e supravandut, SELL cand e supracumparat.
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("mean_reversion")


class MeanReversion(BaseStrategy):
    """Mean Reversion cu RSI si Bollinger Bands."""

    def __init__(self, params: dict):
        super().__init__("MeanReversion", params)

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calculeaza RSI si Bollinger Bands."""
        df = data.copy()

        # RSI
        df["rsi"] = ta.rsi(df["close"], length=self.params["rsi_period"])

        # Bollinger Bands
        bbands_df = ta.bbands(
            df["close"],
            length=self.params["bb_period"],
            std=self.params["bb_std_dev"],
        )
        if bbands_df is not None:
            df["bb_lower"] = bbands_df.iloc[:, 0]   # BBL
            df["bb_middle"] = bbands_df.iloc[:, 1]  # BBM
            df["bb_upper"] = bbands_df.iloc[:, 2]   # BBU
        else:
            df["bb_lower"] = float("nan")
            df["bb_middle"] = float("nan")
            df["bb_upper"] = float("nan")

        return df

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Analizeaza datele si returneaza semnalul de trading."""
        df = self.calculate_indicators(data)
        last = df.iloc[-1]

        # Verificare NaN
        if last[["rsi", "bb_lower", "bb_upper"]].isna().any():
            return Signal.HOLD

        # BUY: RSI supravandut + pretul sub BB lower
        if last["rsi"] < self.params["rsi_oversold"] and last["close"] < last["bb_lower"]:
            logger.info(
                f"SEMNAL BUY — RSI={last['rsi']:.1f}, "
                f"Close={last['close']:.5f}, BB_lower={last['bb_lower']:.5f}"
            )
            return Signal.BUY

        # SELL: RSI supracumparat + pretul peste BB upper
        if last["rsi"] > self.params["rsi_overbought"] and last["close"] > last["bb_upper"]:
            logger.info(
                f"SEMNAL SELL — RSI={last['rsi']:.1f}, "
                f"Close={last['close']:.5f}, BB_upper={last['bb_upper']:.5f}"
            )
            return Signal.SELL

        return Signal.HOLD
