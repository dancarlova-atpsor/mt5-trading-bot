"""
AdaptiveMTF — Multi-Timeframe Adaptive Strategy
Creat 2026-08-18 — dupa 2 saptamani de zone_based static care nu detecta trend UP pe XAG (55→65)

Filosofie: face ce face un TRADER REAL:
  1. D1 → detecteaza TREND MAJOR (bullish/bearish/neutral) via EMA 50 + MACD + structura HH/LL
  2. H4 → detecteaza SETUP (pullback in trend / rally in downtrend / range) via RSI + ATR
  3. H1 → TIMING intrare (RSI extrem) — confirmarea finala
  4. DECIZIE:
     - BULLISH bias + pullback H4 + H1 oversold  → BUY (in directia trend-ului!)
     - BEARISH bias + rally H4 + H1 overbought  → SELL (in directia trend-ului!)
     - NEUTRAL bias + extreme H4+H1              → BUY/SELL range
  5. SL/TP dinamic: ATR H4 * 1.5 pentru SL, R:R 2.5 pentru TP

Elimina problemele zone_based:
- Nu mai are zone HARDCODED care se invecheasca
- Trade in DIRECTIA trend-ului (nu contra ca inainte)
- Adapteaza la piata (recalculeaza indicatori la fiecare tick)
- Multi-TF confirmare = semnal de calitate
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("adaptive_mtf")


class AdaptiveMTFStrategy(BaseStrategy):
    """Strategia adaptiva multi-timeframe. Fara zone hardcoded — totul dinamic."""

    def __init__(self, params: dict):
        super().__init__("AdaptiveMTF", params)
        # D1 trend detection
        self.ema_period_d1 = params.get("ema_period_d1", 50)
        self.macd_fast = params.get("macd_fast", 12)
        self.macd_slow = params.get("macd_slow", 26)
        self.macd_signal_period = params.get("macd_signal", 9)

        # H4 setup
        self.rsi_period = params.get("rsi_period", 14)
        self.atr_period = params.get("atr_period", 14)
        self.atr_sl_mult = params.get("atr_sl_mult", 1.5)
        self.tp_rr = params.get("tp_rr", 2.5)

        # RSI thresholds
        self.rsi_buy_extreme = params.get("rsi_buy_extreme", 35)   # H1 for entry timing
        self.rsi_sell_extreme = params.get("rsi_sell_extreme", 65)
        self.rsi_buy_moderate = params.get("rsi_buy_moderate", 50)  # H4 for setup
        self.rsi_sell_moderate = params.get("rsi_sell_moderate", 50)
        self.rsi_range_extreme_buy = params.get("rsi_range_extreme_buy", 30)  # NEUTRAL bias range
        self.rsi_range_extreme_sell = params.get("rsi_range_extreme_sell", 70)

        # State: SL/TP calculate in analyze_mtf, citite din main.py via get_sl_tp
        self._last_sl: dict = {}
        self._last_tp: dict = {}
        self._last_bias: dict = {}
        self._current_symbol: str = ""

    def set_current_symbol(self, symbol: str):
        self._current_symbol = symbol

    def _detect_trend_d1(self, data_d1: pd.DataFrame) -> str:
        """Analiza D1 → BULLISH / BEARISH / NEUTRAL.

        3 indicatori:
          1. Pretul vs EMA 50 (deasupra = bull)
          2. MACD histogram (pozitiv & crescator = bull)
          3. Structura HH/LL (recent 20 candles high > older 20 = bull)
        """
        if len(data_d1) < self.ema_period_d1 + 20:
            return "NEUTRAL"

        # 1. EMA 50
        ema = ta.ema(data_d1["close"], length=self.ema_period_d1)
        last_close = float(data_d1["close"].iloc[-1])
        last_ema = float(ema.iloc[-1]) if not pd.isna(ema.iloc[-1]) else last_close

        # 2. MACD
        macd_result = ta.macd(
            data_d1["close"],
            fast=self.macd_fast,
            slow=self.macd_slow,
            signal=self.macd_signal_period,
        )
        if macd_result is None or macd_result.empty or len(macd_result.columns) < 2:
            return "NEUTRAL"

        macd_hist = macd_result.iloc[-1, 1]
        macd_hist_prev = macd_result.iloc[-2, 1] if len(macd_result) >= 2 else 0
        if pd.isna(macd_hist) or pd.isna(macd_hist_prev):
            return "NEUTRAL"

        # 3. Structure: HH/LL
        recent_high = float(data_d1["high"].iloc[-20:].max())
        older_high = float(data_d1["high"].iloc[-40:-20].max()) if len(data_d1) >= 40 else recent_high
        recent_low = float(data_d1["low"].iloc[-20:].min())
        older_low = float(data_d1["low"].iloc[-40:-20].min()) if len(data_d1) >= 40 else recent_low

        bullish_structure = recent_high > older_high and recent_low > older_low
        bearish_structure = recent_high < older_high and recent_low < older_low

        # Score bull/bear
        bull_score = 0
        bear_score = 0

        if last_close > last_ema:
            bull_score += 1
        else:
            bear_score += 1

        if macd_hist > 0 and macd_hist >= macd_hist_prev:
            bull_score += 1
        elif macd_hist < 0 and macd_hist <= macd_hist_prev:
            bear_score += 1

        if bullish_structure:
            bull_score += 1
        elif bearish_structure:
            bear_score += 1

        if bull_score >= 2 and bull_score > bear_score:
            return "BULLISH"
        elif bear_score >= 2 and bear_score > bull_score:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def analyze_mtf(
        self,
        data_d1: pd.DataFrame,
        data_h4: pd.DataFrame,
        data_h1: pd.DataFrame,
        symbol: str,
    ) -> Signal:
        """Analiza multi-timeframe. Stocheaza SL/TP intern, returneaza Signal."""

        if len(data_d1) < 60 or len(data_h4) < 30 or len(data_h1) < 30:
            logger.debug(
                f"[AdaptiveMTF] {symbol}: date insuficiente "
                f"(D1={len(data_d1)}, H4={len(data_h4)}, H1={len(data_h1)})"
            )
            return Signal.HOLD

        # 1. TREND D1 bias
        bias = self._detect_trend_d1(data_d1)
        self._last_bias[symbol] = bias

        # 2. H4 indicators
        rsi_h4 = ta.rsi(data_h4["close"], length=self.rsi_period)
        atr_h4 = ta.atr(data_h4["high"], data_h4["low"], data_h4["close"], length=self.atr_period)

        if rsi_h4 is None or atr_h4 is None or len(rsi_h4) == 0 or len(atr_h4) == 0:
            return Signal.HOLD
        if pd.isna(rsi_h4.iloc[-1]) or pd.isna(atr_h4.iloc[-1]):
            return Signal.HOLD

        last_rsi_h4 = float(rsi_h4.iloc[-1])
        last_atr_h4 = float(atr_h4.iloc[-1])

        # 3. H1 indicators
        rsi_h1 = ta.rsi(data_h1["close"], length=self.rsi_period)
        if rsi_h1 is None or len(rsi_h1) == 0 or pd.isna(rsi_h1.iloc[-1]):
            return Signal.HOLD
        last_rsi_h1 = float(rsi_h1.iloc[-1])

        current_price = float(data_h1["close"].iloc[-1])

        # 4. Decizie multi-TF
        signal = Signal.HOLD
        sl = None
        tp = None
        reason = ""

        if bias == "BULLISH":
            if last_rsi_h4 <= self.rsi_buy_moderate and last_rsi_h1 <= self.rsi_buy_extreme:
                signal = Signal.BUY
                sl = current_price - last_atr_h4 * self.atr_sl_mult
                tp = current_price + (current_price - sl) * self.tp_rr
                reason = (
                    f"BULLISH bias + H4 RSI {last_rsi_h4:.1f} <= {self.rsi_buy_moderate} "
                    f"+ H1 RSI {last_rsi_h1:.1f} <= {self.rsi_buy_extreme}"
                )

        elif bias == "BEARISH":
            if last_rsi_h4 >= self.rsi_sell_moderate and last_rsi_h1 >= self.rsi_sell_extreme:
                signal = Signal.SELL
                sl = current_price + last_atr_h4 * self.atr_sl_mult
                tp = current_price - (sl - current_price) * self.tp_rr
                reason = (
                    f"BEARISH bias + H4 RSI {last_rsi_h4:.1f} >= {self.rsi_sell_moderate} "
                    f"+ H1 RSI {last_rsi_h1:.1f} >= {self.rsi_sell_extreme}"
                )

        else:  # NEUTRAL / RANGE
            if last_rsi_h4 <= self.rsi_range_extreme_buy and last_rsi_h1 <= self.rsi_range_extreme_buy:
                signal = Signal.BUY
                sl = current_price - last_atr_h4 * self.atr_sl_mult
                tp = current_price + (current_price - sl) * self.tp_rr
                reason = (
                    f"NEUTRAL bias + H4/H1 RSI oversold ({last_rsi_h4:.1f}/{last_rsi_h1:.1f}) "
                    f"— range BUY"
                )
            elif last_rsi_h4 >= self.rsi_range_extreme_sell and last_rsi_h1 >= self.rsi_range_extreme_sell:
                signal = Signal.SELL
                sl = current_price + last_atr_h4 * self.atr_sl_mult
                tp = current_price - (sl - current_price) * self.tp_rr
                reason = (
                    f"NEUTRAL bias + H4/H1 RSI overbought ({last_rsi_h4:.1f}/{last_rsi_h1:.1f}) "
                    f"— range SELL"
                )

        if signal != Signal.HOLD:
            logger.info(
                f"[AdaptiveMTF] {symbol}: {reason} → {signal.value} @ {current_price:.5f} "
                f"| SL={sl:.5f} TP={tp:.5f} (ATR H4={last_atr_h4:.5f}, R:R={self.tp_rr})"
            )
            self._last_sl[symbol] = sl
            self._last_tp[symbol] = tp
        else:
            logger.debug(
                f"[AdaptiveMTF] {symbol}: {bias} bias | H4 RSI {last_rsi_h4:.1f} | H1 RSI {last_rsi_h1:.1f} — HOLD"
            )

        return signal

    def get_sl_tp(self, symbol: str, direction: str, entry_price: float) -> tuple:
        """Returneaza SL/TP calculate anterior in analyze_mtf."""
        sl = self._last_sl.get(symbol)
        tp = self._last_tp.get(symbol)
        return (sl, tp)

    # Interfata clasica BaseStrategy (unused pentru AdaptiveMTF)
    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        return data

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Nu se foloseste — folosim analyze_mtf. Fallback HOLD."""
        return Signal.HOLD
