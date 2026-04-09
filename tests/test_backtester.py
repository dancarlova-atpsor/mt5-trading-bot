"""
Teste pentru motorul de backtesting.
"""

import numpy as np
import pandas as pd
import pytest

from backtesting.backtester import Backtester
from strategies.trend_following import TrendFollowing
from risk.risk_manager import RiskManager


def make_trending_data(n: int = 300, start: float = 1.1000, step: float = 0.0003) -> pd.DataFrame:
    """Creeaza date cu trend ascendent pentru testarea backtesterului."""
    np.random.seed(42)
    closes = [start + i * step + np.random.normal(0, 0.0001) for i in range(n)]
    data = {
        "open": [c - 0.0003 for c in closes],
        "high": [c + 0.0008 for c in closes],
        "low": [c - 0.0008 for c in closes],
        "close": closes,
        "volume": [1000 + i for i in range(n)],
    }
    index = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(data, index=index)


def make_volatile_data(n: int = 300, center: float = 1.1500) -> pd.DataFrame:
    """Creeaza date volatile cu reversii frecvente."""
    np.random.seed(123)
    closes = [center + 0.005 * np.sin(2 * np.pi * i / 30) + np.random.normal(0, 0.001) for i in range(n)]
    data = {
        "open": [c - 0.0005 for c in closes],
        "high": [c + 0.0015 for c in closes],
        "low": [c - 0.0015 for c in closes],
        "close": closes,
        "volume": [1000] * n,
    }
    index = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame(data, index=index)


class TestBacktester:
    """Teste pentru Backtester."""

    @pytest.fixture
    def strategy(self):
        params = {
            "ema_fast": 20,
            "ema_slow": 50,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "adx_threshold": 25,
            "adx_period": 14,
        }
        return TrendFollowing(params)

    @pytest.fixture
    def risk_manager(self):
        return RiskManager()

    def test_backtest_runs_without_error(self, strategy, risk_manager):
        """Backtestul ruleaza fara erori pe date sintetice."""
        data = make_trending_data(300)
        bt = Backtester(strategy, risk_manager, "EURUSD", initial_balance=10000)
        results = bt.run(data, lookback=60)
        assert results is not None
        assert isinstance(results, dict)

    def test_results_have_all_keys(self, strategy, risk_manager):
        """Rezultatele contin toate cheile necesare."""
        data = make_trending_data(300)
        bt = Backtester(strategy, risk_manager, "EURUSD")
        results = bt.run(data)

        expected_keys = [
            "total_trades", "win_rate", "net_profit", "profit_factor",
            "max_drawdown", "max_drawdown_pct", "sharpe_ratio",
            "avg_win", "avg_loss", "largest_win", "largest_loss",
            "initial_balance", "final_balance",
        ]
        for key in expected_keys:
            assert key in results, f"Cheia '{key}' lipseste din rezultate"

    def test_equity_curve_length(self, strategy, risk_manager):
        """Equity curve are lungimea corecta."""
        data = make_trending_data(300)
        lookback = 60
        bt = Backtester(strategy, risk_manager, "EURUSD")
        bt.run(data, lookback=lookback)
        assert len(bt.equity_curve) == len(data) - lookback

    def test_balance_changes_with_trades(self, strategy, risk_manager):
        """Balance final difera de initial daca au fost tranzactii."""
        data = make_volatile_data(300)
        bt = Backtester(strategy, risk_manager, "EURUSD", initial_balance=10000)
        results = bt.run(data)
        if results["total_trades"] > 0:
            # Balance ar trebui sa se fi schimbat
            assert results["final_balance"] != results["initial_balance"]

    def test_win_rate_valid_range(self, strategy, risk_manager):
        """Win rate este intre 0% si 100%."""
        data = make_trending_data(300)
        bt = Backtester(strategy, risk_manager, "EURUSD")
        results = bt.run(data)
        assert 0 <= results["win_rate"] <= 100

    def test_trades_list_populated(self, strategy, risk_manager):
        """Lista de tranzactii se populeaza corect."""
        data = make_volatile_data(300)
        bt = Backtester(strategy, risk_manager, "EURUSD")
        bt.run(data)
        for trade in bt.trades:
            assert "entry_price" in trade
            assert "exit_price" in trade
            assert "pnl" in trade
            assert "direction" in trade
            assert trade["direction"] in ("BUY", "SELL")

    def test_no_trades_on_flat_data(self, strategy, risk_manager):
        """Date complet plate nu genereaza semnale (ADX prea mic)."""
        closes = [1.1000] * 300
        data = pd.DataFrame({
            "open": closes,
            "high": [c + 0.0001 for c in closes],
            "low": [c - 0.0001 for c in closes],
            "close": closes,
            "volume": [1000] * 300,
        }, index=pd.date_range("2024-01-01", periods=300, freq="h"))

        bt = Backtester(strategy, risk_manager, "EURUSD")
        results = bt.run(data)
        assert results["total_trades"] == 0

    def test_max_drawdown_non_negative(self, strategy, risk_manager):
        """Max drawdown este >= 0."""
        data = make_trending_data(300)
        bt = Backtester(strategy, risk_manager, "EURUSD")
        results = bt.run(data)
        assert results["max_drawdown"] >= 0
        assert results["max_drawdown_pct"] >= 0
