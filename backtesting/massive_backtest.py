"""
Backtest MASIV: 28 simboluri × 3 strategii × 5 TF × 6 perioade.

Simboluri (28):
- Crypto (5): BTCUSD, ETHUSD, SOLUSD, DSHUSD, AVEUSD — inchidere 22:00 RO
- Metals (5): XAUUSD, XAGUSD, XPTUSD, XPDUSD, XAGEUR
- Commodities (3): WTI, BRENT, COFFEE
- Indices (2): NIKKEI, US2000
- Forex (13): NZDUSD, AUDUSD, AUDJPY, NZDJPY, USDCHF, GBPUSD, USDJPY, CHFJPY,
              GBPJPY, GBPAUD, AUDCAD, EURUSD, EURGBP, USDCAD, EURJPY
  (1 e contine cele 15 dar EURUSD/EURJPY nu existau in scan -> verific la rulare)

Strategii (3): TrendFollowing, Breakout, MeanReversion
TF (5): M15, M30, H1, H4, D1
Perioade (6): 30, 60, 90, 120, 150, 180 zile (calculate din slice de 180 zile)

Output: results/massive_backtest_*.csv
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

from risk.risk_manager import RiskManager
from strategies.trend_following import TrendFollowing
from strategies.breakout import Breakout
from strategies.mean_reversion import MeanReversion
from strategies.base_strategy import Signal
from utils.helpers import pips_to_price

# ============================================
# CONFIG
# ============================================

CRYPTO_CLOSE_TIME_RO = 22  # ora RO la care fortam close pe crypto
BUCHAREST_TZ = ZoneInfo("Europe/Bucharest")

SYMBOLS = {
    "CRYPTO": ["BTCUSD.cfd", "ETHUSD.cfd", "SOLUSD.cfd", "DSHUSD.cfd", "AVEUSD.cfd"],
    "METALS": ["XAUUSD.cfd", "XAGUSD.cfd", "XPTUSD.cfd", "XPDUSD.cfd", "XAGEUR.cfd"],
    "COMMODITIES": ["WTI.cfd", "BRENT.cfd", "COFFEE.cfd"],
    "INDICES": ["NIKKEI.cfd", "US2000.cfd"],
    "FOREX": [
        "NZDUSD.cfd", "AUDUSD.cfd", "AUDJPY.cfd", "NZDJPY.cfd",
        "USDCHF.cfd", "GBPUSD.cfd", "USDJPY.cfd", "CHFJPY.cfd",
        "GBPJPY.cfd", "GBPAUD.cfd", "AUDCAD.cfd", "EURUSD.cfd",
        "EURGBP.cfd", "USDCAD.cfd", "EURJPY.cfd"
    ]
}

TF_MAP = {
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1":  mt5.TIMEFRAME_H1,
    "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

PERIODS_DAYS = [30, 60, 90, 120, 150, 180]
LOOKBACK_DAYS = max(PERIODS_DAYS) + 30  # 210 zile (cu buffer pentru indicator warmup)

INITIAL_BALANCE = 5700.0
LOT_FIXED = 0.01  # lot fix pentru toate (sa fie comparabil)

STRATEGY_PARAMS = {
    "TrendFollowing": {
        "ema_fast": 20, "ema_slow": 50,
        "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
        "adx_threshold": 25, "adx_period": 14
    },
    "Breakout": {
        "lookback_period": 50, "atr_period": 14,
        "min_atr_ratio": 0.8, "min_vol_ratio": 1.5
    },
    "MeanReversion": {
        "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70,
        "bb_period": 20, "bb_std_dev": 2.0
    }
}


# ============================================
# Functii utilitare
# ============================================

def init_mt5():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")
    info = mt5.account_info()
    print(f"MT5 conectat: cont {info.login if info else '?'}\n")


def is_symbol_available(symbol):
    info = mt5.symbol_info(symbol)
    if info is None:
        return False
    if not info.visible:
        try:
            return mt5.symbol_select(symbol, True)
        except:
            return False
    return True


def download_data(symbol, tf, days):
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, TF_MAP[tf], utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def contract_size(symbol):
    """Detect contract size from broker."""
    info = mt5.symbol_info(symbol)
    if info and info.trade_contract_size > 0:
        return float(info.trade_contract_size)
    name = symbol.upper()
    if "XAU" in name or "XAG" in name:
        return 100
    if any(c in name for c in ["BTC","ETH","SOL","DSH","AVE","BNB","XRP","DOT","ADA"]):
        return 1
    return 100000


def pnl_usd(symbol, direction, open_price, close_price, lot):
    """Calculeaza PnL in USD folosind tick_value de la broker.
    FIX 2026-05-25: Vechea formula returna in quote currency (JPY) etichetat USD.
    Acum foloseste tick_value (in moneda contului = USD) pentru calcul corect.
    """
    info = mt5.symbol_info(symbol)
    if info is None or info.trade_tick_size == 0 or info.trade_tick_value == 0:
        # Fallback la vechea formula daca nu putem obtine tick info
        contract = contract_size(symbol)
        sign = 1 if direction == "BUY" else -1
        pnl_quote = sign * (close_price - open_price) * lot * contract
        return pnl_quote

    sign = 1 if direction == "BUY" else -1
    price_diff = sign * (close_price - open_price)
    # Convert price diff to ticks, multiply by tick_value (USD) and lot
    ticks = price_diff / info.trade_tick_size
    pnl = ticks * info.trade_tick_value * lot
    return pnl


def get_atr(data, period=14):
    """Calculate ATR safely."""
    if len(data) < period + 1:
        return None
    atr = ta.atr(data["high"], data["low"], data["close"], length=period)
    val = atr.iloc[-1]
    return float(val) if pd.notna(val) and val > 0 else None


def calc_sl_tp(symbol, direction, entry, data, rr_ratio=2.0, atr_mult=1.5):
    """SL/TP based on ATR."""
    atr = get_atr(data, 14)
    if atr is None:
        return None, None
    sl_distance = atr * atr_mult
    tp_distance = sl_distance * rr_ratio
    if direction == "BUY":
        return entry - sl_distance, entry + tp_distance
    return entry + sl_distance, entry - tp_distance


# ============================================
# Backtest simplu cu intraday close pentru crypto
# ============================================

def run_backtest(symbol, strategy, strategy_name, df, is_crypto=False, rr_ratio=2.0):
    """Backtest bar-by-bar. Returneaza lista de trade-uri."""
    trades = []
    position = None
    lookback = 60

    for i in range(lookback, len(df)):
        bar = df.iloc[i]
        bar_time = df.index[i]
        # bar_time este UTC (din MT5)
        # Convert to RO time for crypto close check
        if hasattr(bar_time, "to_pydatetime"):
            bar_dt_utc = bar_time.to_pydatetime().replace(tzinfo=ZoneInfo("UTC"))
            bar_dt_ro = bar_dt_utc.astimezone(BUCHAREST_TZ)
        else:
            bar_dt_ro = None

        # Verifica pozitie deschisa: SL/TP hit OR intraday close
        if position is not None:
            closed = False
            exit_price = None
            exit_reason = None

            if position["direction"] == "BUY":
                if bar["low"] <= position["sl"]:
                    exit_price = position["sl"]
                    exit_reason = "SL"
                    closed = True
                elif bar["high"] >= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "TP"
                    closed = True
            else:
                if bar["high"] >= position["sl"]:
                    exit_price = position["sl"]
                    exit_reason = "SL"
                    closed = True
                elif bar["low"] <= position["tp"]:
                    exit_price = position["tp"]
                    exit_reason = "TP"
                    closed = True

            # Crypto intraday close at 22:00 RO
            if not closed and is_crypto and bar_dt_ro and bar_dt_ro.hour >= CRYPTO_CLOSE_TIME_RO:
                exit_price = bar["close"]
                exit_reason = "INTRADAY_CLOSE"
                closed = True

            if closed:
                pnl = pnl_usd(symbol, position["direction"], position["entry"], exit_price, LOT_FIXED)
                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": bar_time,
                    "direction": position["direction"],
                    "entry": position["entry"],
                    "exit": exit_price,
                    "pnl": pnl,
                    "reason": exit_reason
                })
                position = None

        # Open new position if no position
        if position is None:
            window = df.iloc[:i + 1].copy()
            try:
                signal = strategy.analyze(window)
            except Exception:
                continue
            if signal == Signal.HOLD:
                continue
            direction = signal.value
            entry = float(bar["close"])
            sl, tp = calc_sl_tp(symbol, direction, entry, window, rr_ratio=rr_ratio)
            if sl is None or tp is None:
                continue
            position = {
                "entry_time": bar_time,
                "direction": direction,
                "entry": entry,
                "sl": sl,
                "tp": tp,
            }

    return trades


# ============================================
# MAIN
# ============================================

def main():
    init_mt5()

    all_symbols = []
    for cat, lst in SYMBOLS.items():
        for sym in lst:
            all_symbols.append((cat, sym))

    print(f"Total simboluri configurate: {len(all_symbols)}")
    print(f"Verificare disponibilitate pe broker...")

    available = []
    for cat, sym in all_symbols:
        if is_symbol_available(sym):
            available.append((cat, sym))
        else:
            print(f"  SKIP: {sym} (nu disponibil)")
    print(f"Disponibile: {len(available)}\n")

    # Pre-download data pentru toate (sa nu re-descarcam pe fiecare strategie)
    print(f"Pre-download date pe {LOOKBACK_DAYS} zile pentru toate (sym, TF)...")
    data_cache = {}
    for cat, sym in available:
        for tf_name in TF_MAP:
            df = download_data(sym, tf_name, LOOKBACK_DAYS)
            if df is not None and len(df) > 100:
                data_cache[(sym, tf_name)] = df
        print(f"  {sym}: {sum(1 for k in data_cache if k[0]==sym)} TF-uri")
    print(f"\nTotal date pre-incarcate: {len(data_cache)} combinatii (sym × TF)")

    # Run backtests
    print(f"\nIncep backtest masiv: {len(available)} sym × 3 strat × 5 TF = {len(available)*15} backtest-uri")
    print(f"="*120)

    results = []
    count = 0
    total = len(available) * 3 * 5

    for cat, symbol in available:
        is_crypto = (cat == "CRYPTO")
        for strat_name in ["TrendFollowing", "Breakout", "MeanReversion"]:
            params = STRATEGY_PARAMS[strat_name]
            for tf_name in TF_MAP:
                count += 1
                df = data_cache.get((symbol, tf_name))
                if df is None or len(df) < 100:
                    continue

                # Create strategy instance
                if strat_name == "TrendFollowing":
                    strategy = TrendFollowing(params=params)
                elif strat_name == "Breakout":
                    strategy = Breakout(params=params)
                elif strat_name == "MeanReversion":
                    strategy = MeanReversion(params=params)
                else:
                    continue

                try:
                    trades = run_backtest(symbol, strategy, strat_name, df, is_crypto=is_crypto)
                except Exception as e:
                    continue

                # Calculate metrics per period
                if len(trades) == 0:
                    for period_days in PERIODS_DAYS:
                        results.append({
                            "symbol": symbol, "category": cat,
                            "strategy": strat_name, "tf": tf_name,
                            "period_days": period_days,
                            "trades": 0, "wins": 0, "losses": 0, "wr": 0,
                            "net_pnl": 0, "avg_win": 0, "avg_loss": 0,
                            "max_dd": 0, "intraday_closes": 0
                        })
                    continue

                # Filter per period (last N days)
                now = df.index[-1]
                for period_days in PERIODS_DAYS:
                    cutoff = now - pd.Timedelta(days=period_days)
                    period_trades = [t for t in trades if t["entry_time"] >= cutoff]
                    if len(period_trades) == 0:
                        results.append({
                            "symbol": symbol, "category": cat,
                            "strategy": strat_name, "tf": tf_name,
                            "period_days": period_days,
                            "trades": 0, "wins": 0, "losses": 0, "wr": 0,
                            "net_pnl": 0, "avg_win": 0, "avg_loss": 0,
                            "max_dd": 0, "intraday_closes": 0
                        })
                        continue

                    wins = [t for t in period_trades if t["pnl"] > 0]
                    losses = [t for t in period_trades if t["pnl"] <= 0]
                    intraday = sum(1 for t in period_trades if t["reason"] == "INTRADAY_CLOSE")
                    net = sum(t["pnl"] for t in period_trades)
                    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
                    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

                    # Max drawdown
                    equity = 0
                    peak = 0
                    max_dd = 0
                    for t in period_trades:
                        equity += t["pnl"]
                        if equity > peak:
                            peak = equity
                        dd = peak - equity
                        if dd > max_dd:
                            max_dd = dd

                    results.append({
                        "symbol": symbol, "category": cat,
                        "strategy": strat_name, "tf": tf_name,
                        "period_days": period_days,
                        "trades": len(period_trades),
                        "wins": len(wins), "losses": len(losses),
                        "wr": round(len(wins) / len(period_trades) * 100, 1),
                        "net_pnl": round(net, 2),
                        "avg_win": round(avg_win, 2),
                        "avg_loss": round(avg_loss, 2),
                        "max_dd": round(max_dd, 2),
                        "intraday_closes": intraday
                    })

                if count % 30 == 0:
                    print(f"  Progres: {count}/{total} ({count/total*100:.0f}%) — ultim: {symbol} {strat_name} {tf_name}: {len(trades)} trades")

    # Save results
    df_results = pd.DataFrame(results)
    os.makedirs("results", exist_ok=True)
    output_csv = f"results/massive_backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df_results.to_csv(output_csv, index=False)
    print(f"\n{'='*120}")
    print(f"Total rezultate: {len(results)} rows in {output_csv}")

    # TOP 20 best by NET PnL (period 90 zile)
    df_90 = df_results[df_results["period_days"] == 90].copy()
    df_90 = df_90.sort_values("net_pnl", ascending=False)
    print(f"\n{'='*120}")
    print(f"  TOP 20 PROFITABILE — perioada 90 zile (per combinatie sym+strat+TF)")
    print(f"{'='*120}")
    print(f"{'#':>3} {'Symbol':<14} {'Cat':<12} {'Strategy':<14} {'TF':<5} {'Trades':>7} {'WR%':>6} {'NET':>10} {'AvgW':>8} {'AvgL':>8} {'DD':>8}")
    for i, (_, r) in enumerate(df_90.head(20).iterrows(), 1):
        print(f"{i:>3} {r['symbol']:<14} {r['category']:<12} {r['strategy']:<14} {r['tf']:<5} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net_pnl']:>+8.2f} ${r['avg_win']:>+6.2f} ${r['avg_loss']:>+6.2f} ${r['max_dd']:>+6.2f}")

    print(f"\n  Detaliu complet salvat in: {output_csv}")
    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
