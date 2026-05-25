"""Analiza completa rezultate stress test."""
import pandas as pd
import glob
import sys

csv = sorted(glob.glob("results/stress_test_*.csv"))[-1] if glob.glob("results/stress_test_*.csv") else None
if not csv:
    sys.exit("Nu exista stress_test_*.csv inca")

print(f"Analizez: {csv}")
df = pd.read_csv(csv)
print(f"Total rezultate: {len(df)}\n")

# === 1) BLOW-UP analiza ===
blow_ups = df[df["blow_up"]].copy()
print("="*100)
print(f"  BLOW-UPS DETECTATE (la 10% DD din $5700)")
print("="*100)
if len(blow_ups) > 0:
    # Unique per combo+lot (independent of period)
    unique_bu = blow_ups.groupby(["symbol", "strategy", "tf", "lot"]).first().reset_index()
    print(f"Total BLOW-UP combinatii unice: {len(unique_bu)}")
    print(f"\nBLOW-UP-uri ordonate dupa lot (ASC) — primul lot la care strategia crapa:")
    print(f"{'Symbol':<14} {'Strategy':<14} {'TF':<5} {'MinLotBlow':>11} {'BlowDay':<12}")
    for _, r in unique_bu.sort_values("lot").iterrows():
        print(f"{r['symbol']:<14} {r['strategy']:<14} {r['tf']:<5} {r['lot']:>10.2f}  {r['blow_up_day']}")
else:
    print("Nicio combinatie nu a depasit DD 10% — toate sigure!")

# === 2) Lot maxim sigur per (sym, strat, TF) ===
print("\n" + "="*100)
print(f"  LOT MAXIM SIGUR (cel mai mare lot care NU a dus la blow-up)")
print("="*100)
no_blow = df[~df["blow_up"]].copy()
max_safe_lot = no_blow.groupby(["symbol", "strategy", "tf"])["lot"].max().reset_index()
max_safe_lot = max_safe_lot.sort_values("lot", ascending=False)
print(f"{'Symbol':<14} {'Strategy':<14} {'TF':<5} {'MaxSafeLot':>11}")
for _, r in max_safe_lot.iterrows():
    print(f"{r['symbol']:<14} {r['strategy']:<14} {r['tf']:<5} {r['lot']:>10.2f}")

# === 3) TOP 30 profit 90 zile NO blow-up ===
print("\n" + "="*120)
print(f"  TOP 30 NET PROFIT (90 zile, NO blow-up, min 5 trades)")
print("="*120)
df_90 = df[(df["period_days"] == 90) & (~df["blow_up"]) & (df["trades"] >= 5)].copy()
df_90 = df_90.sort_values("net_pnl", ascending=False).head(30)
print(f"{'#':>3} {'Symbol':<14} {'Strategy':<14} {'TF':<5} {'Lot':>6} {'Trades':>7} {'WR%':>6} {'NET':>9} {'DD':>9} {'DailyHits':>10}")
for i, (_, r) in enumerate(df_90.iterrows(), 1):
    print(f"{i:>3} {r['symbol']:<14} {r['strategy']:<14} {r['tf']:<5} {r['lot']:>5.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net_pnl']:>+7.2f} ${r['max_dd']:>+7.2f} {r['daily_limit_hits']:>10}")

# === 4) Scaling table — top 5 best combinations across lots ===
print("\n" + "="*120)
print(f"  SCALARE LOT — TOP 5 best combinations (90 zile)")
print("="*120)
# best (sym, strategy, tf) by max net_pnl at lot 0.05 (mediu)
best_combos = df[(df["period_days"] == 90) & (~df["blow_up"]) & (df["trades"] >= 5)].groupby(["symbol", "strategy", "tf"])["net_pnl"].max().reset_index()
best_combos = best_combos.sort_values("net_pnl", ascending=False).head(5)
for _, combo in best_combos.iterrows():
    print(f"\n  {combo['symbol']} {combo['strategy']} {combo['tf']}:")
    subset = df[(df["symbol"] == combo["symbol"]) & (df["strategy"] == combo["strategy"]) & (df["tf"] == combo["tf"]) & (df["period_days"] == 90)].sort_values("lot")
    print(f"  {'Lot':>6} {'Trades':>7} {'WR%':>6} {'NET':>9} {'DD':>9} {'BlowUp':>7}")
    for _, r in subset.iterrows():
        bu = "YES" if r["blow_up"] else "no"
        print(f"  {r['lot']:>5.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net_pnl']:>+7.2f} ${r['max_dd']:>+7.2f} {bu:>7}")

# === 5) Periodicitate — best lot per perioada ===
print("\n" + "="*120)
print(f"  BEST COMBINATIE PER PERIOADA (NET PnL maxim, NO blow-up, min 5 trades)")
print("="*120)
for period in sorted(df["period_days"].unique()):
    df_p = df[(df["period_days"] == period) & (~df["blow_up"]) & (df["trades"] >= 5)].copy()
    if len(df_p) == 0:
        print(f"\n  Perioada {period} zile: niciun rezultat valid")
        continue
    df_p = df_p.sort_values("net_pnl", ascending=False).head(5)
    print(f"\n  Perioada {period} zile — TOP 5:")
    print(f"  {'#':>3} {'Symbol':<14} {'Strategy':<14} {'TF':<5} {'Lot':>6} {'Trades':>7} {'WR%':>6} {'NET':>9}")
    for i, (_, r) in enumerate(df_p.iterrows(), 1):
        print(f"  {i:>3} {r['symbol']:<14} {r['strategy']:<14} {r['tf']:<5} {r['lot']:>5.2f} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net_pnl']:>+7.2f}")
print(f"\n\nDone analyzing {csv}")
