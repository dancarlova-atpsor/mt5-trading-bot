"""
Backtest strategia DCA + HEDGE discutata cu userul:
  - BUY #1 0.01 lot la inceputul ciclului
  - Daca pretul scade 2% → BUY #2 0.02 lot
  - Daca scade inca 2% (-4% total) → BUY #3 0.03 lot
  - Daca scade inca 2% (-6% total) → BUY #4 0.04 lot + SELL HEDGE 0.10 lot
  - Exit:
      a) TAKE PROFIT: pret revine la breakeven mediu + 0.3% margin → inchide tot
      b) HEDGE PROFIT: pret continua jos, SELL hedge atinge $50 profit → inchide hedge,
         lasa BUY-urile sa astepte revenirea (pana la stop loss total)
      c) STOP LOSS HARD: floating loss > $300 → inchide tot in pierdere
  - Test si pe directia opusa (SELL grid + BUY hedge) pentru ranging perfect

Usage:
    .venv/Scripts/python.exe backtesting/dca_hedge_backtest.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd

SYMBOLS = ["XAUUSD.cfd", "USDCHF.cfd", "USDCAD.cfd", "XAGUSD.cfd"]
LOOKBACK_DAYS = 90
INITIAL_BALANCE = 5700.0

# DCA params
LEVEL_DISTANCE_PCT = 2.0  # 2% intre trepte
LOT_PROGRESSION = [0.01, 0.02, 0.03, 0.04]  # aritmetic
HEDGE_LOT = 0.10  # = sum(LOT_PROGRESSION)
HEDGE_TRIGGER_LEVEL = 4  # la treapta 4 (-6%) declansam hedge

# Exit params
TP_MARGIN_PCT = 0.3  # inchide ciclu cand pret > avg_entry * (1 + 0.3%)
HEDGE_PROFIT_TARGET = 50.0  # inchide hedge cand profit > $50
HARD_STOP_LOSS = -300.0  # daca floating < -$300, inchide tot

CONTRACT_SIZE = {
    "XAUUSD.cfd": 100,
    "XAGUSD.cfd": 5000,
    "USDCHF.cfd": 100000,
    "USDCAD.cfd": 100000,
}


def init_mt5():
    if not mt5.initialize():
        sys.exit("MT5 nu se conecteaza")
    info = mt5.account_info()
    print(f"MT5 conectat: cont {info.login if info else '?'}")


def download_h1(symbol, days):
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def pnl_for_position(symbol, direction, open_price, current_price, lot, contract):
    """P&L in moneda contului (USD) pentru o pozitie."""
    if direction == "BUY":
        diff = current_price - open_price
    else:
        diff = open_price - current_price
    pnl_quote = diff * lot * contract
    # Pentru USDCHF, USDCAD: quote currency = CHF/CAD, convert la USD
    if symbol in ("USDCHF.cfd", "USDCAD.cfd"):
        return pnl_quote / current_price
    # XAU, XAG: quote = USD direct
    return pnl_quote


def simulate_dca_cycle(symbol, df, direction="BUY"):
    """Simuleaza grid DCA in directia data. Returneaza lista de cicluri (cu rezultat fiecare)."""
    contract = CONTRACT_SIZE[symbol]
    opposite = "SELL" if direction == "BUY" else "BUY"
    cycles = []

    i = 0
    while i < len(df):
        # Start ciclu nou
        entry_price = df.iloc[i]["close"]
        positions = []  # lista pozitii deschise
        # BUY #1
        positions.append({
            "level": 1, "direction": direction,
            "open_price": entry_price, "lot": LOT_PROGRESSION[0]
        })
        cycle_start = df.index[i]
        max_floating_loss = 0
        hedge_open = None  # pozitia hedge (daca declansata)
        cycle_outcome = None

        for j in range(i + 1, len(df)):
            current = df.iloc[j]["close"]
            high = df.iloc[j]["high"]
            low = df.iloc[j]["low"]

            # Verifica daca trebuie deschis nivel nou (pret in directie ADVERSA)
            if len(positions) < 4 and not hedge_open:
                next_level = len(positions) + 1
                if direction == "BUY":
                    trigger_price = entry_price * (1 - LEVEL_DISTANCE_PCT/100 * (next_level - 1))
                    triggered = low <= trigger_price
                else:
                    trigger_price = entry_price * (1 + LEVEL_DISTANCE_PCT/100 * (next_level - 1))
                    triggered = high >= trigger_price

                if triggered:
                    positions.append({
                        "level": next_level, "direction": direction,
                        "open_price": trigger_price, "lot": LOT_PROGRESSION[next_level - 1]
                    })

                    # La treapta 4 → hedge
                    if next_level == HEDGE_TRIGGER_LEVEL:
                        hedge_open = {
                            "direction": opposite,
                            "open_price": trigger_price,
                            "lot": HEDGE_LOT
                        }

            # Calcul floating P&L total
            total_pnl = sum(
                pnl_for_position(symbol, p["direction"], p["open_price"], current, p["lot"], contract)
                for p in positions
            )
            hedge_pnl = 0
            if hedge_open:
                hedge_pnl = pnl_for_position(symbol, hedge_open["direction"], hedge_open["open_price"], current, hedge_open["lot"], contract)
            floating = total_pnl + hedge_pnl

            if floating < max_floating_loss:
                max_floating_loss = floating

            # Exit conditions
            avg_entry = sum(p["open_price"] * p["lot"] for p in positions) / sum(p["lot"] for p in positions)
            tp_price = avg_entry * (1 + TP_MARGIN_PCT/100) if direction == "BUY" else avg_entry * (1 - TP_MARGIN_PCT/100)

            # 1) TP: pret revine peste media + margin (BUY) sau sub (SELL)
            tp_hit = (direction == "BUY" and high >= tp_price) or (direction == "SELL" and low <= tp_price)
            if tp_hit and not hedge_open:
                # Inchide ciclu pe profit (toate pozitiile)
                exit_price = tp_price
                final_pnl = sum(
                    pnl_for_position(symbol, p["direction"], p["open_price"], exit_price, p["lot"], contract)
                    for p in positions
                )
                cycle_outcome = {
                    "start": cycle_start, "end": df.index[j],
                    "outcome": "TP", "pnl": final_pnl,
                    "levels_opened": len(positions), "had_hedge": False,
                    "max_floating_loss": max_floating_loss
                }
                i = j + 1
                break

            # 2) Hedge profit target — cand hedge profitabil > 50, inchide hedge
            if hedge_open and hedge_pnl >= HEDGE_PROFIT_TARGET:
                # Inchide doar hedge-ul, lasa restul
                # Adaugam profit-ul hedge-ului si continuam cu BUY-urile
                # Apoi tracking: cand BUY-urile ajung la TP, inchidem si pe ele
                # Simplificare: la inchiderea hedge-ului, cumulam profit si continuam ciclul
                hedge_realized = hedge_pnl
                hedge_open = None
                # Continue tracking BUY-urile separat
                # Adaugam profit hedge la final calcul
                positions[0]["hedge_realized"] = hedge_realized  # store

            # 3) Hard stop loss
            if floating <= HARD_STOP_LOSS:
                exit_price = current
                buy_pnl = sum(
                    pnl_for_position(symbol, p["direction"], p["open_price"], exit_price, p["lot"], contract)
                    for p in positions
                )
                hedge_realized = positions[0].get("hedge_realized", 0)
                if hedge_open:
                    hedge_realized += pnl_for_position(symbol, hedge_open["direction"], hedge_open["open_price"], exit_price, hedge_open["lot"], contract)
                final_pnl = buy_pnl + hedge_realized
                cycle_outcome = {
                    "start": cycle_start, "end": df.index[j],
                    "outcome": "HARD_SL", "pnl": final_pnl,
                    "levels_opened": len(positions), "had_hedge": "hedge_realized" in positions[0] or False,
                    "max_floating_loss": max_floating_loss
                }
                i = j + 1
                break

            # 4) After hedge realized, daca BUY-urile ajung la breakeven cu profit hedge inclus
            if "hedge_realized" in positions[0] and tp_hit:
                exit_price = tp_price
                buy_pnl = sum(
                    pnl_for_position(symbol, p["direction"], p["open_price"], exit_price, p["lot"], contract)
                    for p in positions
                )
                final_pnl = buy_pnl + positions[0]["hedge_realized"]
                cycle_outcome = {
                    "start": cycle_start, "end": df.index[j],
                    "outcome": "TP_AFTER_HEDGE", "pnl": final_pnl,
                    "levels_opened": len(positions), "had_hedge": True,
                    "max_floating_loss": max_floating_loss
                }
                i = j + 1
                break

        # Daca s-a iesit din loop fara exit, ciclu deschis la final
        if cycle_outcome is None:
            # Inchide la ultimul pret (mark-to-market)
            last = df.iloc[-1]["close"]
            buy_pnl = sum(
                pnl_for_position(symbol, p["direction"], p["open_price"], last, p["lot"], contract)
                for p in positions
            )
            hedge_realized = positions[0].get("hedge_realized", 0)
            if hedge_open:
                hedge_realized += pnl_for_position(symbol, hedge_open["direction"], hedge_open["open_price"], last, hedge_open["lot"], contract)
            final_pnl = buy_pnl + hedge_realized
            cycle_outcome = {
                "start": cycle_start, "end": df.index[-1],
                "outcome": "OPEN_END", "pnl": final_pnl,
                "levels_opened": len(positions), "had_hedge": hedge_open is not None or "hedge_realized" in positions[0],
                "max_floating_loss": max_floating_loss
            }
            i = len(df)  # exit outer

        cycles.append(cycle_outcome)

    return cycles


def run_symbol(symbol, df):
    print(f"\n{'='*70}")
    print(f"  DCA BACKTEST: {symbol} | {len(df)} bare H1")
    print(f"{'='*70}")

    for direction in ["BUY", "SELL"]:
        print(f"\n  Direction: {direction} (grid pentru directia asta)")
        cycles = simulate_dca_cycle(symbol, df, direction=direction)

        total_pnl = sum(c["pnl"] for c in cycles)
        n_cycles = len(cycles)
        wins = sum(1 for c in cycles if c["pnl"] > 0)
        max_floating = min((c["max_floating_loss"] for c in cycles), default=0)
        outcomes = {}
        for c in cycles:
            outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1

        print(f"    Cicluri: {n_cycles}")
        print(f"    Win/Loss: {wins}/{n_cycles - wins}")
        print(f"    NET P&L: ${total_pnl:+.2f}")
        print(f"    Max floating loss vazut: ${max_floating:.2f}")
        print(f"    Outcomes: {outcomes}")

        # Sample primele 3 si ultimele 2 cicluri
        for c in cycles[:3]:
            print(f"      {c['start']} → {c['end']}: {c['outcome']} | levels={c['levels_opened']} | hedge={c['had_hedge']} | P&L ${c['pnl']:+.2f}")
        if len(cycles) > 5:
            print(f"      ... ({len(cycles) - 5} cicluri intermediare) ...")
        for c in cycles[-2:]:
            print(f"      {c['start']} → {c['end']}: {c['outcome']} | levels={c['levels_opened']} | hedge={c['had_hedge']} | P&L ${c['pnl']:+.2f}")

        return_per_symbol_dir = total_pnl


def main():
    init_mt5()
    print(f"\n{'='*70}")
    print(f"  DCA + HEDGE BACKTEST | {LOOKBACK_DAYS} zile | Balance ${INITIAL_BALANCE}")
    print(f"  Trepte: {LOT_PROGRESSION} | Distanta: {LEVEL_DISTANCE_PCT}% | Hedge: {HEDGE_LOT}lot la treapta {HEDGE_TRIGGER_LEVEL}")
    print(f"  TP: avg + {TP_MARGIN_PCT}% | Hard SL: ${HARD_STOP_LOSS}")
    print(f"{'='*70}")

    summary = []
    for sym in SYMBOLS:
        df = download_h1(sym, LOOKBACK_DAYS)
        if len(df) == 0:
            print(f"  {sym}: SKIP (no data)")
            continue

        for direction in ["BUY", "SELL"]:
            cycles = simulate_dca_cycle(sym, df, direction=direction)
            total = sum(c["pnl"] for c in cycles)
            wins = sum(1 for c in cycles if c["pnl"] > 0)
            n = len(cycles)
            max_dd = min((c["max_floating_loss"] for c in cycles), default=0)
            outcomes = {}
            for c in cycles:
                outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
            summary.append({
                "symbol": sym, "direction": direction, "cycles": n,
                "wins": wins, "wr": wins/n*100 if n > 0 else 0,
                "net": total, "max_dd": max_dd, "outcomes": outcomes
            })

    # Tabel sumar
    print(f"\n\n{'='*100}")
    print(f"  TABEL FINAL DCA + HEDGE")
    print(f"{'='*100}")
    print(f"{'Symbol':<12} {'Dir':<5} {'Cycles':>7} {'WR%':>6} {'NET P&L':>11} {'MaxFloat':>11} {'Outcomes':<30}")
    print(f"{'-'*100}")
    for s in summary:
        print(f"{s['symbol']:<12} {s['direction']:<5} {s['cycles']:>7} {s['wr']:>5.1f}% ${s['net']:>+9.2f} ${s['max_dd']:>+9.2f} {str(s['outcomes']):<30}")

    # Total per simbol (BUY + SELL)
    print(f"\n  TOTAL per simbol (BUY+SELL grids combined):")
    for sym in SYMBOLS:
        sym_results = [s for s in summary if s["symbol"] == sym]
        if not sym_results:
            continue
        total = sum(s["net"] for s in sym_results)
        cycles = sum(s["cycles"] for s in sym_results)
        print(f"    {sym}: {cycles} cicluri, NET ${total:+.2f}")

    grand_total = sum(s["net"] for s in summary)
    print(f"\n  GRAND TOTAL (toate simbolurile, ambele directii): ${grand_total:+.2f}")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
