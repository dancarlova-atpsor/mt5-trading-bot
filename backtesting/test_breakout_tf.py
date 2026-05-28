"""Test Breakout pe BRENT + WTI pe toate TF-urile (M15, M30, H1, H4, D1)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timedelta
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
from strategies.breakout import Breakout
from strategies.base_strategy import Signal

LOOKBACK = 100
PERIODS = [30, 60, 90]
LOTS = {"BRENT.cfd": 0.08, "WTI.cfd": 0.07}
TF_MAP = {"M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}
BR_PARAMS = {"lookback_period": 50, "atr_period": 14, "min_atr_ratio": 0.8, "min_vol_ratio": 1.5}

mt5.initialize()
print(f"MT5: {mt5.account_info().login}\n")

def dl(sym, tf):
    r = mt5.copy_rates_range(sym, TF_MAP[tf], datetime.now()-timedelta(days=LOOKBACK), datetime.now())
    if r is None or len(r)==0: return None
    df = pd.DataFrame(r); df["time"]=pd.to_datetime(df["time"],unit="s"); df.set_index("time",inplace=True)
    return df

def pnl(si,d,op,cp,lot):
    if si.trade_tick_size==0: return 0
    s=1 if d=="BUY" else -1
    return (s*(cp-op)/si.trade_tick_size)*si.trade_tick_value*lot

def atr(data,p=14):
    if len(data)<p+1: return None
    a=ta.atr(data["high"],data["low"],data["close"],length=p).iloc[-1]
    return float(a) if pd.notna(a) and a>0 else None

def sltp(d,e,data,rr=2.0,m=1.5):
    a=atr(data)
    if a is None: return None,None
    sd=a*m; td=sd*rr
    return (e-sd,e+td) if d=="BUY" else (e+sd,e-td)

def bt(sym,df,lot):
    si=mt5.symbol_info(sym); trades=[]; pos=None
    for i in range(60,len(df)):
        bar=df.iloc[i]
        if pos:
            c=False;ep=None
            if pos["d"]=="BUY":
                if bar["low"]<=pos["sl"]:ep=pos["sl"];c=True
                elif bar["high"]>=pos["tp"]:ep=pos["tp"];c=True
            else:
                if bar["high"]>=pos["sl"]:ep=pos["sl"];c=True
                elif bar["low"]<=pos["tp"]:ep=pos["tp"];c=True
            if c: trades.append({"t":pos["t"],"pnl":pnl(si,pos["d"],pos["e"],ep,lot)});pos=None
        if pos is None:
            w=df.iloc[:i+1].copy()
            try: sig=Breakout(params=BR_PARAMS).analyze(w)
            except: continue
            if sig==Signal.HOLD: continue
            d=sig.value;e=float(bar["close"]);sl,tp=sltp(d,e,w)
            if sl is None: continue
            pos={"t":df.index[i],"d":d,"e":e,"sl":sl,"tp":tp}
    return trades

results=[]
for sym in LOTS:
    for tf in TF_MAP:
        df=dl(sym,tf)
        if df is None or len(df)<100: continue
        trades=bt(sym,df,LOTS[sym])
        now=df.index[-1]
        for pdays in PERIODS:
            pt=[t for t in trades if t["t"]>=now-pd.Timedelta(days=pdays)]
            if not pt: results.append({"sym":sym,"tf":tf,"period":pdays,"trades":0,"wr":0,"net":0});continue
            w=sum(1 for t in pt if t["pnl"]>0)
            results.append({"sym":sym,"tf":tf,"period":pdays,"trades":len(pt),"wr":round(w/len(pt)*100,1),"net":round(sum(t["pnl"] for t in pt),2)})

df_r=pd.DataFrame(results)
print("="*90)
print("  BREAKOUT pe TOATE TF-urile — 90 ZILE")
print("="*90)
print(f"{'Symbol':<12} {'TF':<5} {'Trades':>7} {'WR%':>6} {'NET':>10} {'Tr/zi':>7}")
print("-"*90)
d90=df_r[df_r["period"]==90].sort_values(["sym","net"],ascending=[True,False])
for _,r in d90.iterrows():
    print(f"{r['sym']:<12} {r['tf']:<5} {r['trades']:>7} {r['wr']:>5.1f}% ${r['net']:>+8.2f} {r['trades']/90:>6.2f}")

print("\n  Best TF per simbol (90 zile):")
for sym in LOTS:
    sub=d90[d90["sym"]==sym]
    if len(sub)==0: continue
    best=sub.iloc[0]
    print(f"    {sym}: {best['tf']} = ${best['net']:+.2f} ({best['trades']} trades, WR {best['wr']:.1f}%)")
mt5.shutdown()
print("\nDone.")
