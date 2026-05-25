"""
Scan TOATE simbolurile broker si ranking dupa volatilitate normalizata (ATR%).
Pentru identificare top 50 cele mai volatile (excluse stocks - le-am scos deja).

Categorii: FOREX (majors + crosses), CRYPTO, METALS, INDICES, COMMODITIES
ATR% = ATR / pret * 100 (volatilitate % zilnica medie pe 90 zile)

Usage:
    .venv/Scripts/python.exe backtesting/scan_volatility.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta

LOOKBACK_DAYS = 90
ATR_PERIOD = 14
EXCLUDE_STOCKS = True  # Stocks au pierdut in real test, le excludem


def categorize(symbol_info):
    name = symbol_info.name.upper()
    path = (symbol_info.path or "").upper()

    if "CRYPTO" in path or any(c in name for c in ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "DOT", "LTC", "LINK", "AVAX", "BNB", "MATIC", "TRX", "UNI", "ATOM", "FIL", "AAVE", "SHIB"]):
        return "CRYPTO"
    if "METAL" in path or any(m in name for m in ["XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER", "PLATINUM"]):
        return "METALS"
    if "INDIC" in path or "INDIC" in (symbol_info.description or "").upper():
        return "INDICES"
    if any(c in name for c in ["BRENT", "WTI", "OIL", "NATGAS", "COCOA", "COFFEE", "SUGAR", "COTTON", "WHEAT", "CORN"]):
        return "COMMODITIES"
    # FOREX: 6 chars starting with currency code
    if len(name) >= 6:
        currencies = ["EUR", "USD", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "SEK", "NOK", "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH"]
        first3 = name[:3]
        if first3 in currencies:
            return "FOREX"
    return "STOCKS"


def calc_atr_pct(symbol, days=LOOKBACK_DAYS):
    """Calculeaza ATR% mediu pe ultimele N zile (D1 bars)."""
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days + 30)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_D1, utc_from, utc_to)
    if rates is None or len(rates) < ATR_PERIOD + 5:
        return None
    df = pd.DataFrame(rates)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=ATR_PERIOD)
    last_atrs = df["atr"].iloc[-min(30, len(df)):]
    last_closes = df["close"].iloc[-min(30, len(df)):]
    valid = pd.notna(last_atrs) & (last_closes > 0)
    if valid.sum() < 5:
        return None
    atr_pct = (last_atrs[valid] / last_closes[valid] * 100).mean()
    return float(atr_pct)


def main():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")

    syms = mt5.symbols_get()
    print(f"\nScanare {len(syms)} simboluri pe broker EarnBroker...")
    print(f"Calculez ATR% mediu pe {LOOKBACK_DAYS} zile (D1 timeframe)")
    print(f"Categorii excluse: {'STOCKS' if EXCLUDE_STOCKS else 'NONE'}")
    print("="*100)

    results = []
    count = 0
    for s in syms:
        cat = categorize(s)
        if EXCLUDE_STOCKS and cat == "STOCKS":
            continue
        count += 1
        if count % 20 == 0:
            print(f"  procesate {count}...")

        # Trebuie sa fie visible/selectabil
        if not s.visible:
            try:
                mt5.symbol_select(s.name, True)
            except:
                pass

        atr_pct = calc_atr_pct(s.name)
        if atr_pct is None:
            continue
        info = mt5.symbol_info(s.name)
        spread_pct = (info.spread * info.point / info.bid * 100) if info and info.bid > 0 else 0
        results.append({
            "symbol": s.name,
            "category": cat,
            "atr_pct": atr_pct,
            "spread_pct": spread_pct,
            "description": s.description or "",
            "swap_long": info.swap_long if info else 0,
            "swap_short": info.swap_short if info else 0,
        })

    print(f"\nTotal cu date valide: {len(results)}")

    # Sort by ATR% desc
    results.sort(key=lambda x: -x["atr_pct"])

    # TOP 50
    print(f"\n{'='*120}")
    print(f"  TOP 50 CELE MAI VOLATILE SIMBOLURI (excl. stocks) — ATR% mediu 30 zile")
    print(f"{'='*120}")
    print(f"{'#':>3} {'Symbol':<18} {'Category':<12} {'ATR%':>7} {'Spread%':>8} {'SwapL':>8} {'SwapS':>8}  Description")
    print(f"{'-'*120}")
    for i, r in enumerate(results[:50], 1):
        print(f"{i:>3} {r['symbol']:<18} {r['category']:<12} {r['atr_pct']:>6.2f}% {r['spread_pct']:>7.3f}% {r['swap_long']:>+7.2f} {r['swap_short']:>+7.2f}  {r['description'][:50]}")

    # Breakdown per categorie
    print(f"\n{'='*100}")
    print(f"  BREAKDOWN TOP 50 PER CATEGORIE")
    print(f"{'='*100}")
    cat_counts = {}
    for r in results[:50]:
        cat_counts[r["category"]] = cat_counts.get(r["category"], 0) + 1
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        avg_atr = sum(r["atr_pct"] for r in results[:50] if r["category"] == cat) / cnt
        print(f"  {cat}: {cnt} simboluri (ATR mediu {avg_atr:.2f}%)")

    # Save full results to CSV
    df_out = pd.DataFrame(results[:100])
    csv_path = "results/volatility_scan.csv"
    os.makedirs("results", exist_ok=True)
    df_out.to_csv(csv_path, index=False)
    print(f"\n  Top 100 salvat in: {csv_path}")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
