"""
Strategie de scalping — Stochastic crossover si VWAP.
Semnale rapide pe timeframe mic (M5), cu verificare spread.
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("scalping")


class Scalping(BaseStrategy):
    """Scalping cu Stochastic si VWAP."""

    def __init__(self, params: dict):
        super().__init__("Scalping", params)
        self._current_spread: int = 0

    def set_current_spread(self, spread: int):
        """Seteaza spread-ul curent (apelat din main loop inainte de analyze)."""
        self._current_spread = spread

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calculeaza Stochastic si VWAP."""
        df = data.copy()

        # Stochastic
        stoch_df = ta.stoch(
            df["high"], df["low"], df["close"],
            k=self.params["stochastic_k"],
            d=self.params["stochastic_d"],
            smooth_k=self.params["stochastic_smooth"],
        )
        if stoch_df is not None:
            df["stoch_k"] = stoch_df.iloc[:, 0]
            df["stoch_d"] = stoch_df.iloc[:, 1]
        else:
            df["stoch_k"] = float("nan")
            df["stoch_d"] = float("nan")

        # Stoch previous (pentru crossover)
        df["stoch_k_prev"] = df["stoch_k"].shift(1)
        df["stoch_d_prev"] = df["stoch_d"].shift(1)

        # VWAP — rolling cu tick_volume
        vol_col = "tick_volume" if "tick_volume" in df.columns else "volume"
        if vol_col in df.columns and df[vol_col].sum() > 0:
            period = self.params["vwap_period"]
            typical_price = (df["high"] + df["low"] + df["close"]) / 3
            cum_tp_vol = (typical_price * df[vol_col]).rolling(period).sum()
            cum_vol = df[vol_col].rolling(period).sum()
            df["vwap"] = cum_tp_vol / cum_vol
        else:
            # Fallback: SMA daca nu avem volum
            df["vwap"] = ta.sma(df["close"], length=self.params["vwap_period"])

        return df

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Analizeaza datele si returneaza semnalul de trading."""
        # Verificare spread
        if self._current_spread > self.params["max_spread_points"]:
            logger.debug(
                f"Spread prea mare: {self._current_spread} > {self.params['max_spread_points']}"
            )
            return Signal.HOLD

        df = self.calculate_indicators(data)
        last = df.iloc[-1]

        # Verificare NaN
        cols = ["stoch_k", "stoch_d", "stoch_k_prev", "stoch_d_prev", "vwap"]
        if last[cols].isna().any():
            return Signal.HOLD

        oversold = self.params["stochastic_oversold"]
        overbought = self.params["stochastic_overbought"]

        # BUY: Stoch K < oversold, crossover K peste D, pret > VWAP
        if (
            last["stoch_k"] < oversold
            and last["stoch_k_prev"] < last["stoch_d_prev"]  # era sub D
            and last["stoch_k"] > last["stoch_d"]             # acum e peste D
            and last["close"] > last["vwap"]
        ):
            logger.info(
                f"SEMNAL BUY — Stoch_K={last['stoch_k']:.1f}, "
                f"Stoch_D={last['stoch_d']:.1f}, VWAP={last['vwap']:.5f}"
            )
            return Signal.BUY

        # SELL: Stoch K > overbought, crossunder K sub D, pret < VWAP
        if (
            last["stoch_k"] > overbought
            and last["stoch_k_prev"] > last["stoch_d_prev"]  # era peste D
            and last["stoch_k"] < last["stoch_d"]             # acum e sub D
            and last["close"] < last["vwap"]
        ):
            logger.info(
                f"SEMNAL SELL — Stoch_K={last['stoch_k']:.1f}, "
                f"Stoch_D={last['stoch_d']:.1f}, VWAP={last['vwap']:.5f}"
            )
            return Signal.SELL

        return Signal.HOLD
