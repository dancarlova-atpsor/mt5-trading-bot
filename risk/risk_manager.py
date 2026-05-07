"""
Modul de Risk Management — dimensionare pozitii, SL/TP, trailing stop, limite zilnice.
"""

from datetime import date

import pandas as pd
import pandas_ta as ta

import MetaTrader5 as mt5

from utils.logger import setup_logger
from utils.helpers import load_config, pips_to_price

logger = setup_logger("risk_manager")


class RiskManager:
    """Gestioneaza riscul per tranzactie si pe zi."""

    def __init__(self, config_path: str = "risk_management.yaml"):
        config = load_config(config_path)
        self.position_sizing = config["position_sizing"]
        self.stop_loss_config = config["stop_loss"]
        self.take_profit_config = config["take_profit"]
        self.trailing_config = config["trailing_stop"]
        self.limits = config["limits"]

        # Tracking zilnic
        self._daily_trades: int = 0
        self._daily_pnl: float = 0.0
        self._current_date: date = date.today()

    def _reset_daily_if_needed(self):
        """Reseteaza contoarele zilnice daca s-a schimbat ziua."""
        today = date.today()
        if today != self._current_date:
            logger.info(
                f"Zi noua ({today}) — reset contoare zilnice. "
                f"PnL ieri: {self._daily_pnl:.2f}, tranzactii: {self._daily_trades}"
            )
            self._daily_trades = 0
            self._daily_pnl = 0.0
            self._current_date = today

    def calculate_position_size(
        self, account_balance: float, stop_loss_pips: float, symbol: str
    ) -> float:
        """Calculeaza volumul (lot size) pe baza riscului.

        Foloseste trade_tick_value de la broker pentru a obtine valoarea
        reala in moneda contului (evita bug-ul JPY/metals/crypto).
        """
        # Verifica overrides per simbol
        volume_overrides = self.position_sizing.get("volume_overrides", {}) or {}
        if symbol in volume_overrides:
            return float(volume_overrides[symbol])

        if self.position_sizing["method"] == "fixed_lot":
            return self.position_sizing["fixed_lot"]

        # Metoda percent_risk
        risk_per_trade = self.position_sizing["risk_per_trade"]

        # Simulated balance — pt test "ca si cum am avea cont real" cu balance specific
        # Daca e setat, ignoram balance-ul real si folosim sim_balance pt calcul
        sim_balance = self.position_sizing.get("simulated_balance", 0)
        balance_for_calc = sim_balance if sim_balance > 0 else account_balance

        risk_amount = balance_for_calc * (risk_per_trade / 100)

        # Cap absolut pe risk in USD (sa nu depaseasca limita zilnica)
        max_risk_usd = self.position_sizing.get("max_risk_per_trade_usd", 0)
        if max_risk_usd > 0 and risk_amount > max_risk_usd:
            logger.debug(f"Risk clamp: {risk_amount:.2f} → {max_risk_usd} USD (max_risk_per_trade_usd)")
            risk_amount = max_risk_usd

        if stop_loss_pips <= 0:
            logger.warning("SL pips <= 0, folosesc lot minim 0.01")
            return 0.01

        # Foloseste tick_value de la broker (valoare reala in moneda contului)
        info = mt5.symbol_info(symbol)
        if info is None or info.trade_tick_size == 0 or info.trade_tick_value == 0:
            logger.warning(f"Nu pot obtine tick_value pentru {symbol}, folosesc lot minim")
            return 0.01

        # Pip in price
        pip_price = pips_to_price(symbol, 1.0)

        # Cati tick-uri are un pip (pt forex 5-digit: 1 pip = 10 ticks)
        pip_ticks = pip_price / info.trade_tick_size

        # Valoare 1 pip per 1 lot in moneda contului
        pip_value_per_lot = info.trade_tick_value * pip_ticks

        # Risc per lot la SL hit
        risk_per_lot = stop_loss_pips * pip_value_per_lot

        if risk_per_lot <= 0:
            return 0.01

        lot_size = risk_amount / risk_per_lot

        # Clamp la limite
        max_lot = self.position_sizing.get("max_lot", 1.0)
        lot_size = max(info.volume_min, min(lot_size, max_lot))

        # Rotunjeste la volume_step
        if info.volume_step > 0:
            lot_size = round(lot_size / info.volume_step) * info.volume_step
        lot_size = round(lot_size, 2)

        logger.debug(
            f"Position size {symbol}: balance={account_balance:.2f}, "
            f"SL={stop_loss_pips:.1f} pips, risk={risk_amount:.2f}, "
            f"pip_val={pip_value_per_lot:.2f}/lot, lot={lot_size}"
        )
        return lot_size

    def calculate_stop_loss(
        self, data: pd.DataFrame, direction: str, current_price: float, symbol: str
    ) -> float:
        """Calculeaza Stop Loss — ATR-based, override per simbol sau default pips."""
        # Verifica override per simbol (in pips sau price)
        sl_overrides = self.stop_loss_config.get("symbol_overrides", {}) or {}
        if symbol in sl_overrides:
            override = sl_overrides[symbol]
            # Override poate fi in "pips" sau "price" direct
            if isinstance(override, dict):
                if "pips" in override:
                    sl_distance = pips_to_price(symbol, override["pips"])
                elif "price" in override:
                    sl_distance = float(override["price"])
                else:
                    sl_distance = pips_to_price(symbol, self.stop_loss_config["default_pips"])
            else:
                # Direct valoare numerica = pips
                sl_distance = pips_to_price(symbol, float(override))

            if direction == "BUY":
                sl = current_price - sl_distance
            else:
                sl = current_price + sl_distance

            digits = self._get_symbol_digits(symbol)
            logger.debug(f"SL OVERRIDE {symbol} {direction}: {sl:.{digits}f} (distanta: {sl_distance:.{digits}f})")
            return float(round(sl, digits))

        # Default: ATR-based sau pips
        if self.stop_loss_config.get("use_atr", False) and len(data) >= self.stop_loss_config["atr_period"] + 1:
            atr = ta.atr(
                data["high"], data["low"], data["close"],
                length=self.stop_loss_config["atr_period"]
            )
            atr_value = atr.iloc[-1]
            if pd.notna(atr_value) and atr_value > 0:
                sl_distance = atr_value * self.stop_loss_config["atr_multiplier"]
            else:
                sl_distance = pips_to_price(symbol, self.stop_loss_config["default_pips"])
        else:
            sl_distance = pips_to_price(symbol, self.stop_loss_config["default_pips"])

        if direction == "BUY":
            sl = current_price - sl_distance
        else:
            sl = current_price + sl_distance

        digits = self._get_symbol_digits(symbol)
        logger.debug(f"SL {symbol} {direction}: {sl:.{digits}f} (distanta: {sl_distance:.{digits}f})")
        return float(round(sl, digits))

    def calculate_take_profit(
        self, entry_price: float, stop_loss: float, direction: str, symbol: str = ""
    ) -> float:
        """Calculeaza Take Profit pe baza risk-reward ratio.
        Suporta override per simbol via take_profit.symbol_rr_overrides in config.
        """
        sl_distance = abs(entry_price - stop_loss)
        rr_overrides = self.take_profit_config.get("symbol_rr_overrides", {}) or {}
        rr = rr_overrides.get(symbol, self.take_profit_config["risk_reward_ratio"])
        tp_distance = sl_distance * rr

        if direction == "BUY":
            tp = entry_price + tp_distance
        else:
            tp = entry_price - tp_distance

        digits = self._get_symbol_digits(symbol) if symbol else 5
        logger.debug(f"TP {direction} {symbol}: {tp:.{digits}f} (RR={rr})")
        return float(round(tp, digits))

    @staticmethod
    def _get_symbol_digits(symbol: str) -> int:
        """Obtine numarul de digits pentru un simbol de la MT5."""
        try:
            info = mt5.symbol_info(symbol)
            if info:
                return info.digits
        except Exception:
            pass
        # Fallback pe baza numelui
        sym = symbol.upper()
        if "XAU" in sym or "XAG" in sym:
            return 3
        if "JPY" in sym:
            return 3
        return 5

    def can_open_trade(
        self, symbol: str, open_positions: list[dict], account_balance: float
    ) -> tuple[bool, str]:
        """Verifica daca se poate deschide o tranzactie noua."""
        self._reset_daily_if_needed()

        # Limita pozitii totale
        if len(open_positions) >= self.limits["max_open_positions"]:
            return False, f"Limita pozitii deschise atinsa ({self.limits['max_open_positions']})"

        # Limita per simbol
        symbol_count = sum(1 for p in open_positions if p["symbol"] == symbol)
        if symbol_count >= self.limits["max_positions_per_symbol"]:
            return False, f"Limita pozitii pe {symbol} atinsa ({self.limits['max_positions_per_symbol']})"

        # Limita tranzactii zilnice
        if self._daily_trades >= self.limits["max_daily_trades"]:
            return False, f"Limita tranzactii zilnice atinsa ({self.limits['max_daily_trades']})"

        # Limita pierdere zilnica
        max_loss = account_balance * (self.limits["max_daily_loss_percent"] / 100)
        if self._daily_pnl <= -max_loss:
            return False, f"Limita pierdere zilnica atinsa ({self._daily_pnl:.2f})"

        return True, "OK"

    def update_trailing_stop(self, position: dict, current_price: float) -> float | None:
        """Calculeaza noul SL pentru trailing stop. Returneaza None daca nu e cazul."""
        if not self.trailing_config.get("enabled", False):
            return None

        symbol = position["symbol"]
        activation_distance = pips_to_price(symbol, self.trailing_config["activation_pips"])
        trail_distance = pips_to_price(symbol, self.trailing_config["trail_pips"])

        if position["type"] == "BUY":
            profit_distance = current_price - position["open_price"]
            if profit_distance >= activation_distance:
                new_sl = current_price - trail_distance
                if new_sl > position["sl"]:
                    logger.debug(
                        f"Trailing BUY #{position['ticket']}: SL {position['sl']:.5f} -> {new_sl:.5f}"
                    )
                    return round(new_sl, 5)
        else:  # SELL
            profit_distance = position["open_price"] - current_price
            if profit_distance >= activation_distance:
                new_sl = current_price + trail_distance
                if position["sl"] == 0 or new_sl < position["sl"]:
                    logger.debug(
                        f"Trailing SELL #{position['ticket']}: SL {position['sl']:.5f} -> {new_sl:.5f}"
                    )
                    return round(new_sl, 5)

        return None

    def record_trade(self, pnl: float):
        """Inregistreaza o tranzactie inchisa (PnL si contor)."""
        self._reset_daily_if_needed()
        self._daily_trades += 1
        self._daily_pnl += pnl
        logger.info(f"Trade inregistrat: PnL={pnl:.2f} | Zilnic: {self._daily_trades} tranzactii, PnL={self._daily_pnl:.2f}")

    def get_daily_stats(self) -> dict:
        """Returneaza statisticile zilnice."""
        self._reset_daily_if_needed()
        return {
            "trades": self._daily_trades,
            "pnl": self._daily_pnl,
            "date": self._current_date.isoformat(),
        }
