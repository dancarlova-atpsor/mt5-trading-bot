"""
Teste pentru RiskManager — position sizing, SL/TP, limite.
"""

import numpy as np
import pandas as pd
import pytest

from risk.risk_manager import RiskManager


def make_ohlcv_for_atr(n: int = 100) -> pd.DataFrame:
    """Creeaza date OHLCV cu volatilitate controlata pentru testare ATR."""
    np.random.seed(42)
    closes = [1.1000 + i * 0.0001 + np.random.normal(0, 0.0005) for i in range(n)]
    data = {
        "open": [c - 0.0005 for c in closes],
        "high": [c + 0.0010 for c in closes],
        "low": [c - 0.0010 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    }
    index = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(data, index=index)


class TestPositionSizing:
    """Teste pentru calculul dimensiunii pozitiei."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_percent_risk_basic(self, rm):
        """Test position sizing cu metoda percent_risk."""
        lot = rm.calculate_position_size(
            account_balance=10000, stop_loss_pips=50, symbol="EURUSD"
        )
        assert lot > 0
        assert lot <= rm.position_sizing["max_lot"]
        assert lot >= 0.01

    def test_lot_size_increases_with_balance(self, rm):
        """Lot mai mare la balance mai mare."""
        lot_small = rm.calculate_position_size(5000, 50, "EURUSD")
        lot_big = rm.calculate_position_size(50000, 50, "EURUSD")
        assert lot_big > lot_small

    def test_lot_size_decreases_with_bigger_sl(self, rm):
        """Lot mai mic cu SL mai mare."""
        lot_tight = rm.calculate_position_size(10000, 20, "EURUSD")
        lot_wide = rm.calculate_position_size(10000, 100, "EURUSD")
        assert lot_tight > lot_wide

    def test_minimum_lot(self, rm):
        """Lot minim 0.01 chiar si la balance mic."""
        lot = rm.calculate_position_size(100, 50, "EURUSD")
        assert lot >= 0.01

    def test_xauusd_sizing(self, rm):
        """Position sizing corect pentru aur."""
        lot = rm.calculate_position_size(10000, 50, "XAUUSD")
        assert lot > 0
        assert lot <= rm.position_sizing["max_lot"]

    def test_zero_sl_returns_minimum(self, rm):
        """SL 0 pips -> lot minim."""
        lot = rm.calculate_position_size(10000, 0, "EURUSD")
        assert lot == 0.01


class TestStopLoss:
    """Teste pentru calculul Stop Loss."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_buy_sl_below_price(self, rm):
        """SL pentru BUY trebuie sa fie sub pretul curent."""
        data = make_ohlcv_for_atr()
        sl = rm.calculate_stop_loss(data, "BUY", 1.1100, "EURUSD")
        assert sl < 1.1100

    def test_sell_sl_above_price(self, rm):
        """SL pentru SELL trebuie sa fie deasupra pretului curent."""
        data = make_ohlcv_for_atr()
        sl = rm.calculate_stop_loss(data, "SELL", 1.1100, "EURUSD")
        assert sl > 1.1100


class TestTakeProfit:
    """Teste pentru calculul Take Profit."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_buy_tp_above_entry(self, rm):
        """TP pentru BUY trebuie sa fie deasupra entry."""
        tp = rm.calculate_take_profit(1.1000, 1.0950, "BUY")
        assert tp > 1.1000

    def test_sell_tp_below_entry(self, rm):
        """TP pentru SELL trebuie sa fie sub entry."""
        tp = rm.calculate_take_profit(1.1000, 1.1050, "SELL")
        assert tp < 1.1000

    def test_rr_ratio(self, rm):
        """Verificare risk-reward ratio corect."""
        entry = 1.1000
        sl = 1.0950  # 50 pips
        tp = rm.calculate_take_profit(entry, sl, "BUY")
        sl_distance = abs(entry - sl)
        tp_distance = abs(tp - entry)
        expected_ratio = rm.take_profit_config["risk_reward_ratio"]
        assert abs(tp_distance / sl_distance - expected_ratio) < 0.01


class TestCanOpenTrade:
    """Teste pentru verificarea limitelor de trading."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_can_open_when_no_positions(self, rm):
        """Permite deschidere cand nu exista pozitii."""
        can, reason = rm.can_open_trade("EURUSD", [], 10000)
        assert can is True
        assert reason == "OK"

    def test_blocked_by_max_positions(self, rm):
        """Blocheaza cand sunt prea multe pozitii deschise."""
        positions = [{"symbol": f"SYM{i}", "type": "BUY"} for i in range(10)]
        can, reason = rm.can_open_trade("EURUSD", positions, 10000)
        assert can is False
        assert "pozitii deschise" in reason.lower()

    def test_blocked_by_max_per_symbol(self, rm):
        """Blocheaza cand sunt prea multe pozitii pe un simbol."""
        positions = [{"symbol": "EURUSD", "type": "BUY"} for _ in range(5)]
        can, reason = rm.can_open_trade("EURUSD", positions, 10000)
        assert can is False

    def test_blocked_by_daily_loss(self, rm):
        """Blocheaza cand pierderea zilnica depaseste limita."""
        rm._daily_pnl = -500  # Pierdere mare
        can, reason = rm.can_open_trade("EURUSD", [], 10000)
        assert can is False
        assert "pierdere zilnica" in reason.lower()

    def test_blocked_by_daily_trades(self, rm):
        """Blocheaza cand numarul de tranzactii zilnice depaseste limita."""
        rm._daily_trades = 100
        can, reason = rm.can_open_trade("EURUSD", [], 10000)
        assert can is False
        assert "tranzactii zilnice" in reason.lower()


class TestTrailingStop:
    """Teste pentru trailing stop."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_no_update_before_activation(self, rm):
        """Nu actualizeaza SL inainte de activare."""
        position = {
            "symbol": "EURUSD",
            "type": "BUY",
            "open_price": 1.1000,
            "sl": 1.0950,
            "ticket": 1,
        }
        # Profit mic, sub activation_pips
        new_sl = rm.update_trailing_stop(position, 1.1010)
        assert new_sl is None

    def test_update_after_activation_buy(self, rm):
        """Actualizeaza SL dupa activare pe BUY."""
        position = {
            "symbol": "EURUSD",
            "type": "BUY",
            "open_price": 1.1000,
            "sl": 1.0950,
            "ticket": 1,
        }
        # Profit > 30 pips (activation_pips)
        new_sl = rm.update_trailing_stop(position, 1.1050)
        if new_sl is not None:
            assert new_sl > position["sl"]

    def test_sl_only_moves_up_for_buy(self, rm):
        """SL nu scade pe BUY."""
        position = {
            "symbol": "EURUSD",
            "type": "BUY",
            "open_price": 1.1000,
            "sl": 1.1020,  # SL deja ridicat
            "ticket": 1,
        }
        # Pret care ar da SL mai mic decat cel existent
        new_sl = rm.update_trailing_stop(position, 1.1035)
        if new_sl is not None:
            assert new_sl > position["sl"]
        # Daca e None inseamna ca SL nu s-a mutat (corect)


class TestRecordTrade:
    """Teste pentru inregistrarea tranzactiilor."""

    @pytest.fixture
    def rm(self):
        return RiskManager()

    def test_record_trade_updates_counters(self, rm):
        """record_trade incrementeaza contorul si PnL-ul zilnic."""
        rm.record_trade(50.0)
        assert rm._daily_trades == 1
        assert rm._daily_pnl == 50.0

        rm.record_trade(-30.0)
        assert rm._daily_trades == 2
        assert rm._daily_pnl == 20.0

    def test_daily_stats(self, rm):
        """get_daily_stats returneaza statisticile corecte."""
        rm.record_trade(100.0)
        rm.record_trade(-40.0)
        stats = rm.get_daily_stats()
        assert stats["trades"] == 2
        assert stats["pnl"] == 60.0
