"""
Dashboard Trading Bot MT5 — Streamlit
Pagini: Status Live | Backtesting | Istoric Tranzactii
"""

import sys
from pathlib import Path

# Adauga root-ul proiectului in path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Trading Bot MT5",
    page_icon="📊",
    layout="wide",
)

# ============================================
# Sidebar — Navigare
# ============================================
page = st.sidebar.radio(
    "Navigare",
    ["📈 Status Live", "🔬 Backtesting", "📋 Istoric Tranzactii"],
)


# ============================================
# Helper: conectare MT5 (optional)
# ============================================
@st.cache_resource
def get_mt5_connection():
    """Incearca sa se conecteze la MT5. Returneaza None daca nu merge."""
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            return mt5
    except Exception:
        pass
    return None


def get_account_info(mt5_lib):
    """Returneaza info cont sau date mock."""
    if mt5_lib:
        info = mt5_lib.account_info()
        if info:
            return {
                "balance": info.balance,
                "equity": info.equity,
                "margin": info.margin,
                "free_margin": info.margin_free,
                "profit": info.profit,
                "currency": info.currency,
                "leverage": info.leverage,
            }
    # Date mock pentru demo/preview
    return {
        "balance": 10000.00,
        "equity": 10150.00,
        "margin": 250.00,
        "free_margin": 9900.00,
        "profit": 150.00,
        "currency": "USD",
        "leverage": 100,
    }


def get_open_positions(mt5_lib, magic=123456):
    """Returneaza pozitiile deschise sau date mock."""
    if mt5_lib:
        positions = mt5_lib.positions_get()
        if positions:
            return [
                {
                    "Ticket": p.ticket,
                    "Simbol": p.symbol,
                    "Tip": "BUY" if p.type == 0 else "SELL",
                    "Volume": p.volume,
                    "Pret Deschidere": p.price_open,
                    "Pret Curent": p.price_current,
                    "SL": p.sl,
                    "TP": p.tp,
                    "Profit": p.profit,
                }
                for p in positions
                if p.magic == magic
            ]
    return []


# ============================================
# PAGINA 1: Status Live
# ============================================
if page == "📈 Status Live":
    st.title("📈 Status Live")

    mt5 = get_mt5_connection()
    connected = mt5 is not None

    if connected:
        st.success("Conectat la MetaTrader 5")
    else:
        st.warning("Nu sunt conectat la MT5 — afisez date demo")

    # Metrici principale
    acc = get_account_info(mt5)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Balance", f"{acc['balance']:,.2f} {acc['currency']}")
    col2.metric("Equity", f"{acc['equity']:,.2f} {acc['currency']}")
    col3.metric("Profit Zilnic", f"{acc['profit']:+,.2f}", delta=f"{acc['profit']:+,.2f}")
    col4.metric("Free Margin", f"{acc['free_margin']:,.2f}")

    st.divider()

    # Pozitii deschise
    st.subheader("Pozitii Deschise")
    positions = get_open_positions(mt5)
    if positions:
        df_pos = pd.DataFrame(positions)
        # Coloreaza profitul
        st.dataframe(
            df_pos,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Profit": st.column_config.NumberColumn(format="%.2f"),
                "Pret Deschidere": st.column_config.NumberColumn(format="%.5f"),
                "Pret Curent": st.column_config.NumberColumn(format="%.5f"),
                "SL": st.column_config.NumberColumn(format="%.5f"),
                "TP": st.column_config.NumberColumn(format="%.5f"),
            },
        )
        total_profit = sum(p["Profit"] for p in positions)
        st.info(f"Total profit pozitii deschise: **{total_profit:+,.2f} {acc['currency']}**")
    else:
        st.info("Nicio pozitie deschisa.")

    # Auto-refresh
    if st.sidebar.checkbox("Auto-refresh (10s)", value=False):
        st.rerun()


# ============================================
# PAGINA 2: Backtesting
# ============================================
elif page == "🔬 Backtesting":
    st.title("🔬 Backtesting")

    col1, col2, col3 = st.columns(3)

    with col1:
        strategy_name = st.selectbox(
            "Strategie",
            ["Trend Following", "Mean Reversion", "Scalping"],
        )
    with col2:
        symbol = st.selectbox("Simbol", ["EURUSD", "GBPUSD", "XAUUSD", "US500"])
    with col3:
        initial_balance = st.number_input("Balance Initial", value=10000, step=1000)

    col4, col5 = st.columns(2)
    with col4:
        bars = st.slider("Numar bare", 200, 2000, 500, step=100)
    with col5:
        spread = st.number_input("Spread (pips)", value=1.0, step=0.5)

    if st.button("Ruleaza Backtest", type="primary", use_container_width=True):
        with st.spinner("Rulez backtestul..."):
            try:
                from utils.helpers import load_config
                from risk.risk_manager import RiskManager
                from backtesting.backtester import Backtester

                strategies_config = load_config("strategies.yaml")
                risk_manager = RiskManager()

                # Instantiaza strategia
                strategy_map = {
                    "Trend Following": ("trend_following", "strategies.trend_following", "TrendFollowing"),
                    "Mean Reversion": ("mean_reversion", "strategies.mean_reversion", "MeanReversion"),
                    "Scalping": ("scalping", "strategies.scalping", "Scalping"),
                }
                key, module_name, class_name = strategy_map[strategy_name]
                import importlib
                mod = importlib.import_module(module_name)
                cls = getattr(mod, class_name)
                strategy = cls(strategies_config[key])

                # Incearca date reale de la MT5
                mt5 = get_mt5_connection()
                data = None
                if mt5:
                    from core.data_fetcher import DataFetcher
                    fetcher = DataFetcher()
                    tf = strategies_config[key].get("timeframe", "H1")
                    data = fetcher.get_rates(symbol, tf, bars)

                # Fallback: date sintetice
                if data is None:
                    st.info("MT5 indisponibil — folosesc date sintetice pentru demo")
                    np.random.seed(42)
                    n = bars
                    closes = [1.1000 + i * 0.0002 + np.random.normal(0, 0.001) for i in range(n)]
                    data = pd.DataFrame({
                        "open": [c - 0.0003 for c in closes],
                        "high": [c + 0.0008 for c in closes],
                        "low": [c - 0.0008 for c in closes],
                        "close": closes,
                        "volume": [1000 + i for i in range(n)],
                    }, index=pd.date_range("2024-01-01", periods=n, freq="h"))

                # Ruleaza backtestul
                bt = Backtester(
                    strategy, risk_manager, symbol,
                    initial_balance=initial_balance,
                    spread_pips=spread,
                )
                results = bt.run(data, lookback=60)

                # Afiseaza rezultatele
                st.success(f"Backtest finalizat: {results['total_trades']} tranzactii")

                # Metrici
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Profit Net", f"{results['net_profit']:+,.2f}")
                m2.metric("Win Rate", f"{results['win_rate']:.1f}%")
                m3.metric("Profit Factor", f"{results['profit_factor']:.2f}")
                m4.metric("Sharpe Ratio", f"{results['sharpe_ratio']:.2f}")

                m5, m6, m7, m8 = st.columns(4)
                m5.metric("Max Drawdown", f"{results['max_drawdown']:,.2f}")
                m6.metric("Max DD %", f"{results['max_drawdown_pct']:.2f}%")
                m7.metric("Castig Mediu", f"{results['avg_win']:,.2f}")
                m8.metric("Pierdere Medie", f"{results['avg_loss']:,.2f}")

                st.divider()

                # Equity Curve cu Plotly
                st.subheader("Curba Capitalului")
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=bt.equity_curve,
                    mode="lines",
                    name="Capital",
                    line=dict(color="#2196F3", width=2),
                ))
                fig.add_hline(
                    y=initial_balance,
                    line_dash="dash",
                    line_color="gray",
                    annotation_text=f"Balance initial ({initial_balance:,})",
                )

                # Marcheaza tranzactiile
                for trade in bt.trades:
                    if isinstance(trade.get("exit_time"), int):
                        idx = min(trade["exit_time"] - 60, len(bt.equity_curve) - 1)
                        if 0 <= idx < len(bt.equity_curve):
                            color = "#4CAF50" if trade["pnl"] > 0 else "#F44336"
                            symbol_marker = "triangle-up" if trade["direction"] == "BUY" else "triangle-down"
                            fig.add_trace(go.Scatter(
                                x=[idx], y=[bt.equity_curve[idx]],
                                mode="markers",
                                marker=dict(color=color, size=8, symbol=symbol_marker),
                                name=f"{trade['direction']} {trade['pnl']:+.2f}",
                                showlegend=False,
                            ))

                fig.update_layout(
                    height=400,
                    margin=dict(l=0, r=0, t=30, b=0),
                    xaxis_title="Bara",
                    yaxis_title="Capital",
                )
                st.plotly_chart(fig, use_container_width=True)

                # Tabel tranzactii
                st.subheader("Detalii Tranzactii")
                if bt.trades:
                    df_trades = pd.DataFrame(bt.trades)
                    st.dataframe(
                        df_trades,
                        use_container_width=True,
                        hide_index=True,
                    )

                    # Distributie PnL
                    st.subheader("Distributie PnL")
                    pnls = [t["pnl"] for t in bt.trades]
                    fig_hist = go.Figure()
                    fig_hist.add_trace(go.Histogram(
                        x=pnls,
                        nbinsx=20,
                        marker_color=["#4CAF50" if p > 0 else "#F44336" for p in sorted(pnls)],
                    ))
                    fig_hist.update_layout(
                        height=300,
                        margin=dict(l=0, r=0, t=10, b=0),
                        xaxis_title="PnL",
                        yaxis_title="Frecventa",
                    )
                    st.plotly_chart(fig_hist, use_container_width=True)

            except Exception as e:
                st.error(f"Eroare la backtest: {e}")


# ============================================
# PAGINA 3: Istoric Tranzactii
# ============================================
elif page == "📋 Istoric Tranzactii":
    st.title("📋 Istoric Tranzactii")

    # Cauta fisiere CSV cu rezultate
    results_dir = ROOT / "results"
    csv_files = list(results_dir.glob("*.csv")) if results_dir.exists() else []

    if csv_files:
        selected_file = st.selectbox(
            "Selecteaza fisierul",
            csv_files,
            format_func=lambda x: x.name,
        )

        df = pd.read_csv(selected_file)
        st.info(f"Incarcat {len(df)} tranzactii din {selected_file.name}")

        # Filtre
        col1, col2, col3 = st.columns(3)
        with col1:
            if "direction" in df.columns:
                directions = ["Toate"] + df["direction"].unique().tolist()
                sel_dir = st.selectbox("Directie", directions)
                if sel_dir != "Toate":
                    df = df[df["direction"] == sel_dir]

        with col2:
            if "entry_time" in df.columns:
                # Sortare cronologica
                df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")

        with col3:
            if "pnl" in df.columns:
                pnl_filter = st.selectbox("Filtru PnL", ["Toate", "Castiguri", "Pierderi"])
                if pnl_filter == "Castiguri":
                    df = df[df["pnl"] > 0]
                elif pnl_filter == "Pierderi":
                    df = df[df["pnl"] <= 0]

        # Statistici rapide
        if "pnl" in df.columns and len(df) > 0:
            total_pnl = df["pnl"].sum()
            wins = len(df[df["pnl"] > 0])
            losses = len(df[df["pnl"] <= 0])
            win_rate = wins / len(df) * 100 if len(df) > 0 else 0

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Total PnL", f"{total_pnl:+,.2f}")
            m2.metric("Tranzactii", f"{len(df)}")
            m3.metric("Win Rate", f"{win_rate:.1f}%")
            m4.metric("W/L", f"{wins}/{losses}")

        # Tabel
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Export
        csv_data = df.to_csv(index=False)
        st.download_button(
            "Descarca CSV filtrat",
            csv_data,
            file_name="trades_filtered.csv",
            mime="text/csv",
        )

    else:
        st.info(
            "Nu exista fisiere CSV cu tranzactii.\n\n"
            "Ruleaza un backtest si exporta rezultatele, sau foloseste botul live "
            "pentru a genera date."
        )

        # Optiune de a genera date demo
        if st.button("Genereaza date demo"):
            results_dir.mkdir(parents=True, exist_ok=True)
            np.random.seed(42)
            n = 50
            demo_trades = pd.DataFrame({
                "entry_time": pd.date_range("2024-01-01", periods=n, freq="12h"),
                "exit_time": pd.date_range("2024-01-01 06:00", periods=n, freq="12h"),
                "direction": np.random.choice(["BUY", "SELL"], n),
                "entry_price": np.random.uniform(1.05, 1.15, n).round(5),
                "exit_price": np.random.uniform(1.05, 1.15, n).round(5),
                "volume": [0.01] * n,
                "pnl": np.random.normal(10, 50, n).round(2),
                "pnl_pips": np.random.normal(5, 25, n).round(1),
            })
            demo_path = results_dir / "demo_trades.csv"
            demo_trades.to_csv(demo_path, index=False)
            st.success(f"Date demo generate: {demo_path}")
            st.rerun()
