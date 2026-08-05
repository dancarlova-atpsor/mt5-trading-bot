"""
ZoneBasedStrategy — strategie unificata pentru simboluri in regim de basing sau trending pullback.

Filosofie:
  - NU tranzactioneaza in mijloc de range (dead_zone) — evita whipsaw
  - BUY doar in buy_zone (fund) cu RSI H4 < 40
  - SELL doar in sell_zone (top) cu RSI H4 > 60
  - Zone tehnice HARDCODED per simbol (bazate pe analiza chart, nu Donchian dinamic)
  - SL fix pe nivel tehnic (nu ATR) — mai strans si mai relevant

Creat: 2026-08-03 (dupa analiza chart 7 simboluri XAG/XAU/XPT/XPD/BRENT/WTI/COCOA)
Motiv: framework-ul MeanRev H1 + Breakout H1 = whipsaw factory in range strans si trending recovery.
"""

import pandas as pd
import pandas_ta as ta

from strategies.base_strategy import BaseStrategy, Signal
from utils.logger import setup_logger

logger = setup_logger("zone_based")


class ZoneBasedStrategy(BaseStrategy):
    """Strategie zone-based cu confirmari RSI + optional volume.

    Config asteptat (per symbol):
      buy_zone: [min, max]      # BUY doar cand pretul e in acest interval
      sell_zone: [min, max]     # SELL doar cand pretul e in acest interval
      dead_zone: [min, max]     # NU tranzactiona in interval (mijloc range)
      rsi_buy_max: 40           # BUY doar daca RSI <= 40
      rsi_sell_min: 60          # SELL doar daca RSI >= 60
      breakout_up: 62.30        # Optional: BUY breakout daca pretul > acest nivel
      breakout_down: 53.50      # Optional: SELL breakdown daca pretul < acest nivel
    """

    def __init__(self, params: dict):
        super().__init__("ZoneBased", params)
        # params: dict de forma { symbol1: { buy_zone: [...], ... }, symbol2: {...}, ... }
        # sau simplu { buy_zone: [...], ... } pentru single-symbol testing
        self.rsi_period = params.get("rsi_period", 14)
        self._current_symbol: str = ""

    def set_current_symbol(self, symbol: str):
        """Seteaza simbolul curent pentru lookup zone."""
        self._current_symbol = symbol

    def _get_zones(self, symbol: str) -> dict | None:
        """Returneaza config zonelor pentru un simbol.
        Cauta in params.zones[symbol] sau in params direct (single-symbol mode).
        """
        zones_map = self.params.get("zones", {}) or {}
        if symbol in zones_map:
            return zones_map[symbol]
        # Fallback: single-symbol mode (params contine direct buy_zone etc)
        if "buy_zone" in self.params or "sell_zone" in self.params:
            return self.params
        return None

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        df["rsi"] = ta.rsi(df["close"], length=self.rsi_period)
        return df

    def analyze(self, data: pd.DataFrame) -> Signal:
        symbol = self._current_symbol
        zones = self._get_zones(symbol)
        if zones is None:
            logger.debug(f"[ZoneBased] {symbol}: nu are zone configurate — HOLD")
            return Signal.HOLD

        df = self.calculate_indicators(data)
        last = df.iloc[-1]
        if pd.isna(last["rsi"]):
            return Signal.HOLD

        price = float(last["close"])
        rsi = float(last["rsi"])

        # ═══════════════════════════════════════════════════════════
        # 1. BREAKOUT check (DEZACTIVAT 2026-08-05)
        # Motivul: SL/TP zone-based bogus pentru pretul breakout
        # Ex XPTUSD BREAKOUT UP 1776 → BUY cu TP=1640 (mai jos ca entry!)
        # Necesita implementare dedicata BREAKOUT get_sl_tp — TODO
        # ═══════════════════════════════════════════════════════════
        # breakout_up = zones.get("breakout_up")
        # breakout_down = zones.get("breakout_down")
        # if breakout_up is not None and price > float(breakout_up):
        #     return Signal.BUY
        # if breakout_down is not None and price < float(breakout_down):
        #     return Signal.SELL

        # ═══════════════════════════════════════════════════════════
        # 2. DEAD ZONE — skip tranzactionare
        # ═══════════════════════════════════════════════════════════
        dead_zone = zones.get("dead_zone")
        if dead_zone is not None and len(dead_zone) == 2:
            if float(dead_zone[0]) <= price <= float(dead_zone[1]):
                logger.debug(f"[ZoneBased] {symbol}: in dead zone [{dead_zone[0]}, {dead_zone[1]}] la {price:.5f} — HOLD")
                return Signal.HOLD

        # ═══════════════════════════════════════════════════════════
        # 3. BUY ZONE — pret in zona jos + RSI oversold
        # ═══════════════════════════════════════════════════════════
        buy_zone = zones.get("buy_zone")
        rsi_buy_max = zones.get("rsi_buy_max", 40)

        if buy_zone is not None and len(buy_zone) == 2:
            if float(buy_zone[0]) <= price <= float(buy_zone[1]):
                if rsi <= float(rsi_buy_max):
                    logger.info(
                        f"[ZoneBased] {symbol}: BUY zone [{buy_zone[0]}, {buy_zone[1]}] "
                        f"la {price:.5f} + RSI {rsi:.1f} <= {rsi_buy_max} → BUY"
                    )
                    return Signal.BUY
                else:
                    logger.debug(
                        f"[ZoneBased] {symbol}: in buy_zone la {price:.5f} dar RSI {rsi:.1f} > {rsi_buy_max} — astept RSI mai jos"
                    )

        # ═══════════════════════════════════════════════════════════
        # 4. SELL ZONE — pret in zona sus + RSI overbought
        # ═══════════════════════════════════════════════════════════
        sell_zone = zones.get("sell_zone")
        rsi_sell_min = zones.get("rsi_sell_min", 60)

        if sell_zone is not None and len(sell_zone) == 2:
            if float(sell_zone[0]) <= price <= float(sell_zone[1]):
                if rsi >= float(rsi_sell_min):
                    logger.info(
                        f"[ZoneBased] {symbol}: SELL zone [{sell_zone[0]}, {sell_zone[1]}] "
                        f"la {price:.5f} + RSI {rsi:.1f} >= {rsi_sell_min} → SELL"
                    )
                    return Signal.SELL
                else:
                    logger.debug(
                        f"[ZoneBased] {symbol}: in sell_zone la {price:.5f} dar RSI {rsi:.1f} < {rsi_sell_min} — astept RSI mai sus"
                    )

        return Signal.HOLD

    def get_sl_tp(self, symbol: str, direction: str, entry_price: float) -> tuple[float | None, float | None]:
        """Returneaza SL si TP hardcoded pentru un simbol zone-based.

        Preferat vs ATR-based pentru ca:
        - SL fixat la nivel tehnic (sub/peste breakout)
        - TP la mijloc range sau extremitate opusa

        Config asteptat in zones[symbol]:
          buy_sl:  53.80          # Absolute price SL pentru BUY
          buy_tp:  58.00          # Absolute price TP pentru BUY (mijloc range)
          sell_sl: 62.50          # Absolute price SL pentru SELL
          sell_tp: 58.00          # Absolute price TP pentru SELL

        Fallback: daca lipsesc, deduce din breakout levels + zone opusa.
        Returns: (sl, tp) sau (None, None) daca nu se poate calcula.
        """
        zones = self._get_zones(symbol)
        if zones is None:
            return None, None

        if direction == "BUY":
            # SL: explicit buy_sl SAU breakout_down
            sl = zones.get("buy_sl")
            if sl is None and zones.get("breakout_down") is not None:
                sl = float(zones["breakout_down"]) - 0.3 * (entry_price - float(zones["breakout_down"]))  # 30% sub trigger
            # TP: explicit buy_tp SAU mijloc dead_zone SAU minim sell_zone
            tp = zones.get("buy_tp")
            if tp is None:
                dz = zones.get("dead_zone")
                if dz and len(dz) == 2:
                    tp = (float(dz[0]) + float(dz[1])) / 2
                elif zones.get("sell_zone"):
                    tp = float(zones["sell_zone"][0])
            return (float(sl) if sl is not None else None,
                    float(tp) if tp is not None else None)
        elif direction == "SELL":
            sl = zones.get("sell_sl")
            if sl is None and zones.get("breakout_up") is not None:
                sl = float(zones["breakout_up"]) + 0.3 * (float(zones["breakout_up"]) - entry_price)
            tp = zones.get("sell_tp")
            if tp is None:
                dz = zones.get("dead_zone")
                if dz and len(dz) == 2:
                    tp = (float(dz[0]) + float(dz[1])) / 2
                elif zones.get("buy_zone"):
                    tp = float(zones["buy_zone"][1])
            return (float(sl) if sl is not None else None,
                    float(tp) if tp is not None else None)

        return None, None
