"""
Backtest cele 4 modificari propuse pentru fix asimetrie castig/pierdere:
1. Cap lot maxim 0.05 (in loc de 0.1)
2. Trailing activation +$88 (era $44) — lasa winners sa creasca
3. Breakeven stop la +$44 — daca scade sub, close la 0
4. atr_multiplier 1.5 -> 1.0 (SL mai stransa)

Compar 4 configuratii:
- CURRENT (actual live) — atr 1.5, trailing 44, no BE, lot cap 0.10
- CAP_LOT — atr 1.5, trailing 44, no BE, lot cap 0.05
- LET_RUN — atr 1.5, trailing 88, BE 44, lot cap 0.05
- TIGHT_SL — atr 1.0, trailing 88, BE 44, lot cap 0.05 (FULL FIX)

BRENT (Breakout M30 + MeanRev H1) + WTI (Breakout M30 + MeanRev H1)
Perioada: 90 zile
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
from strategies.breakout import Breakout
from strategies.mean_reversion import MeanReversion
from strategies.base_strategy import Signal

LOOKBACK = 100
SYMBOLS = ["BRENT.cfd", "WTI.cfd"]
LOTS_BASE = {"BRENT.cfd": 0.08, "WTI.cfd": 0.07}  # baza (poate fi cap-uita)

TF_MAP = {"M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1}

BR_PARAMS = {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 1.5}
MR_PARAMS = {"rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, "bb_period": 20, "bb_std_dev": 2.0}

# Configuratii testate
CONFIGS = {
    "CURRENT": {
        "atr_mult": 1.5, "rr": 3.0,
        "lot_cap": 0.10,             # Max lot permis
        "trailing_active_usd": 44,   # Peste care trailing activeaza
        "trailing_pct": 0.50,        # Peak * (1 - 0.5) = close
        "breakeven_usd": None,        # Fara BE stop
    },
    "CAP_LOT": {
        "atr_mult": 1.5, "rr": 3.0,
        "lot_cap": 0.05,
        "trailing_active_usd": 44,
        "trailing_pct": 0.50,
        "breakeven_usd": None,
    },
    "LET_RUN": {
        "atr_mult": 1.5, "rr": 3.0,
        "lot_cap": 0.05,
        "trailing_active_usd": 88,   # RAISED — winners cresc pana la $88 fara trail
        "trailing_pct": 0.30,        # Trail mai putin agresiv
        "breakeven_usd": 44,          # BE stop la +$44
    },
    "TIGHT_SL": {
        "atr_mult": 1.0,             # SL 1x ATR (era 1.5x)
        "rr": 3.0,
        "lot_cap": 0.05,
        "trailing_active_usd": 88,
        "trailing_pct": 0.30,
        "breakeven_usd": 44,
    },
}

# Simulare regime pentru lot multiplier (aproximativ)
def regime_multiplier(atr, price):
    """Aproximeaza multiplier de regime din volatilitate."""
    atr_pct = atr / price * 100
    if atr_pct > 1.5: return 1.3   # TRENDING/VOLATILE
    if atr_pct > 0.8: return 1.0   # RANGING/normal
    if atr_pct > 0.3: return 0.5   # QUIET
    return 0.0

def init_mt5():
    if not mt5.initialize(): sys.exit("MT5 nu se conecteaza")
    print(f"MT5: {mt5.account_info().login}")

def dl(sym, tf):
    r = mt5.copy_rates_range(sym, TF_MAP[tf], datetime.now()-timedelta(days=LOOKBACK), datetime.now())
    if r is None or len(r)==0: return None
    df = pd.DataFrame(r); df["time"]=pd.to_datetime(df["time"],unit="s"); df.set_index("time",inplace=True)
    return df

def pnl_calc(si, d, op, cp, lot):
    if si.trade_tick_size==0: return 0
    s = 1 if d=="BUY" else -1
    return (s*(cp-op)/si.trade_tick_size)*si.trade_tick_value*lot

def atr_val(data, p=14):
    if len(data)<p+1: return None
    a = ta.atr(data["high"],data["low"],data["close"],length=p).iloc[-1]
    return float(a) if pd.notna(a) and a>0 else None

def bt_with_config(symbol, strategy, df, config, symbol_info):
    """Backtest cu simulare trailing + BE + lot cap."""
    trades = []
    pos = None
    lot_base = LOTS_BASE[symbol]

    for i in range(60, len(df)):
        bar = df.iloc[i]
        w = df.iloc[:i+1].copy()

        # Gestioneaza pozitia deschisa
        if pos:
            # Calculeaza profit curent (pentru trailing / BE simulation)
            # Foloseste bar["high"] si bar["low"] pt a determina daca SL/TP/trailing hit
            entry = pos["entry"]
            direction = pos["dir"]
            lot = pos["lot"]

            # Max peak profit posibil in aceasta bara
            if direction == "BUY":
                max_price = bar["high"]
                min_price = bar["low"]
            else:
                max_price = bar["low"]  # inversat: SELL profitabil daca pret scade
                min_price = bar["high"]

            # Profit la max (favorable) si min (adverse)
            profit_max = pnl_calc(symbol_info, direction, entry, max_price, lot)
            profit_min = pnl_calc(symbol_info, direction, entry, min_price, lot)

            # Actualizeaza peak profit
            if profit_max > pos["peak_profit"]:
                pos["peak_profit"] = profit_max

            # Verifica: trailing activated?
            trailing_close_price = None
            if pos["peak_profit"] >= config["trailing_active_usd"]:
                # Trailing activ: close daca profit scade la peak * (1 - trailing_pct)
                target_profit = pos["peak_profit"] * (1 - config["trailing_pct"])
                # Convert profit target back to price
                # PnL = (price_diff * lot * contract) / tick or similar
                # target_price such that pnl_calc = target_profit
                si = symbol_info
                if si.trade_tick_value > 0 and si.trade_tick_size > 0:
                    price_move = target_profit / (lot * si.trade_tick_value / si.trade_tick_size)
                    if direction == "BUY":
                        trailing_close_price = entry + price_move
                    else:
                        trailing_close_price = entry - price_move

            # Verifica: BE stop activated?
            be_close_price = None
            if config["breakeven_usd"] and pos["peak_profit"] >= config["breakeven_usd"]:
                # BE stop la entry
                be_close_price = entry

            # Determine effective SL (broker SL, or BE, or trailing, whichever hit first)
            effective_sl = pos["sl"]
            reason = "SL"
            if be_close_price is not None:
                if direction == "BUY" and be_close_price > effective_sl:
                    effective_sl = be_close_price; reason = "BE"
                elif direction == "SELL" and be_close_price < effective_sl:
                    effective_sl = be_close_price; reason = "BE"

            # Check TP hit first (highest priority for profit)
            closed = False
            exit_price = None
            close_reason = None

            if direction == "BUY":
                if bar["high"] >= pos["tp"]:
                    exit_price = pos["tp"]; close_reason = "TP"; closed = True
                elif trailing_close_price and bar["low"] <= trailing_close_price and pos["peak_profit"] >= config["trailing_active_usd"]:
                    exit_price = trailing_close_price; close_reason = "TRAIL"; closed = True
                elif bar["low"] <= effective_sl:
                    exit_price = effective_sl; close_reason = reason; closed = True
            else:  # SELL
                if bar["low"] <= pos["tp"]:
                    exit_price = pos["tp"]; close_reason = "TP"; closed = True
                elif trailing_close_price and bar["high"] >= trailing_close_price and pos["peak_profit"] >= config["trailing_active_usd"]:
                    exit_price = trailing_close_price; close_reason = "TRAIL"; closed = True
                elif bar["high"] >= effective_sl:
                    exit_price = effective_sl; close_reason = reason; closed = True

            if closed:
                pnl_final = pnl_calc(symbol_info, direction, entry, exit_price, lot)
                trades.append({
                    "entry_time": pos["t"],
                    "pnl": pnl_final,
                    "reason": close_reason,
                    "lot": lot,
                    "peak_profit": pos["peak_profit"]
                })
                pos = None

        # Open new position
        if pos is None:
            try: sig = strategy.analyze(w)
            except: continue
            if sig == Signal.HOLD: continue
            direction = sig.value
            entry = float(bar["close"])
            atr = atr_val(w)
            if atr is None: continue

            # Regime multiplier (aprox)
            reg_mult = regime_multiplier(atr, entry)
            if reg_mult <= 0: continue  # skip QUIET/UNKNOWN

            # Calc lot with cap
            lot = min(lot_base * reg_mult, config["lot_cap"])
            lot = round(lot / 0.01) * 0.01  # round to volume_step
            if lot < 0.01: continue

            sl_d = atr * config["atr_mult"]
            tp_d = sl_d * config["rr"]
            sl = entry - sl_d if direction == "BUY" else entry + sl_d
            tp = entry + tp_d if direction == "BUY" else entry - tp_d

            pos = {
                "t": df.index[i], "dir": direction, "entry": entry,
                "sl": sl, "tp": tp, "lot": lot, "peak_profit": 0
            }

    return trades


def main():
    init_mt5()

    # Pre-download
    data = {}
    infos = {}
    for sym in SYMBOLS:
        infos[sym] = mt5.symbol_info(sym)
        for tf in TF_MAP:
            d = dl(sym, tf)
            if d is not None: data[(sym, tf)] = d
    print(f"Date: {len(data)} sym×TF combos\n")

    # Backtest fiecare configuratie
    all_results = []
    for cfg_name, cfg in CONFIGS.items():
        for sym in SYMBOLS:
            si = infos[sym]
            # Breakout M30
            br = Breakout(params=BR_PARAMS)
            trades_br = bt_with_config(sym, br, data[(sym, "M30")], cfg, si)
            # MeanReversion H1
            mr = MeanReversion(params=MR_PARAMS)
            trades_mr = bt_with_config(sym, mr, data[(sym, "H1")], cfg, si)

            for strat_name, trades in [("Breakout-M30", trades_br), ("MeanRev-H1", trades_mr)]:
                if not trades:
                    all_results.append({"config": cfg_name, "sym": sym, "strat": strat_name,
                                        "trades": 0, "wr": 0, "net": 0, "avg_win": 0, "avg_loss": 0, "max_loss": 0})
                    continue
                # last 90 days
                cutoff = data[(sym, "M30")].index[-1] - pd.Timedelta(days=90)
                pt = [t for t in trades if t["entry_time"] >= cutoff]
                if not pt: pt = trades

                wins = [t for t in pt if t["pnl"] > 0]
                losses = [t for t in pt if t["pnl"] <= 0]
                avg_win = sum(t["pnl"] for t in wins)/len(wins) if wins else 0
                avg_loss = sum(t["pnl"] for t in losses)/len(losses) if losses else 0
                max_loss = min([t["pnl"] for t in losses]) if losses else 0
                net = sum(t["pnl"] for t in pt)

                all_results.append({
                    "config": cfg_name, "sym": sym, "strat": strat_name,
                    "trades": len(pt), "wr": round(len(wins)/len(pt)*100, 1),
                    "net": round(net, 2), "avg_win": round(avg_win, 2),
                    "avg_loss": round(avg_loss, 2), "max_loss": round(max_loss, 2)
                })

    df_r = pd.DataFrame(all_results)

    # Print detaliat per config
    print("="*130)
    print("  REZULTATE PER CONFIGURATIE (90 zile, BRENT + WTI, Breakout M30 + MeanRev H1)")
    print("="*130)
    for cfg_name in CONFIGS:
        sub = df_r[df_r["config"] == cfg_name]
        total_trades = sub["trades"].sum()
        total_net = sub["net"].sum()
        all_wins_sum = (sub["avg_win"] * sub["trades"] * sub["wr"] / 100).sum()
        all_wins_count = (sub["trades"] * sub["wr"] / 100).sum()
        all_losses_count = (sub["trades"] * (100 - sub["wr"]) / 100).sum()
        avg_win_all = all_wins_sum / all_wins_count if all_wins_count > 0 else 0
        avg_loss_all = sub["avg_loss"].mean() if len(sub) > 0 else 0
        max_loss = sub["max_loss"].min() if len(sub) > 0 else 0

        print(f"\n  {cfg_name}: {total_trades} trades, NET ${total_net:+.2f}, "
              f"AvgWin ${avg_win_all:+.2f}, AvgLoss ${avg_loss_all:+.2f}, MaxLoss ${max_loss:+.2f}")
        for _, r in sub.iterrows():
            print(f"    {r['sym']:<12} {r['strat']:<14}: {r['trades']:>3}tr WR {r['wr']:>5.1f}% NET ${r['net']:>+7.2f} "
                  f"AvgW ${r['avg_win']:>+5.2f} AvgL ${r['avg_loss']:>+5.2f}")

    # Sumar
    print("\n" + "="*80)
    print("  SUMAR - NET total pe configuratie (90 zile)")
    print("="*80)
    for cfg_name in CONFIGS:
        sub = df_r[df_r["config"] == cfg_name]
        total = sub["net"].sum()
        trades = sub["trades"].sum()
        per_day = total / 90
        per_month = per_day * 22
        print(f"  {cfg_name:<10}: NET ${total:>+8.2f} | {trades} trades | ${per_day:+.2f}/zi | ${per_month:+.2f}/luna")

    df_r.to_csv("results/asymmetry_fix.csv", index=False)
    mt5.shutdown()
    print("\nDone. CSV: results/asymmetry_fix.csv")


if __name__ == "__main__":
    main()
