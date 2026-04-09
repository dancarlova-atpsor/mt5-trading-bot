"""
Rapoarte si grafice pentru backtesting.
"""

from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

from utils.logger import setup_logger

logger = setup_logger("reporter")


class BacktestReporter:
    """Genereaza rapoarte si grafice din rezultatele backtestului."""

    def __init__(self, results: dict, equity_curve: list[float], trades: list[dict]):
        self.results = results
        self.equity_curve = equity_curve
        self.trades = trades

    def print_report(self):
        """Afiseaza raportul in consola."""
        r = self.results
        sep = "=" * 50

        print(f"\n{sep}")
        print("       RAPORT BACKTEST")
        print(sep)
        print(f"  Balance initial:     {r['initial_balance']:>12,.2f}")
        print(f"  Balance final:       {r['final_balance']:>12,.2f}")
        print(f"  Profit net:          {r['net_profit']:>12,.2f}")
        print(sep)
        print(f"  Total tranzactii:    {r['total_trades']:>12}")
        print(f"  Castigatoare:        {r.get('wins', 0):>12}")
        print(f"  Pierdute:            {r.get('losses', 0):>12}")
        print(f"  Win rate:            {r['win_rate']:>11.1f}%")
        print(sep)
        print(f"  Profit brut:         {r.get('gross_profit', 0):>12,.2f}")
        print(f"  Pierdere bruta:      {r.get('gross_loss', 0):>12,.2f}")
        print(f"  Profit factor:       {r['profit_factor']:>12.2f}")
        print(sep)
        print(f"  Castig mediu:        {r['avg_win']:>12,.2f}")
        print(f"  Pierdere medie:      {r['avg_loss']:>12,.2f}")
        print(f"  Cel mai mare castig: {r['largest_win']:>12,.2f}")
        print(f"  Cea mai mare pierd.: {r['largest_loss']:>12,.2f}")
        print(sep)
        print(f"  Max drawdown:        {r['max_drawdown']:>12,.2f}")
        print(f"  Max drawdown %:      {r['max_drawdown_pct']:>11.2f}%")
        print(f"  Sharpe ratio:        {r['sharpe_ratio']:>12.2f}")
        print(f"{sep}\n")

    def plot_equity_curve(self, save_path: str = None):
        """Genereaza graficul curbei de capital."""
        if not self.equity_curve:
            logger.warning("Nu exista equity curve de afisat.")
            return

        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(self.equity_curve, color="#2196F3", linewidth=1.5, label="Capital")
        ax.axhline(
            y=self.results["initial_balance"],
            color="gray", linestyle="--", alpha=0.5,
            label=f"Balance initial ({self.results['initial_balance']:,.0f})"
        )

        # Marcheaza tranzactiile pe grafic
        trade_indices = []
        cumulative_bar = 0
        for trade in self.trades:
            if trade["pnl"] > 0:
                color = "#4CAF50"  # Verde pentru castig
                marker = "^"
            else:
                color = "#F44336"  # Rosu pentru pierdere
                marker = "v"
            # Aproximare index in equity curve
            if isinstance(trade.get("exit_time"), int):
                idx = min(trade["exit_time"], len(self.equity_curve) - 1)
                ax.scatter(idx, self.equity_curve[idx], color=color, marker=marker, s=30, zorder=5)

        ax.set_title("Curba Capitalului", fontsize=14, fontweight="bold")
        ax.set_xlabel("Bara")
        ax.set_ylabel("Capital")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        if save_path:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            plt.savefig(save_path, dpi=150)
            logger.info(f"Grafic salvat: {save_path}")
        else:
            plt.show()

        plt.close(fig)

    def export_trades_csv(self, filepath: str = "results/trades.csv"):
        """Exporta tranzactiile in CSV."""
        if not self.trades:
            logger.warning("Nu exista tranzactii de exportat.")
            return

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(self.trades)
        df.to_csv(filepath, index=False)
        logger.info(f"Tranzactii exportate: {filepath} ({len(self.trades)} randuri)")
