"""
DCA + HEDGE backtest REALIST:
  - Max 1 ciclu activ per simbol (nu suprapuneri)
  - Comisioane $0.30 per trade
  - Swap incluse (per noapte * nights tinut)
  - TP/SL exact ca in DCA naiv
  - Cooldown 24h dupa close ciclu (evita re-entry imediat)
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
LEVEL_DISTANCE_PCT = 2.0
LOT_PROGRESSION = [0.01, 0.02, 0.03, 0.04]
HEDGE_LOT = 0.10
HEDGE_TRIGGER_LEVEL = 4
TP_MARGIN_PCT = 0.3
HEDGE_PROFIT_TARGET = 50.0
HARD_STOP_LOSS = -300.0
COMMISSION_PER_TRADE = 0.30
COOLDOWN_HOURS = 24

CONTRACT_SIZE = {
    "XAUUSD.cfd": 100, "XAGUSD.cfd": 5000,
    "USDCHF.cfd": 100000, "USDCAD.cfd": 100000,
}
# Swap per night (conservative estimate USD per 1 lot)
SWAP_PER_NIGHT = {
    "XAUUSD.cfd": {"BUY": -61.0, "SELL": 0.0},
    "XAGUSD.cfd": {"BUY": -5.0, "SELL": 0.5},
    "USDCHF.cfd": {"BUY": 1.0, "SELL": -3.0},
    "USDCAD.cfd": {"BUY": -2.0, "SELL": 0.5},
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
    if direction == "BUY":
        diff = current_price - open_price
    else:
        diff = open_price - current_price
    pnl_quote = diff * lot * contract
    if symbol in ("USDCHF.cfd", "USDCAD.cfd"):
        return pnl_quote / current_price
    return pnl_quote


def calc_swap(symbol, direction, lot, nights):
    swap = SWAP_PER_NIGHT[symbol][direction] * lot * nights
    return swap  # negative = cost


def simulate_dca_realistic(symbol, df, direction="BUY"):
    contract = CONTRACT_SIZE[symbol]
    opposite = "SELL" if direction == "BUY" else "BUY"
    cycles = []

    i = 0
    last_exit_time = None
    while i < len(df):
        # Cooldown check
        current_time = df.index[i]
        if last_exit_time and (current_time - last_exit_time).total_seconds() < COOLDOWN_HOURS * 3600:
            i += 1
            continue

        entry_price = df.iloc[i]["close"]
        entry_time = current_time
        positions = [{
            "level": 1, "direction": direction,
            "open_price": entry_price, "lot": LOT_PROGRESSION[0],
            "open_time": entry_time
        }]
        max_floating_loss = 0
        hedge_open = None
        hedge_realized_pnl = 0
        hedge_realized_swap = 0
        hedge_open_time = None
        cycle_outcome = None

        for j in range(i + 1, len(df)):
            current = df.iloc[j]["close"]
            high = df.iloc[j]["high"]
            low = df.iloc[j]["low"]
            t_now = df.index[j]

            # Open new level if triggered
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
                        "open_price": trigger_price, "lot": LOT_PROGRESSION[next_level - 1],
                        "open_time": t_now
                    })
                    if next_level == HEDGE_TRIGGER_LEVEL:
                        hedge_open = {
                            "direction": opposite, "open_price": trigger_price, "lot": HEDGE_LOT,
                            "open_time": t_now
                        }
                        hedge_open_time = t_now

            # Floating P&L
            buy_pnl = sum(
                pnl_for_position(symbol, p["direction"], p["open_price"], current, p["lot"], contract)
                for p in positions
            )
            hedge_pnl_now = 0
            if hedge_open:
                hedge_pnl_now = pnl_for_position(symbol, hedge_open["direction"], hedge_open["open_price"], current, hedge_open["lot"], contract)
            floating = buy_pnl + hedge_pnl_now + hedge_realized_pnl

            if floating < max_floating_loss:
                max_floating_loss = floating

            avg_entry = sum(p["open_price"] * p["lot"] for p in positions) / sum(p["lot"] for p in positions)
            tp_price = avg_entry * (1 + TP_MARGIN_PCT/100) if direction == "BUY" else avg_entry * (1 - TP_MARGIN_PCT/100)
            tp_hit = (direction == "BUY" and high >= tp_price) or (direction == "SELL" and low <= tp_price)

            # Hedge profit close
            if hedge_open and hedge_pnl_now >= HEDGE_PROFIT_TARGET:
                # Calc swap for hedge
                hedge_nights = max(0, (t_now - hedge_open_time).total_seconds() // 86400)
                hedge_swap = calc_swap(symbol, hedge_open["direction"], hedge_open["lot"], hedge_nights)
                hedge_realized_pnl += hedge_pnl_now
                hedge_realized_swap += hedge_swap
                hedge_open = None

            # Exit conditions
            should_exit = False
            outcome = None

            if tp_hit and not hedge_open and (hedge_realized_pnl == 0 or buy_pnl + hedge_realized_pnl > 0):
                # TP normal sau TP after hedge realized
                exit_price = tp_price
                should_exit = True
                outcome = "TP_AFTER_HEDGE" if hedge_realized_pnl > 0 else "TP"

            if floating <= HARD_STOP_LOSS:
                exit_price = current
                should_exit = True
                outcome = "HARD_SL"

            if should_exit:
                # Calculate everything
                final_buy_pnl = sum(
                    pnl_for_position(symbol, p["direction"], p["open_price"], exit_price, p["lot"], contract)
                    for p in positions
                )
                # Swap pe BUY-uri
                buy_swap = 0
                for p in positions:
                    nights = max(0, (t_now - p["open_time"]).total_seconds() // 86400)
                    buy_swap += calc_swap(symbol, p["direction"], p["lot"], nights)

                # Hedge daca inca deschis
                final_hedge_pnl = hedge_realized_pnl
                final_hedge_swap = hedge_realized_swap
                if hedge_open:
                    final_hedge_pnl += pnl_for_position(symbol, hedge_open["direction"], hedge_open["open_price"], exit_price, hedge_open["lot"], contract)
                    nights = max(0, (t_now - hedge_open_time).total_seconds() // 86400)
                    final_hedge_swap += calc_swap(symbol, hedge_open["direction"], hedge_open["lot"], nights)

                # Comisioane: nr trade-uri (open + close fiecare)
                n_trades = (len(positions) + (1 if (hedge_open or hedge_realized_pnl > 0) else 0)) * 2
                commission = n_trades * COMMISSION_PER_TRADE

                final_pnl = final_buy_pnl + buy_swap + final_hedge_pnl + final_hedge_swap - commission

                cycle_outcome = {
                    "start": entry_time, "end": t_now,
                    "outcome": outcome, "pnl": final_pnl,
                    "gross": final_buy_pnl + final_hedge_pnl,
                    "swap": buy_swap + final_hedge_swap,
                    "commission": commission,
                    "levels": len(positions),
                    "had_hedge": hedge_open is not None or hedge_realized_pnl != 0,
                    "max_floating": max_floating_loss,
                    "duration_hours": (t_now - entry_time).total_seconds() / 3600
                }
                last_exit_time = t_now
                i = j + 1
                break

        if cycle_outcome is None:
            # Mark to market la sfarsit
            last = df.iloc[-1]["close"]
            t_now = df.index[-1]
            final_buy_pnl = sum(
                pnl_for_position(symbol, p["direction"], p["open_price"], last, p["lot"], contract)
                for p in positions
            )
            buy_swap = 0
            for p in positions:
                nights = max(0, (t_now - p["open_time"]).total_seconds() // 86400)
                buy_swap += calc_swap(symbol, p["direction"], p["lot"], nights)
            final_hedge_pnl = hedge_realized_pnl
            final_hedge_swap = hedge_realized_swap
            if hedge_open:
                final_hedge_pnl += pnl_for_position(symbol, hedge_open["direction"], hedge_open["open_price"], last, hedge_open["lot"], contract)
                nights = max(0, (t_now - hedge_open_time).total_seconds() // 86400)
                final_hedge_swap += calc_swap(symbol, hedge_open["direction"], hedge_open["lot"], nights)
            n_trades = (len(positions) + (1 if (hedge_open or hedge_realized_pnl > 0) else 0)) * 2
            commission = n_trades * COMMISSION_PER_TRADE
            final_pnl = final_buy_pnl + buy_swap + final_hedge_pnl + final_hedge_swap - commission

            cycle_outcome = {
                "start": entry_time, "end": t_now,
                "outcome": "OPEN_END", "pnl": final_pnl,
                "gross": final_buy_pnl + final_hedge_pnl,
                "swap": buy_swap + final_hedge_swap,
                "commission": commission,
                "levels": len(positions),
                "had_hedge": hedge_open is not None or hedge_realized_pnl != 0,
                "max_floating": max_floating_loss,
                "duration_hours": (t_now - entry_time).total_seconds() / 3600
            }
            i = len(df)

        cycles.append(cycle_outcome)

    return cycles


def main():
    init_mt5()
    print(f"\n{'='*100}")
    print(f"  DCA + HEDGE REALISTIC | {LOOKBACK_DAYS} zile | Balance ${INITIAL_BALANCE}")
    print(f"  Comisioane: ${COMMISSION_PER_TRADE}/trade | Swap inclus | Cooldown {COOLDOWN_HOURS}h | Max 1 ciclu/simbol")
    print(f"{'='*100}\n")

    summary = []
    for sym in SYMBOLS:
        df = download_h1(sym, LOOKBACK_DAYS)
        if len(df) == 0:
            continue
        for direction in ["BUY", "SELL"]:
            cycles = simulate_dca_realistic(sym, df, direction=direction)
            total = sum(c["pnl"] for c in cycles)
            wins = sum(1 for c in cycles if c["pnl"] > 0)
            n = len(cycles)
            avg_dur = sum(c["duration_hours"] for c in cycles) / n if n > 0 else 0
            max_dd = min((c["max_floating"] for c in cycles), default=0)
            outcomes = {}
            for c in cycles:
                outcomes[c["outcome"]] = outcomes.get(c["outcome"], 0) + 1
            total_swap = sum(c["swap"] for c in cycles)
            total_comm = sum(c["commission"] for c in cycles)
            summary.append({
                "symbol": sym, "direction": direction, "cycles": n,
                "wins": wins, "wr": wins/n*100 if n > 0 else 0,
                "net": total, "swap": total_swap, "comm": total_comm,
                "max_dd": max_dd, "avg_dur_h": avg_dur, "outcomes": outcomes
            })

    print(f"{'Symbol':<12} {'Dir':<5} {'Cycles':>6} {'WR%':>5} {'AvgDur':>7} {'Swap':>8} {'Comm':>7} {'NET':>10} {'MaxDD':>9} {'Outcomes'}")
    print(f"{'-'*120}")
    for s in summary:
        print(f"{s['symbol']:<12} {s['direction']:<5} {s['cycles']:>6} {s['wr']:>4.1f}% {s['avg_dur_h']:>6.1f}h ${s['swap']:>+6.2f} ${s['comm']:>5.2f} ${s['net']:>+8.2f} ${s['max_dd']:>+7.2f} {s['outcomes']}")

    print(f"\n  TOTAL per simbol (BUY+SELL):")
    grand_total = 0
    for sym in SYMBOLS:
        sym_results = [s for s in summary if s["symbol"] == sym]
        total = sum(s["net"] for s in sym_results)
        grand_total += total
        cycles = sum(s["cycles"] for s in sym_results)
        print(f"    {sym}: {cycles} cicluri, NET ${total:+.2f}")

    print(f"\n  GRAND TOTAL: ${grand_total:+.2f}")
    print(f"  AVG/zi: ${grand_total/LOOKBACK_DAYS:+.2f}")
    print(f"  ROI 90d: {grand_total/INITIAL_BALANCE*100:+.2f}%")

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
