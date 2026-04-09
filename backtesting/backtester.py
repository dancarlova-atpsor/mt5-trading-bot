"""
Motor de backtesting — simuleaza strategii pe date istorice bar-by-bar.
"""

import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal
from risk.risk_manager import RiskManager
from utils.logger import setup_logger
from utils.helpers import pips_to_price

logger = setup_logger("backtester")


class Backtester:
    """Simuleaza o strategie pe date istorice cu risk management."""

    def __init__(
        self,
        strategy: BaseStrategy,
        risk_manager: RiskManager,
        symbol: str,
        timeframe: str = "H1",
        initial_balance: float = 10000.0,
        spread_pips: float = 1.0,
    ):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.symbol = symbol
        self.timeframe = timeframe
        self.initial_balance = initial_balance
        self.spread_pips = spread_pips

        self.balance = initial_balance
        self.trades: list[dict] = []
        self.equity_curve: list[float] = []

    def run(self, data: pd.DataFrame, lookback: int = 60) -> dict:
        """Ruleaza backtestul bar-by-bar.

        Args:
            data: DataFrame cu OHLCV (index = time)
            lookback: Numarul minim de bare necesare pentru indicatori

        Returns:
            Dict cu rezultatele backtestului
        """
        logger.info(
            f"Start backtest: {self.strategy.name} pe {self.symbol} {self.timeframe} "
            f"| {len(data)} bare | Balance initial: {self.initial_balance}"
        )

        position = None  # O singura pozitie pe strategie la un moment dat
        pip_price = pips_to_price(self.symbol, 1.0)
        spread_price = pips_to_price(self.symbol, self.spread_pips)

        for i in range(lookback, len(data)):
            bar = data.iloc[i]
            window = data.iloc[:i + 1].copy()

            # Daca avem pozitie deschisa — verificam SL/TP
            if position is not None:
                closed = False

                if position["direction"] == "BUY":
                    # SL hit?
                    if bar["low"] <= position["sl"]:
                        exit_price = position["sl"]
                        closed = True
                    # TP hit?
                    elif bar["high"] >= position["tp"]:
                        exit_price = position["tp"]
                        closed = True
                else:  # SELL
                    # SL hit?
                    if bar["high"] >= position["sl"]:
                        exit_price = position["sl"]
                        closed = True
                    # TP hit?
                    elif bar["low"] <= position["tp"]:
                        exit_price = position["tp"]
                        closed = True

                if closed:
                    pnl = self._calculate_pnl(position, exit_price)
                    self.balance += pnl
                    self.trades.append({
                        "entry_time": position["entry_time"],
                        "exit_time": bar.name if hasattr(bar, "name") else i,
                        "direction": position["direction"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price,
                        "volume": position["volume"],
                        "sl": position["sl"],
                        "tp": position["tp"],
                        "pnl": round(pnl, 2),
                        "pnl_pips": round(pnl / (pip_price * position["volume"] * self._contract_size()), 1),
                    })
                    position = None

            # Daca nu avem pozitie — verificam semnal
            if position is None:
                signal_result = self.strategy.analyze(window)

                if signal_result != Signal.HOLD:
                    direction = signal_result.value
                    entry_price = bar["close"]

                    # Adauga spread la entry
                    if direction == "BUY":
                        entry_price += spread_price / 2
                    else:
                        entry_price -= spread_price / 2

                    # Calcul SL, TP, volume
                    sl = self.risk_manager.calculate_stop_loss(
                        window, direction, entry_price, self.symbol
                    )
                    tp = self.risk_manager.calculate_take_profit(entry_price, sl, direction, self.symbol)
                    sl_pips = abs(entry_price - sl) / pip_price
                    volume = self.risk_manager.calculate_position_size(
                        self.balance, sl_pips, self.symbol
                    )

                    position = {
                        "entry_time": bar.name if hasattr(bar, "name") else i,
                        "entry_price": entry_price,
                        "direction": direction,
                        "volume": volume,
                        "sl": sl,
                        "tp": tp,
                    }

            # Equity curve — balance + unrealized PnL
            unrealized = 0
            if position is not None:
                unrealized = self._calculate_pnl(position, bar["close"])
            self.equity_curve.append(self.balance + unrealized)

        # Inchide pozitia ramasa la sfarsit
        if position is not None:
            exit_price = data.iloc[-1]["close"]
            pnl = self._calculate_pnl(position, exit_price)
            self.balance += pnl
            self.trades.append({
                "entry_time": position["entry_time"],
                "exit_time": data.index[-1] if hasattr(data.index[-1], "strftime") else len(data) - 1,
                "direction": position["direction"],
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "volume": position["volume"],
                "sl": position["sl"],
                "tp": position["tp"],
                "pnl": round(pnl, 2),
                "pnl_pips": round(pnl / (pip_price * position["volume"] * self._contract_size()), 1),
            })

        logger.info(
            f"Backtest finalizat: {len(self.trades)} tranzactii | "
            f"Balance final: {self.balance:.2f}"
        )
        return self.get_results()

    def _contract_size(self) -> float:
        """Returneaza contract size pe baza simbolului."""
        sym = self.symbol.upper()
        if "XAU" in sym:
            return 100
        if "US500" in sym or "US30" in sym or "NAS" in sym:
            return 1
        return 100000

    def _calculate_pnl(self, position: dict, exit_price: float) -> float:
        """Calculeaza PnL pentru o pozitie."""
        contract = self._contract_size()
        if position["direction"] == "BUY":
            return (exit_price - position["entry_price"]) * position["volume"] * contract
        else:
            return (position["entry_price"] - exit_price) * position["volume"] * contract

    def get_results(self) -> dict:
        """Calculeaza si returneaza statisticile backtestului."""
        total_trades = len(self.trades)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "net_profit": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "max_drawdown": 0,
                "max_drawdown_pct": 0,
                "sharpe_ratio": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "largest_win": 0,
                "largest_loss": 0,
                "initial_balance": self.initial_balance,
                "final_balance": self.balance,
            }

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] <= 0]

        gross_profit = sum(t["pnl"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0

        # Max drawdown din equity curve
        max_dd, max_dd_pct = self._max_drawdown()

        # Sharpe ratio
        sharpe = self._sharpe_ratio()

        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / total_trades * 100, 1),
            "net_profit": round(self.balance - self.initial_balance, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "avg_win": round(gross_profit / len(wins), 2) if wins else 0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
            "largest_win": round(max(t["pnl"] for t in wins), 2) if wins else 0,
            "largest_loss": round(min(t["pnl"] for t in losses), 2) if losses else 0,
            "initial_balance": self.initial_balance,
            "final_balance": round(self.balance, 2),
        }

    def _max_drawdown(self) -> tuple[float, float]:
        """Calculeaza max drawdown absolut si procentual."""
        if not self.equity_curve:
            return 0.0, 0.0

        equity = np.array(self.equity_curve)
        peak = np.maximum.accumulate(equity)
        drawdown = peak - equity
        max_dd = float(np.max(drawdown))

        # Procentual
        dd_pct = drawdown / peak * 100
        max_dd_pct = float(np.max(dd_pct)) if peak.max() > 0 else 0.0

        return max_dd, max_dd_pct

    def _sharpe_ratio(self) -> float:
        """Calculeaza Sharpe ratio anualizat din equity curve."""
        if len(self.equity_curve) < 2:
            return 0.0

        equity = np.array(self.equity_curve)
        returns = np.diff(equity) / equity[:-1]

        if returns.std() == 0:
            return 0.0

        # Anualizare — presupunem 252 zile de trading
        return float(returns.mean() / returns.std() * np.sqrt(252))
