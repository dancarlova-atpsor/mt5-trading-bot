"""
Strategie MACD Divergence — detecteaza divergente intre pret si MACD.
Divergenta bullish: pretul face low mai mic, MACD face low mai mare -> BUY
Divergenta bearish: pretul face high mai mare, MACD face high mai mic -> SELL
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("macd_divergence")


class MACDDivergence(BaseStrategy):
    """MACD Divergence — tranzactioneaza pe divergente pret/MACD."""

    def __init__(self, params: dict):
        super().__init__("MACDDivergence", params)

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calculeaza MACD si detecteaza pivot points."""
        df = data.copy()

        # MACD
        macd_df = ta.macd(
            df["close"],
            fast=self.params.get("macd_fast", 12),
            slow=self.params.get("macd_slow", 26),
            signal=self.params.get("macd_signal", 9),
        )
        if macd_df is not None:
            macd_col = [c for c in macd_df.columns if "MACD_" in c and "MACDh" not in c and "MACDs" not in c]
            hist_col = [c for c in macd_df.columns if "MACDh" in c]
            signal_col = [c for c in macd_df.columns if "MACDs" in c]
            df["macd_line"] = macd_df[macd_col[0]] if macd_col else macd_df.iloc[:, 0]
            df["macd_hist"] = macd_df[hist_col[0]] if hist_col else macd_df.iloc[:, 1]
            df["macd_signal"] = macd_df[signal_col[0]] if signal_col else macd_df.iloc[:, 2]
        else:
            df["macd_line"] = float("nan")
            df["macd_hist"] = float("nan")
            df["macd_signal"] = float("nan")

        # RSI ca confirmare
        df["rsi"] = ta.rsi(df["close"], length=self.params.get("rsi_period", 14))

        return df

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Detecteaza divergente MACD si genereaza semnale."""
        df = self.calculate_indicators(data)

        lookback = self.params.get("divergence_lookback", 30)
        if len(df) < lookback + 10:
            return Signal.HOLD

        window = df.iloc[-lookback:]
        last = df.iloc[-1]

        if last[["macd_hist", "rsi"]].isna().any():
            return Signal.HOLD

        # Gaseste ultimele 2 swing lows si swing highs in pret si MACD
        bullish_div = self._check_bullish_divergence(window)
        bearish_div = self._check_bearish_divergence(window)

        rsi_confirm_buy = last["rsi"] < self.params.get("rsi_oversold", 40)
        rsi_confirm_sell = last["rsi"] > self.params.get("rsi_overbought", 60)

        if bullish_div and rsi_confirm_buy:
            logger.info(
                f"SEMNAL BUY (MACD divergence) — RSI={last['rsi']:.1f}, "
                f"MACD_hist={last['macd_hist']:.5f}"
            )
            return Signal.BUY

        if bearish_div and rsi_confirm_sell:
            logger.info(
                f"SEMNAL SELL (MACD divergence) — RSI={last['rsi']:.1f}, "
                f"MACD_hist={last['macd_hist']:.5f}"
            )
            return Signal.SELL

        return Signal.HOLD

    def _check_bullish_divergence(self, window: pd.DataFrame) -> bool:
        """Divergenta bullish: pret face lower low, MACD face higher low."""
        lows = self._find_swing_lows(window["close"], order=5)
        macd_lows = self._find_swing_lows(window["macd_hist"], order=5)

        if len(lows) < 2 or len(macd_lows) < 2:
            return False

        # Ultimele 2 swing lows
        price_low1 = window["close"].iloc[lows[-2]]
        price_low2 = window["close"].iloc[lows[-1]]
        macd_low1 = window["macd_hist"].iloc[macd_lows[-2]]
        macd_low2 = window["macd_hist"].iloc[macd_lows[-1]]

        # Pret: lower low, MACD: higher low
        return price_low2 < price_low1 and macd_low2 > macd_low1

    def _check_bearish_divergence(self, window: pd.DataFrame) -> bool:
        """Divergenta bearish: pret face higher high, MACD face lower high."""
        highs = self._find_swing_highs(window["close"], order=5)
        macd_highs = self._find_swing_highs(window["macd_hist"], order=5)

        if len(highs) < 2 or len(macd_highs) < 2:
            return False

        price_high1 = window["close"].iloc[highs[-2]]
        price_high2 = window["close"].iloc[highs[-1]]
        macd_high1 = window["macd_hist"].iloc[macd_highs[-2]]
        macd_high2 = window["macd_hist"].iloc[macd_highs[-1]]

        # Pret: higher high, MACD: lower high
        return price_high2 > price_high1 and macd_high2 < macd_high1

    @staticmethod
    def _find_swing_lows(series: pd.Series, order: int = 5) -> list[int]:
        """Gaseste indicii swing lows (minim local)."""
        lows = []
        values = series.values
        for i in range(order, len(values) - order):
            if all(values[i] <= values[i - j] for j in range(1, order + 1)) and \
               all(values[i] <= values[i + j] for j in range(1, order + 1)):
                lows.append(i)
        return lows

    @staticmethod
    def _find_swing_highs(series: pd.Series, order: int = 5) -> list[int]:
        """Gaseste indicii swing highs (maxim local)."""
        highs = []
        values = series.values
        for i in range(order, len(values) - order):
            if all(values[i] >= values[i - j] for j in range(1, order + 1)) and \
               all(values[i] >= values[i + j] for j in range(1, order + 1)):
                highs.append(i)
        return highs
