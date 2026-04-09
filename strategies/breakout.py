"""
Strategie Breakout — detecteaza spargerea nivelurilor de suport/rezistenta.
BUY la breakout peste high-ul ultimelor N bare, SELL la breakout sub low.
Confirmare: volum crescut + ATR filter (volatilitate minima).
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("breakout")


class Breakout(BaseStrategy):
    """Breakout Strategy — tranzactioneaza spargerea range-ului."""

    def __init__(self, params: dict):
        super().__init__("Breakout", params)

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Calculeaza niveluri de breakout, ATR si volum mediu."""
        df = data.copy()

        lookback = self.params.get("lookback_period", 20)

        # High/Low pe ultimele N bare (fara bara curenta)
        df["range_high"] = df["high"].shift(1).rolling(lookback).max()
        df["range_low"] = df["low"].shift(1).rolling(lookback).min()

        # ATR pentru filtru volatilitate
        atr_period = self.params.get("atr_period", 14)
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=atr_period)

        # ATR mediu pe o fereastra mai mare (pt comparatie)
        df["atr_avg"] = df["atr"].rolling(atr_period * 2).mean()

        # Volum mediu
        vol_col = "volume" if "volume" in df.columns else "tick_volume"
        if vol_col in df.columns:
            df["vol_avg"] = df[vol_col].rolling(lookback).mean()
            df["vol_ratio"] = df[vol_col] / df["vol_avg"]
        else:
            df["vol_ratio"] = 1.0

        return df

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Detecteaza breakout cu confirmare volum si ATR."""
        df = self.calculate_indicators(data)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Verificare NaN
        cols = ["range_high", "range_low", "atr", "atr_avg", "vol_ratio"]
        if last[cols].isna().any():
            return Signal.HOLD

        min_atr_ratio = self.params.get("min_atr_ratio", 0.8)
        min_vol_ratio = self.params.get("min_vol_ratio", 1.2)

        # Filtru: ATR trebuie sa fie cel putin X% din media (piata activa)
        if last["atr"] < last["atr_avg"] * min_atr_ratio:
            return Signal.HOLD

        # Filtru volum: volumul curent > X * media
        vol_ok = last["vol_ratio"] >= min_vol_ratio

        # BUY Breakout: pretul inchide PESTE range_high + volum crescut
        if last["close"] > last["range_high"] and prev["close"] <= prev["range_high"]:
            if vol_ok:
                logger.info(
                    f"SEMNAL BUY (breakout) — Close={last['close']:.5f} > "
                    f"Range_high={last['range_high']:.5f}, "
                    f"Vol_ratio={last['vol_ratio']:.2f}, ATR={last['atr']:.5f}"
                )
                return Signal.BUY

        # SELL Breakout: pretul inchide SUB range_low + volum crescut
        if last["close"] < last["range_low"] and prev["close"] >= prev["range_low"]:
            if vol_ok:
                logger.info(
                    f"SEMNAL SELL (breakout) — Close={last['close']:.5f} < "
                    f"Range_low={last['range_low']:.5f}, "
                    f"Vol_ratio={last['vol_ratio']:.2f}, ATR={last['atr']:.5f}"
                )
                return Signal.SELL

        return Signal.HOLD
