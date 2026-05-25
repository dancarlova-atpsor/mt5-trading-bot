# CLAUDE.md — Context complet pentru sesiuni Claude Code

> **Fisier critic.** Citeste-l in INTREGIME inainte sa propui modificari.
> Userul (Dan, SC Luxuria Trans & Travel SRL) opereaza un bot de trading MT5 pe cont demo EarnBroker. Foloseste Romanian / Romglish in conversatii.

Ultima actualizare: **2026-05-25 ora 19:50** (STRESS TEST complet — decizie OIL strategy pending).

---

## 1. Statusul curent al proiectului

**Bot ruleaza pe VPS** (MyForexVPS Silver Windows 2019, $14.99/luna), conectat la **MetaTrader 5 EarnBroker custom build 5833**.

**Strategie activa:** **METAL-ONLY** (XAU + XAG) — simplificare 2026-05-18 dupa review weekly real.

**Cont MT5 demo:** `9047479` @ EarnBroker-Sharp ECN, balance ~$98,092 USD (cont mare demo), magic number `123456`, leverage 1:100, simulated_balance pentru risc = `5700 USD` (echivalent 5300 EUR target real).

**Target zilnic estimat:** $50-80/zi pe metale (pe baza performanta reala 14-15 mai: +$80 in 24h).

**ISTORIC strategii:**
- 2026-04-14 → 2026-05-07: 4 forex/metals (XAU, USDCHF, USDCAD, GBPUSD) — break-even
- 2026-05-07 → 2026-05-18: HIBRID + 7 STOCKS — backtest a fost optimist; real -$58 (week 1) + -$10 (week 2)
- 2026-05-18 → prezent: **METAL-ONLY** (XAU + XAG) — singurele care performeaza pe demo real

---

## 2. Arhitectura proiect

```
mt5-trading-bot/
├── main.py                    # Entry point, loop principal
├── config/
│   ├── settings.yaml          # Cont, simboluri, sesiuni, telegram, profit_manager
│   ├── strategies.yaml        # Per-symbol params trend_following + breakout
│   ├── risk_management.yaml   # Position sizing, SL/TP, R:R, limits
│   └── earnings_calendar.yaml # NEW (2026-05-07): blackout ±2 zile pe stocks
├── core/                       # MT5 connector, order manager
├── strategies/
│   ├── trend_following.py     # EMA + MACD + ADX, per-symbol overrides
│   ├── breakout.py            # Donchian + ATR + volume
│   ├── macd_divergence.py     # DEZACTIVAT
│   ├── mean_reversion.py      # DEZACTIVAT
│   ├── scalping.py            # DEZACTIVAT
│   └── news_strategy.py       # DEZACTIVAT
├── superbot/
│   ├── superbot.py            # gate_trade: filtru entry, swap, D1 (selectiv)
│   ├── entry_filter.py        # MTF (D1+H4) + S/R + swap pentru breakout
│   ├── correlation_filter.py
│   ├── deep_analyzer.py       # regime + calibrator periodic
│   ├── parameter_calibrator.py
│   ├── performance_tracker.py # auto-disable simboluri pierzatoare
│   └── regime_detector.py     # TRENDING/RANGING/VOLATILE/QUIET
├── risk/
│   ├── risk_manager.py        # Lot calc, SL/TP cu R:R per-symbol
│   └── profit_manager.py      # Trailing tier-based pe profit cumulativ
├── utils/
│   ├── helpers.py             # pips_to_price (stocks pip=0.01)
│   ├── logger.py              # Rotating file
│   └── notifier.py            # Telegram via requests (sync)
├── backtesting/
│   ├── backtester.py          # Bar-by-bar simulator
│   ├── compare_options.py     # A-E backtest comparison
│   ├── dca_hedge_backtest.py  # DCA naiv (suprapuneri)
│   ├── dca_realistic.py       # DCA cu comm + swap + cooldown
│   ├── hybrid_backtest.py     # Per-symbol R:R + D1 selectiv
│   ├── stocks_backtest.py     # 15 stocks US pe sesiune NASDAQ
│   ├── tf_grid_search.py
│   └── stress_test.py
├── close_swap_protection.py   # Manual: closeprofitable Wed/Fri 22:55 RO
└── logs/trading_bot.log       # Rotating, 10MB × 5
```

---

## 3. Strategia curenta — HIBRID + STOCKS (deployed 2026-05-07)

### 3.1 Simboluri active (12 total)

**Forex/Metals (4)** — tranzactionate 24/5:
- `XAUUSD.cfd` (Aur) — TF, **doar SELL** (BUY blocat in superbot.py: swap -$61/noapte)
- `XAGUSD.cfd` (Argint) — TF, lot fix 0.01 (volume_override)
- `USDCHF.cfd` — TF
- `USDCAD.cfd` — TF + breakout, **D1 filter activ** (singurul!)

**Stocks US NASDAQ/NYSE (7)** — sesiune 17:00-22:30 RO, force close 22:55:
- `NFLX.cfd` (+$300 90d backtest)
- `INTC.cfd` (+$293)
- `GOOGL.cfd` (+$284)
- `AMD.cfd` (+$269) — **earnings blackout 2026-05-04 → 2026-05-08**
- `ORCL.cfd` (+$211)
- `MSFT.cfd` (+$165)
- `AMZN.cfd` (+$149)

**Plus GBPUSD.cfd** — doar pe strategie breakout, nu trend_following.

### 3.2 Stocks SCOASE intentionat

| Simbol | Motiv | Pierdere backtest 90d |
|---|---|---:|
| TSLA | "Meme stock", random walk Twitter-driven | -$149 |
| IBM | Lateralizare, ATR mic, fara momentum | -$68 |
| UBER | Range-bound, fals breakouts | -$31 |
| NVDA | Marginal +$61 dar volatilitate earnings | exclusion preventiva |
| AAPL | Marginal +$24, win rate 25% slab | exclusion |
| META | Marginal +$58 | borderline, possible add later |
| PLTR, BABA | Profit modest, prioritate top 7 | exclusion |

### 3.3 Per-symbol R:R ratio

| Simbol | R:R | Motiv |
|---|---:|---|
| XAU | 3.0 | Backtest A: +$813 |
| USDCHF | 3.0 | Backtest A: +$91 |
| USDCAD | 1.5 | Backtest D: +$151 (cu D1 filter) |
| XAG | 1.5 | Backtest B: +$6636 (cea mai mare contributie) |
| Stocks (toate 7) | 3.0 sau 1.5 cherry-pick | Vezi stocks_backtest results |

### 3.4 Trend-following per-symbol params (din strategies.yaml)

| Simbol | EMA fast | EMA slow | ADX threshold |
|---|---:|---:|---:|
| XAU | 10 | 70 | 30 |
| USDCHF | 10 | 30 | 25 |
| USDCAD | 15 | 70 | 18 |
| XAG | 20 | 50 | 25 |
| Stocks | 20 | 50 (default) | 25 (default) |

(Default: 20/50/25 cu MACD 12/26/9, ADX period 14)

### 3.5 Filtre selective

- **Filtru D1 trend** activ DOAR pe `USDCAD.cfd` (config: `d1_filter: true` in symbol_overrides)
- **Filtru MTF (D1+H4)** pe **breakout** (toate simbolurile breakout, in `superbot/entry_filter.py`)
- **Filtru S/R proximity** pe breakout (0.3% pe forex, 0.5% pe XAU)
- **Filtru swap**: XAUUSD permis doar SELL
- **Filtru sesiune stocks**: 17:00-22:30 RO open window, 22:55 force close, primele 30 min skip dupa open
- **Filtru earnings**: skip ±2 zile fata de data anuntului (din `earnings_calendar.yaml`)

---

## 4. Backtest history (90 zile, balance start $5700)

| Test | Strategie | Total NET | Note |
|---|---|---:|---|
| **A** | Status quo (R:R 3.0, fara filter) | +$2691 | XAU+CHF+CAD+XAG |
| B | R:R 1.5 fara filter | +$6463 | XAG +$6636 dominant |
| C | R:R 3.0 + D1 filter pe toate | -$484 | XAU pierde -$413 (filtrul taie SELL profitabile) |
| D | R:R 1.5 + D1 filter | -$331 | USDCAD merge cu D1, restul nu |
| E | R:R 2.0 + D1 filter | -$645 | rau pe toate |
| **HIBRID** | Per-symbol optim | **+$8525** | XAG DD 95.7% periculos cu lot mare → lot 0.01 fix |
| DCA naiv (suprapuneri) | DCA + hedge | +$14552 | NEREALIST: 978 cicluri/90d pe XAG, ignora margin |
| **DCA realist** | 1 ciclu/simbol + comm + swap | **+$381** | Modest, NU implementat |
| **STOCKS** | TF pe 15 stocks NASDAQ | **+$1721** | Cherry-pick 7 best, $19/zi |

**Decizia finala (2026-05-07):** HIBRID + STOCKS profitabile = $48-69/zi estimat, DD < 15%.

---

## 5. Bug log (rezolvate)

### Bug #1: contract_size = 100000 pe stocks (FIX 2026-05-07)
**Locatie:** `backtesting/backtester.py::_contract_size()`
**Problema:** Default forex (100000) era folosit pentru NVDA/TSLA/etc. → P&L calculat 1000× mai mare.
**Simptom:** backtest stocks afisa +$1.5M / 90 zile = imposibil.
**Fix:** Detectie via `mt5.symbol_info().trade_contract_size` + fallback heuristic per ticker.
**Status:** ✅ Rezolvat. Backtest stocks acum arata $19/zi realist.

### Bug #2: IPC timeout pe VPS Contabo (rezolvat 2026-04-25)
**Cauza:** Generic MetaQuotes MT5 (build 5836) incompatibil cu MetaTrader5 Python lib (5.0.5735).
**Fix:** Zip EarnBroker custom MT5 (build 5833) de pe laptop in `C:\Program Files\Earn Broker MT5 Terminal\` → copy pe VPS la `C:\Users\Administrator\Desktop\EarnBrokerMT5\`.
**Status:** ✅ Rezolvat pe MyForexVPS (Contabo a fost cancelled).

### Bug #3: Volume cap exceeded USDCAD 1.3 lot (rezolvat 2026-04-23)
**Cauza:** `risk_per_trade: 0.5%` calcula volum mare pe SL stransa.
**Fix:** Adaugat `max_risk_per_trade_usd: 28` cap absolut + `simulated_balance: 5700`.
**Status:** ✅ Rezolvat. Volume cap respectat.

### Bug #4: pandas-ta missing pe VPS (rezolvat 2026-04-25)
**Fix:** `pip install pandas-ta==0.4.71b0` (versiune beta).

### Bug #5: 403 Forex Factory news calendar (cosmetic)
**Cauza:** FF blocheaza scraping din anumite IP-uri.
**Fix:** Fallback automat la cache local. Nu blocheaza bot-ul.

### Bug #6: OneDrive log lock pe rotare (cosmetic)
**Cauza:** OneDrive sync lock-uieste fisierul `trading_bot.log` la rotare.
**Status:** Logging continua sa scrie cu eroare cosmetica. Nu blocheaza bot-ul.

### Bug #7: Force-close stocks 22:55 NU se executa — INTC ramasa peste weekend (FIX 2026-05-11)
**Locatie:** `main.py` cap. force-close stocks
**Problema observata:**
- INTC ticket #43468889 deschis vineri 08.05 19:20:23, inchis luni 11.05 15:38:14
- 2.5 zile expunere peste weekend pe stock (in afara sesiunii NASDAQ)
- Force-close 22:55 RO NU s-a executat
**Root cause:** 4 bug-uri cumulate:
1. **Window prea ingust** — doar 5 min (22:55-22:59). Daca bot e busy/lag, ratam window-ul
2. **Timezone naive** — `datetime.now()` foloseste ora server. Daca VPS pe UTC sau alta TZ → fire la ora gresita
3. **Niciun fallback** — daca window 5min ratat o data, stocks ramane open pana zi noua
4. **Niciun startup cleanup** — daca bot restart in weekend, nu detecteaza stocks deschise

**Fix:**
- TZ-aware explicit `ZoneInfo("Europe/Bucharest")` cu fallback la pytz
- Functie `_stocks_should_be_closed(dt)` — verifica weekend SI ora > close_at
- Loop continuu (nu doar in window 5min): la fiecare iteration, daca stocks_should_be_closed → inchide
- Startup cleanup: la pornire bot, daca stocks deschise + in afara sesiunii → force close imediat
- Logging detaliat cu zi (Lun/Marti/...) si ora RO

**Status:** ✅ Fix DEPLOYED pe VPS (commit `a8ff509`, deploy 2026-05-11 17:07 RO). VPS timezone setat la GTB Standard Time (UTC+2 RO) pentru consistenta log-urilor. Test real vineri 16.05 22:55.

### Bug #8: Cooldown 300s ignorat pe stocks dupa broker SL/TP (FIX 2026-05-11, NEDEPLOYAT)
**Locatie:** `main.py::poll_broker_closes()` linia 158
**Problema observata 2026-05-08:**
- INTC #1 inchis 19:02:44 broker TP (+$77.07)
- INTC #2 deschis 19:04:21 — **1m37s** dupa close, sub cooldown 300s
- INTC #2 inchis 19:19:20 broker SL (-$32.27)
- INTC #3 deschis 19:20:23 — **1m03s** dupa close, sub cooldown 300s

**Root cause:** `poll_broker_closes()` salva `out_deal.time` (broker server time, posibil alt timezone) in `symbol_last_close_time`. Cooldown check folosea `time.time()` (UTC epoch local Python). Diferenta de timezone facea `time.time() - last_close` sa fie mult > 300s (sau negativ care comparison-ul tot esua).

**Fix:**
- `symbol_last_close_time[symbol] = time.time()` in loc de `closed_at` (broker time)
- Log cooldown la nivel INFO (era DEBUG) pentru vizibilitate viitoare

**Status:** ⚠️ Fix COMMITTED (commit `d631bcb`) dar **NU DEPLOYAT pe VPS**. Userul a decis sa lase saptamana 12-18 mai 2026 fara modificari. Deploy planificat lunea 19 mai dupa review.

**Workaround pana la deploy:** Cooldown poate fi ignorat ocazional pe stocks dupa SL/TP. Pierderi potentiale estimate < $50/saptamana din re-entry-uri rapide ratate.

### Bug #9: Re-entry dupa TP pe acelasi simbol = capcana trend epuizat (NEFIXAT)
**Pattern observat:** INTC TP +$77 → re-entry imediat → SL -$32. Bot vede semnal H1 inca valid dupa TP-ul anterior, dar trend-ul s-a epuizat. Cooldown 300s ajuta partial dar nu blocheaza complet.
**Posibila solutie:** Cooldown extins (30 min) doar dupa TP hit pe stocks. Sau filter: skip semnal daca acelasi simbol a inchis cu TP in ultimele 60 min.
**Status:** ⚠️ Nedecis. Necesita date 1-2 saptamani sa confirm pattern.

### Bug #10: USDCHF 3 directii in 2 zile = ranging false signals (NEFIXAT)
**Pattern observat 07-08.05:** USDCHF SELL -$22 → BUY -$27 → SELL -$28. Toate au pierdut. Bot ia ambele directii desi simbolul e in RANGING.
**Posibile solutii:**
- A) Filter: daca un simbol a pierdut 2+ trade-uri in ultimele 24h → auto-disable 12h (mai sever decat min_win_rate=40 actual)
- B) Scot USDCHF temporar pana H1 trend devine clar
- C) Reduce R:R USDCHF la 1.5 (similar XAG/USDCAD)
- D) Las performance_tracker sa lucreze (auto-disable 6h dupa 4 trade-uri WR<40%)
**Status:** ⚠️ Nedecis. User a optat pentru D — observatie pasiva o saptamana, decizie pe 19 mai.

---

## 6. VPS deployment

### 6.1 Locatie pe VPS

- **Bot:** `C:\Users\Administrator\Desktop\bot\`
- **MT5 terminal:** `C:\Users\Administrator\Desktop\EarnBrokerMT5\terminal64.exe` (build 5833)
- **Python:** `C:\Users\Administrator\Desktop\bot\.venv\Scripts\python.exe` (3.12)
- **Logs:** `C:\Users\Administrator\Desktop\bot\logs\trading_bot.log`

### 6.2 Procedura deploy (fara git pe VPS)

GitHub repo este **privat** (`dancarlova-atpsor/mt5-trading-bot`). Pentru deploy:

**Pe LAPTOP:**
1. Commit + push toate modificarile:
   ```bash
   git add -A && git commit -m "..." && git push origin main
   ```
2. In browser: Settings → Danger Zone → **Make public** temporar.

**Pe VPS (PowerShell ca Administrator):**
```powershell
cd C:\Users\Administrator\Desktop\bot

# Backup config local (in caz de modificari pe VPS)
$backup = "$env:USERPROFILE\Desktop\bot-backup-$(Get-Date -Format 'yyyyMMdd-HHmm')"
New-Item -ItemType Directory -Path $backup -Force
Copy-Item "config\*.yaml" -Destination $backup -Force

# Download zip de pe GitHub
$zipUrl = "https://github.com/dancarlova-atpsor/mt5-trading-bot/archive/refs/heads/main.zip"
$zipPath = "$env:TEMP\bot-latest.zip"
$extractPath = "$env:TEMP\bot-extracted"
if (Test-Path $extractPath) { Remove-Item $extractPath -Recurse -Force }
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force

# Overlay (PASTREAZA .venv, logs, .env)
$source = "$extractPath\mt5-trading-bot-main"
$dest = "C:\Users\Administrator\Desktop\bot"
robocopy $source $dest /E /XD .venv logs __pycache__ .git venv /XF .env /R:1 /W:1 | Out-Null

# Restart bot
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
.\.venv\Scripts\python.exe main.py
```

**Pe LAPTOP (dupa deploy reusit):**
- Settings → Danger Zone → **Make private** (revine la privat).

### 6.3 Verificare deploy

Cele 8 fisiere cheie trebuie sa aiba data zilei deploy-ului:
```powershell
Get-Item "config\settings.yaml", "config\strategies.yaml", "config\risk_management.yaml", "config\earnings_calendar.yaml", "main.py", "superbot\superbot.py", "superbot\entry_filter.py", "risk\risk_manager.py" | Select-Object Name, LastWriteTime
```

### 6.4 Daca bot-ul cade

Verifica fereastra PowerShell ramasa deschisa pe RDP. Daca a fost inchisa accidental:
```powershell
cd C:\Users\Administrator\Desktop\bot
.\.venv\Scripts\python.exe main.py
```

**TODO viitor:** Bot ca Windows Service (auto-restart la boot, nu mai depinde de fereastra deschisa). User a confirmat ca e nice-to-have.

---

## 7. Earnings calendar — update trimestrial

Fisier: `config/earnings_calendar.yaml`

Bot face skip ±2 zile fata de data fiecarui anunt.

**Datele aproximative 2026 (DE VERIFICAT pe earningswhispers.com):**

| Simbol | Q1 (Feb) | Q2 (May) | Q3 (Aug) | Q4 (Nov) |
|---|---|---|---|---|
| NVDA | ~26 Feb | ~22 May | ~21 Aug | ~19 Nov |
| AMD | ~04 Feb | ~06 May | ~05 Aug | ~04 Nov |
| AMZN | ~06 Feb | ~30 Apr | ~31 Jul | ~30 Oct |
| GOOGL | ~04 Feb | ~29 Apr | ~22 Jul | ~28 Oct |
| MSFT | ~28 Jan | ~29 Apr | ~22 Jul | ~28 Oct |
| INTC | ~22 Jan | ~24 Apr | ~24 Jul | ~23 Oct |
| NFLX | ~21 Jan | ~17 Apr | ~17 Jul | ~21 Oct |
| ORCL | ~10 Mar | ~10 Jun | ~10 Sep | ~10 Dec |

**La inceput de fiecare trimestru** (martie, iunie, septembrie, decembrie):
1. Verifica datele exacte pe earningswhispers.com
2. Updateaza `config/earnings_calendar.yaml`
3. Commit + push + deploy

---

## 8. Daily monitoring

### 8.1 Telegram notifications

Bot trimite pe Telegram (chat 8374879733):
- ✅ Bot pornit / oprit
- ✅ Trade deschis (cu SL/TP/volum)
- ✅ Trade inchis (cu PnL)
- ✅ Erori critice
- ✅ Daily summary (orele 23:30)

### 8.2 Logs

Pe VPS: `tail -f C:\Users\Administrator\Desktop\bot\logs\trading_bot.log`

Cauta linii cu:
- `SEMNAL` — semnale generate de strategii
- `Ordine executata` — trade-uri deschise
- `BLOCKED` — semnale blocate de filtre
- `PROFIT MANAGER` — trailing/cycle close
- `force-close` — stocks force closed la 22:55
- `ERROR` / `CRITICAL` — probleme

### 8.3 Schedule zilnic asteptat

| Ora RO | Eveniment |
|---|---|
| 00:00-09:00 | Forex/metals overnight (low activity) |
| 09:00-16:30 | Forex/metals (sesiune EU+) |
| 16:30 | NYSE deschide (bot inca asteapta 17:00) |
| **17:00** | Bot incepe semnale stocks |
| **22:30** | Bot opreste stocks noi |
| **22:55** | Force close stocks (chiar pe minus) |
| 23:00 | NYSE inchide |

### 8.4 Anomalii de raportat

- Trade pe simboluri scoase (TSLA, IBM, UBER, NVDA, AAPL, META, PLTR, BABA)
- Trade pe XAUUSD BUY (ar trebui blocat din swap filter)
- Stocks deschise dupa 22:55
- Stocks deschise inainte de 17:00 RO
- Trade pe AMD intre 4-8 mai 2026 (earnings blackout)
- DD pe XAG > 20% — re-evaluez strategia

---

## 9. Decision log — de ce am ales fiecare optiune

### 9.1 De ce HIBRID per-symbol R:R?
Backtest a aratat ca R:R 3.0 functioneaza pe XAU/USDCHF (trenduri puternice → TP atins) dar **nu pe XAG/USDCAD** (volatilitate mare → SL atins inainte de TP). R:R 1.5 la XAG/USDCAD = +$6787 vs R:R 3.0 = +$1786 pe aceleasi simboluri.

### 9.2 De ce D1 filter doar pe USDCAD?
Backtest C (D1 filter pe toate) = -$484. **D1 filter taie SELL-uri counter-D1 care erau profitabile pe XAU** (pullback-uri in trend). Pe USDCAD insa filtrul a crescut WR de la 27% la 50% (+$151).

### 9.3 De ce XAG cu lot fix 0.01?
HIBRID complet pe XAG (R:R 1.5, lot risk-based) a aratat +$7382 dar **DD 95.7%** = blow-up account. Lot fix 0.01 limiteaza expunerea la ~$80 per trade (XAG @ $80 × 0.01 lot × 5000 contract = $4 per pip). DD limitat la <15%.

### 9.4 De ce DCA + Hedge NU implementat?
Backtest naiv arata +$14552 dar **a fost iluzie** (suprapuneri imposibile, fara swap, fara comm). Backtest realist (1 ciclu/simbol + comm + swap + cooldown 24h) = doar +$381 = $4.23/zi. Sub status quo. Plus **`max_open_positions: 3`** nu permite 4 BUY-uri + 1 hedge simultan.

### 9.5 De ce stocks doar 7 din 15?
Cherry-pick din backtest 90 zile. Profitabile (>$100): NFLX, INTC, GOOGL, AMD, ORCL, MSFT, AMZN. Pierzatoare: TSLA (-$149), IBM (-$68), UBER (-$31). Marginale (<$100): NVDA, AAPL, META, PLTR, BABA — exclusion preventiva pentru focus pe 7 confirmate.

### 9.6 De ce force-close stocks 22:55?
- NYSE inchide 23:00 RO
- Gap overnight = pierderi necontrolate (earnings, news, fed)
- Weekend gap = pierderi de luni
- Stocks fara swap → niciun motiv sa tinem peste noapte
- User confirm: **inchide tot, chiar pe minus** (decizie 2026-05-07)

### 9.7 De ce XAUUSD BUY blocat?
Swap BUY = -$61/noapte/lot pe XAU. Pe trade tinut 5 nopti = -$300 cost ascuns. SELL swap = ~$0. Decis 2026-04-23.

### 9.8 De ce simulated_balance 5700?
User are cont real 5300 EUR. Vrea sa testeze pe demo "ca si cum" ar avea 5300 EUR. Risk_manager foloseste 5700 USD (echivalent) pentru calcul lot, ignora balance demo $98k. Permite cap absolut $28/trade.

### 9.9 De ce trailing stop dezactivat?
Trailing pip-based (30 pips activate, 15 trail) era prea agresiv pe XAU (30 pips = 30 cents). ProfitManager tier-based (profit-USD-based) e mai eficient: 50% trailing pana $50, 35% pana $100, 25% peste $100.

---

## 10. Cont info

### 10.1 EarnBroker MT5
- **Cont:** `9047479` (demo, dar pentru "real test 5300 EUR")
- **Server:** `EarnBroker-Sharp ECN`
- **Mod:** demo
- **Build MT5:** 5833 (custom EarnBroker, OBLIGATORIU — generic 5836 nu merge cu Python lib)
- **Leverage:** 1:100 (forex), ~1:5 (stocks CFD)
- **Magic number:** `123456`
- **Balance demo:** ~$98,624 (irelevant, folosim simulated_balance)

### 10.2 Telegram
- **Bot token:** `8689397349:AAGMYNnNquOkoHepkcIPWm1vs7Xo_g-WO3I` (in settings.yaml)
- **Chat ID:** `8374879733`

### 10.3 GitHub
- **Repo:** `https://github.com/dancarlova-atpsor/mt5-trading-bot`
- **Branch principal:** `main`
- **Vizibilitate:** **PRIVAT** (fa public temporar pentru deploy VPS, apoi privat inapoi)
- **User git local:** Dan Cirlova (din git config)

### 10.4 VPS
- **Provider:** MyForexVPS Silver Windows 2019
- **Pret:** $14.99/luna
- **OS:** Windows Server 2019
- **User:** Administrator
- **RDP:** clipboard activat (pentru transfer fisiere)

---

## 11. Common operations — cum fac X

### 11.1 Cum modific R:R pentru un simbol
**Fisier:** `config/strategies.yaml`
```yaml
trend_following:
  symbol_overrides:
    XAGUSD.cfd:
      rr_ratio: 2.0   # de la 1.5
```
Apoi: commit + push + deploy + restart bot.

### 11.2 Cum adaug un simbol nou
1. **Verifica disponibilitate broker:**
   ```python
   import MetaTrader5 as mt5
   mt5.initialize()
   info = mt5.symbol_info("NEWSYM.cfd")
   ```
2. **Adauga in `config/settings.yaml` → symbols list**
3. **Adauga in `config/strategies.yaml` → trend_following.symbols + symbol_overrides**
4. **Backtest 90 zile inainte de deploy:**
   ```powershell
   .\.venv\Scripts\python.exe backtesting/compare_options.py
   ```
5. Daca rezultate OK → commit + push + deploy.

### 11.3 Cum scot un simbol pierzator
1. Comenteaza in `config/settings.yaml` si `config/strategies.yaml`
2. Inchide manual pozitiile deschise pe simbolul ala (din MT5)
3. Commit + push + deploy.

### 11.4 Cum modific orele sesiune stocks
**Fisier:** `config/settings.yaml`
```yaml
stocks_session:
  open_delay_minutes: 30      # Skip primele 30 min dupa NYSE open
  close_buffer_minutes: 25    # Skip ultimele 25 min
  force_close_at: "22:55"     # Force close
```

### 11.5 Cum updatez earnings calendar
**Fisier:** `config/earnings_calendar.yaml`
```yaml
NVDA.cfd:
  - "2026-05-22"
  - "2026-08-21"
```
Bot face skip ±2 zile automat.

### 11.6 Cum verific status bot
**Pe VPS PowerShell:**
```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id, StartTime
```
Daca apare 1-2 procese Python active → bot OK.

**Logs ultimele 50 linii:**
```powershell
Get-Content C:\Users\Administrator\Desktop\bot\logs\trading_bot.log -Tail 50
```

### 11.7 Cum opresc bot-ul
```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

### 11.8 Cum pornesc bot-ul
```powershell
cd C:\Users\Administrator\Desktop\bot
.\.venv\Scripts\python.exe main.py
```
**LASA fereastra deschisa.** Daca o inchizi → bot oprit.

### 11.9 Cum rulez backtest local (laptop)
```powershell
cd "C:\Users\danci\OneDrive\Desktop\MT 5\mt5-trading-bot"
.\.venv\Scripts\python.exe backtesting/compare_options.py
```
MT5 trebuie deschis si logat pe laptop.

---

## 12. Quirks si feedback de la user

### 12.1 User feedback important
- **"Doar analizam, nu modificam"** — cand user cere analiza, NU implementa pana nu spune explicit "implementeaza"
- **"Las cum e — backtest 90 zile zice profitabil"** — user prefera sa nu schimbe daca dovezile arata ca status quo merge
- User vrea **transparenta totala** despre limitele simularilor (ex: DCA naiv vs realist)
- User raspunde **majuscule + scurt** uneori — semn ca e nervos sau in graba, nu este nepoliticos
- User vorbeste **Romanian / Romglish** — raspunde in aceeasi limba

### 12.2 Greseli mele de evitat
- ❌ Nu inchide pozitii fara confirmare user (am inchis o data fara sa intreb)
- ❌ Nu rula backtest in paralel cu MT5 ocupat (problema concurenta API)
- ❌ Nu folosi mock data in backtest fara sa avertizez explicit
- ❌ Nu prezinta rezultate "prea bune" fara disclaimer (DCA naiv vs realist exemplu)
- ❌ Nu propune modificari masive la cont 5300 EUR fara backtest 90+ zile
- ❌ Nu deploy pe VPS direct — intreaba intai daca user confirma
- ❌ Nu modifica fisiere config fara sa fac backup si sa avertizez user

### 12.3 Lectii invatate
- **Backtest naiv ≠ realitate.** Mereu adauga: comisioane, swap, max_pozitii, cooldown, max_lot
- **Win rate mare poate ascunde DD mare.** XAG 99% WR cu DD 95% = tot blow-up
- **R:R per simbol** > R:R global. Ce merge pe XAU nu merge pe XAG
- **Filtre selective** > filtre universale. D1 filter pe toate simbolurile = pierde, pe USDCAD doar = castiga
- **Stocks au avantaj structural:** zero swap, zero weekend, sesiune limitata = expunere controlata

---

## 13. Roadmap viitor (nice-to-have)

1. **Bot ca Windows Service** — auto-start la boot VPS, fara fereastra PowerShell deschisa
2. **Auto-restart la crash** — supervisor process
3. **Email backup notifications** — pe langa Telegram
4. **API earnings calendar** — automate update (FinancialModelingPrep / Yahoo Finance)
5. **Dashboard web** — `dashboard/` exista in proiect, neactivat
6. **Migrare cont real** — dupa 30 zile profit consistent pe demo
7. **Adaugare crypto** (BTC, ETH) — necesita strategie diferita (24/7, alta volatilitate)
8. **Strategy switcher per regime** — daca XAU intra in RANGING, switch trend_following → mean_reversion

---

## 14. Comenzi utile pentru noi sesiuni

### Citeste statusul bot
```powershell
# Pe VPS
ssh sau RDP, apoi:
Get-Process python | Select-Object Id, StartTime
Get-Content C:\Users\Administrator\Desktop\bot\logs\trading_bot.log -Tail 30
```

### Verifica simboluri active
```python
import yaml
with open('config/settings.yaml') as f: print(yaml.safe_load(f)['symbols'])
```

### Ruleaza backtest comparativ
```powershell
cd "C:\Users\danci\OneDrive\Desktop\MT 5\mt5-trading-bot"
.\.venv\Scripts\python.exe backtesting/compare_options.py
.\.venv\Scripts\python.exe backtesting/stocks_backtest.py
.\.venv\Scripts\python.exe backtesting/hybrid_backtest.py
```

### Verifica trade-uri active pe MT5
```python
import MetaTrader5 as mt5
mt5.initialize()
positions = mt5.positions_get()
for p in positions:
    print(f'{p.ticket} {p.symbol} {p.type_str} vol={p.volume} pnl=${p.profit:.2f}')
```

---

## 15. Contact / escalation

- **User Telegram:** chat 8374879733 (notificari bot)
- **GitHub repo:** dancarlova-atpsor/mt5-trading-bot (privat)
- **MT5 Demo:** EarnBroker support (chat in MT5)

---

## 16. Reference commits & deploy snapshot

| Data | Commit | Descriere | Status VPS |
|---|---|---|---|
| 2026-05-11 17:00 RO | `d631bcb` | Fix Bug #8 cooldown (broker time vs UTC) | ⚠️ NEDEPLOYAT |
| 2026-05-11 16:00 RO | `a8ff509` | Fix Bug #7 force-close stocks 22:55 ZoneInfo | ✅ DEPLOYAT |
| 2026-05-07 15:30 RO | `478580a` | CLAUDE.md complet 100% | ✅ pe GitHub |
| 2026-05-07 14:30 RO | `ab39a87` | HIBRID + STOCKS — strategia activa pe VPS | ✅ DEPLOYAT |
| Anterior 2026-04-28 | `e9b2f5d` | Config REAL TEST 5300 EUR (simulated_balance) | — |
| Anterior 2026-04-25 | `9504f58` | ProfitManager tier-based | — |
| Anterior 2026-04-22 | `e18bab7` | TrendFollowing per-symbol params (grid search) | — |
| Anterior 2026-04-19 | `f2151e0` | Entry filter selectiv (doar pe Breakout) | — |

**Pentru rollback la status quo (pre-stocks):** `git checkout e9b2f5d -- config/`

**Versiune ruleaza pe VPS in saptamana 12-18 mai 2026:** `a8ff509` (cu fix force-close, fara fix cooldown). Deploy `d631bcb` planificat lunea 19 mai dupa review.

---

## 17. OneDrive sync workflow

**Laptop:** `C:\Users\danci\OneDrive\Desktop\MT 5\mt5-trading-bot\` (synced via OneDrive Personal cu cont Microsoft).

**Workflow normal:**
1. Modificari → fac pe laptop in `OneDrive/Desktop/MT 5/...`
2. Test local (laptop are MT5 deschis)
3. `git add -A && git commit && git push origin main`
4. Pe VPS: download zip GitHub + overlay (vezi sectiunea 6.2)

**OneDrive sync NU functioneaza pe VPS** (testat 2026-04-25 — sync e instabil pe Windows Server). Deci:
- ❌ Modificari pe VPS = nu se propaga inapoi pe laptop
- ✅ Modificari pe laptop = trec prin GitHub (manual deploy pe VPS)

**Backup automat laptop:** OneDrive face cloud backup la `mt5-trading-bot/`. Fisiere importante (configs, logs vechi) nu se pierd daca laptop crash.

**Backup VPS:** **NU EXISTA** automat. Daca crapa VPS-ul, fisierele de pe VPS se pierd. Singurul "backup" e GitHub repo. **Atentie: log-urile de pe VPS NU sunt in GitHub** (`logs/` e in `.gitignore`).

---

## 18. Trade history real (ultimele 2 saptamani)

### 2026-04-28 → 2026-05-07 (status quo + initial XAG)

| Data | Simbol | Tip | Vol | PnL | Note |
|---|---|---|---:|---:|---|
| 29.04 01:39 | USDCAD | BUY | 0.13 | +$11.96 | ✓ |
| 01.05 00:03 | USDCHF | SELL | 0.10 | +$3.20 | ✓ |
| 01.05 00:03 | USDCAD | SELL | 0.10 | +$10.10 | ✓ |
| 04.05 13:57 | USDCHF | BUY | 0.13 | -$28.07 | ✗ counter-D1 |
| 04.05 15:11 | USDCHF | BUY | 0.13 | +$11.28 | ✓ |
| 04.05 17:51 | XAUUSD | SELL | 0.01 | -$33.66 | ✗ retrace lung |
| 05.05 01:01 | USDCAD | BUY | 0.13 | -$13.18 | ✗ counter-D1 |
| 06.05 12:01 | USDCHF | SELL | 0.13 | +$8.85 | ✓ |
| 07.05 03:02 | USDCAD | BUY | 0.13 | -$14.12 | ✗ counter-D1 |

**Statistica:** 9 trade-uri, WR 55.6%, NET **-$43.64**.
**Pattern identificat:** 4/4 pierderi = trade-uri counter-D1 → motivul pentru filtru D1 selectiv pe USDCAD.

### Pozitii deschise la deploy (2026-05-07 15:04)
- USDCHF SELL 0.13 (vechi, ramas de la old config) — gestionata de bot nou via magic 123456
- XAGUSD BUY 0.01 (NEW deschis de bot nou imediat la pornire) — R:R 1.5 confirmed working

### 2026-05-07 → 2026-05-11 (post-deploy HIBRID + STOCKS, primele 4 zile)

| Data | Simbol | Tip | Vol | PnL | Outcome |
|---|---|---|---:|---:|---|
| 07.05 15:04 | XAGUSD | buy | 0.01 | -$9.90 | SL hit |
| 07.05 17:00 | ORCL | buy | 0.03 | -$13.23 | SL hit |
| 07.05 18:02 | XAGUSD | buy | 0.01 | -$10.70 | SL hit |
| 07.05 10:29 | USDCHF | sell | 0.13 | -$22.70 | SL hit |
| 07.05 22:00 | USDCHF | buy | 0.13 | -$27.05 | trailing close |
| 08.05 17:38 | INTC | buy | 0.07 | **+$77.07** | TP hit ✓ |
| 08.05 19:04 | INTC | buy | 0.07 | -$32.27 | SL hit (re-entry rapid!) |
| 08.05 19:20 | INTC | buy | 0.03 | +$8.55 | trailing close LUNI (Bug #7!) |
| 08.05 20:06 | USDCHF | sell | 0.13 | -$28.23 | broker close luni 00:00 |
| 11.05 17:07 | INTC | buy | 0.05 | OPEN | R:R 3.0 confirmed |

**Statistica 9 trade-uri inchise:** WR 22% (2W/7L), NET **-$58.46** in 4 zile = **-$14.6/zi** (vs target +$48-69/zi).

**Pattern-uri identificate:**
1. Singurul win mare (+$77 INTC TP) — trend stabilit dimineata pe stock
2. 4/9 pierderi = re-entry rapid pe acelasi simbol (Bug #8 cooldown)
3. 3/9 pierderi = USDCHF 3 directii in 2 zile (Bug #10 ranging false signals)
4. 1/9 = INTC ramasa weekend (Bug #7, deja fixat)

**Decizie 2026-05-11:** Observare pasiva pana 19 mai. Performance_tracker va auto-disable simboluri WR<40% dupa 4 trade-uri.

---

## 19. Task Scheduler pe VPS — `close_swap_protection.py`

**Status:** Necunoscut dupa deploy 2026-05-07. **DE VERIFICAT** la prima sesiune noua.

**Ce face:** Script standalone care se conecteaza la MT5, inchide pozitii cu PnL > $5 inainte de:
- Miercuri 22:55 RO (inainte de swap triplu Wed→Thu)
- Vineri 22:55 RO (inainte de weekend)

**Verificare pe VPS:**
```powershell
schtasks /query /TN "close_swap_protection*" /v /fo LIST 2>&1 | Select-String -Pattern "TaskName|Status|Next Run|Last Run"
```

**Daca task-urile NU exista, recreeaza:**
```powershell
$python = "C:\Users\Administrator\Desktop\bot\.venv\Scripts\python.exe"
$script = "C:\Users\Administrator\Desktop\bot\close_swap_protection.py"
$workdir = "C:\Users\Administrator\Desktop\bot"

# Miercuri
schtasks /create /tn "close_swap_protection_wed" /tr "$python `"$script`"" /sc weekly /d WED /st 22:55 /ru "Administrator" /rp "<PAROLA_ADMIN>" /f

# Vineri
schtasks /create /tn "close_swap_protection_fri" /tr "$python `"$script`"" /sc weekly /d FRI /st 22:55 /ru "Administrator" /rp "<PAROLA_ADMIN>" /f
```

**Note:** Bot-ul principal cu force-close 22:55 acopera doar **stocks**, NU forex/metals. Task-urile separate `close_swap_protection` acopera **forex/metals profitabile** Mier+Vin.

---

## 20. Profit Manager — tier-based trailing detaliu

**Fisier:** `risk/profit_manager.py::_get_trail_pct_for_peak()`

| Peak profit atins | Trail % | Lock % din peak | Exemplu |
|---:|---:|---:|---|
| < $20 | 0% (no trail) | — | Doar SL broker activ |
| $20 - $50 | 50% | 50% | Peak $40 → close la $20 |
| $50 - $100 | 35% | 65% | Peak $80 → close la $52 |
| > $100 | 25% | 75% | Peak $150 → close la $112 |

**Activare trailing:** la `+40 EUR` profit cumulativ pe pozitie (din settings.yaml).

**Cycle target:** +150 EUR profit cumulativ pe **ciclu** (dintre toate pozitiile inchise consecutive). La +150 EUR → pauza 5 min, apoi reia.

**Daily limits:**
- `max_daily_profit_eur: 300` → bot opreste pana la urmatoarea zi
- `max_daily_loss_eur: 53` → bot opreste pana la urmatoarea zi (~1% din 5324 EUR)
- IN PARALEL: `max_daily_loss_percent: 1.0` din `risk_management.yaml` (calculat pe `simulated_balance: 5700` USD = $57)

**Doua mecanisme separate** — primul atins opreste bot-ul.

---

## 21. SuperBot config detaliu

**Fisier:** `superbot/superbot.py` + `config/settings.yaml::superbot`

```yaml
superbot:
  enabled: true
  deep_analysis_interval_iterations: 30    # 30 × 60s = 30 min
  regime:
    adx_period: 14
    adx_trend_threshold: 25                # ADX > 25 → TRENDING
    atr_period: 14
    bb_period: 20
    volatility_percentile_high: 80         # > P80 → VOLATILE
    quiet_percentile_low: 20               # < P20 → QUIET (skip!)
  performance:
    window_hours: 24
    min_trades_to_evaluate: 4              # Sub 4 trade/24h → nu evaluez
    min_win_rate: 40                       # WR < 40% → DISABLE simbol 6h
    disable_hours: 6
    history_file: "logs/superbot/performance.json"
  correlation:
    timeframe: "M15"
    lookback_bars: 120
    max_abs_corr: 0.75                     # Daca avem deja BUY EURUSD si bot vrea BUY GBPUSD cu corr 0.85 → blocat
  calibrator:
    regime_size_mult:
      TRENDING: 1.3                        # Trade-uri pe trend → lot ×1.3
      RANGING: 0.7                         # Range → lot ×0.7
      VOLATILE: 0.5                        # Volatil → lot ×0.5
      QUIET: 0.0                           # Linistit → SKIP (size 0)
      UNKNOWN: 0.0
    skip_quiet: true
    low_perf_penalty: 0.7                  # Performer slab → lot ×0.7 in plus
    min_perf_win_rate: 45
```

**Flow gate_trade():**
1. Filter swap (XAU BUY blocked)
2. Filter D1 selectiv (doar USDCAD)
3. Filter MTF + S/R (doar pe Breakout)
4. Filter correlation (max 0.75 abs)
5. Filter performance (auto-disable 6h)
6. Calibrator size mult (regime + perf)
7. → trade approved sau blocked

---

## 22. Componente proiect — descriere

### `mql5/` folder
EA pentru MT5 fallback (mod execution `mql5_ea` sau `both`).
- `ExecutionEA.mq5` — primeste signal-uri din `MQL5/Files/` si executa
- `SignalReceiver.mq5` — citeste fisier signal scris de Python
- `files/` — director comunicare Python ↔ EA

**Status actual:** NEACTIV. `execution.mode: "python_api"` foloseste direct `mt5.order_send()` din Python. EA-urile sunt fallback daca python API se rupe (ex: blocaj IPC).

### `dashboard/app.py`
Streamlit dashboard cu metrici live. **NEACTIV** (nu e pornit la deploy).

**Pentru a porni manual:**
```powershell
cd C:\Users\Administrator\Desktop\bot
.\.venv\Scripts\streamlit.exe run dashboard\app.py --server.port 8501
```
Acceseaza: http://localhost:8501 (pe VPS via RDP, sau prin tunnel SSH).

### `analysis/multitf_analysis.py`
Script ad-hoc pentru analiza multi-TF a oricarui simbol/perioada. Util pentru investigatii dupa pierderi.

### `tests/` folder
Pytest tests:
- `test_backtester.py`
- `test_risk_manager.py`
- `test_strategies.py`
- `test_superbot.py`

**Cum rulezi:**
```powershell
cd C:\Users\danci\OneDrive\Desktop\MT 5\mt5-trading-bot
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

**Status acoperire:** ~30%. Multe componente noi (entry_filter, calibrator, stocks session) FARA teste. **TODO viitor.**

### `core/` folder
- `mt5_connector.py` — initialize MT5, connect/login, retry logic
- `order_manager.py` — order_send wrapper, retry pe `requote`/`off-quotes`

---

## 23. Decision tree pentru o noua sesiune

### 23.1 Daca user spune "review trading bot"
1. Citeste `CLAUDE.md` (asta tu)
2. Verifica logs ultimele 24h pe VPS:
   ```powershell
   Get-Content C:\Users\Administrator\Desktop\bot\logs\trading_bot.log -Tail 200
   ```
3. Verifica trade-uri Telegram (ultimele 7 zile)
4. Calculeaza WR si NET P&L
5. Compara cu estimare $48-69/zi (target)
6. Identifica pattern-uri de pierdere
7. Propune (DAR NU IMPLEMENTA) ajustari

### 23.2 Daca user spune "bot-ul a pierdut bani"
1. **Pattern check:** trade-urile pierdute sunt counter-D1? Pe acelasi simbol?
2. **Regime check:** simbolul a trecut din TRENDING in RANGING?
3. **Performance tracker:** simbolul a fost auto-disabled?
4. **Earnings:** s-a tranzactionat pe simbol cu earnings recent? (verifica `earnings_calendar.yaml`)
5. **News:** blackout active? (Forex Factory)

### 23.3 Daca user vrea sa adauge un simbol nou
1. **Backtest 90 zile OBLIGATORIU** inainte de deploy (`backtesting/compare_options.py`)
2. Daca simbol stock → adauga si in `earnings_calendar.yaml`
3. Verifica `mt5.symbol_info()` — disponibil pe broker?
4. Daca metale/forex → verifica swap (XAU style trap)
5. Cherry-pick R:R per backtest (3.0 sau 1.5)
6. NU implementa direct — propune si astepta confirmare user

### 23.4 Daca user spune "vreau strategie noua"
1. Backtest **3 variante minimum** (status quo, optiune A, optiune B)
2. **MENTIONEAZA disclaimer:** backtest = upper bound, real ~50-70%
3. Verifica `max_open_positions: 3` — strategia incape?
4. Verifica `max_lot: 0.10` — leverage OK?
5. **NU implementa pana nu raspunde user** la trade-off-uri
6. Daca user aproba → cod + commit + deploy + restart

### 23.5 Daca bot-ul nu mai ruleaza
1. SSH/RDP pe VPS
2. `Get-Process python` — vezi cati ruleaza
3. Daca 0 → bot-ul a fost oprit (fereastra inchisa? VPS reboot?)
4. `Get-Content logs\trading_bot.log -Tail 100` — vezi ce a fost ultimul mesaj
5. Cauta `ERROR`, `CRITICAL`, `Traceback`
6. Daca CONTROLLED stop → reporneste cu `.\.venv\Scripts\python.exe main.py`
7. Daca CRASH → escaleaza la user, debug logs

### 23.6 Daca user vrea trecere pe cont real
**STOP. Conditii minime:**
- Cel putin **30 zile profit constant** pe demo
- DD < 15% confirmed
- Win rate > 40%
- Earnings calendar updated
- Backup VPS (snapshot)

**Dupa confirmare user:**
1. Modifica `config/settings.yaml::account.mode: real`
2. Modifica `account.number` la cont real
3. Set env var `MT5_PASSWORD` real
4. **NU pleaca de acolo** — ramai online primele 24-48h sa verifici

---

## 24. Lessons learned (extra)

### 24.1 Backtest pitfalls (2026-05-07)
- **`init_mt5()` lipsea in `hybrid_backtest.py`** → toate simbolurile SKIP. Mereu inclus init la inceput de `main()`.
- **`contract_size = 100000` default forex** → catastrofa pe stocks. **Mereu detect via `mt5.symbol_info().trade_contract_size`**.
- **DCA naiv = simulare nerealista.** Mereu adauga: cooldown, comm, swap, max_pozitii, max_lot.
- **Win rate 99% poate fi capcana** — daca `HARD_SL` sterge ~20% din profit total, WR e iluzoriu.

### 24.2 Volatility-aware decisions
- XAU 4500 → 4700 in 2 zile (mai 2026) = ATR H1 a sarit de la 30 la 50 → SL stransa = stop hit constant
- **R:R 3.0 functioneaza pe trenduri stabile** (USDCHF, USDCAD luni de zile direction lent)
- **R:R 1.5 functioneaza pe volatilitate mare** (XAU, XAG, stocks intra-day)
- **NU schimba R:R global** — schimba per-symbol

### 24.3 Stocks specifics
- Spread = 0 advertise, dar fill **slippage 0.1-0.5%** in primele 30 min open (de aceea skip)
- Earnings = miscare ±10-20% in afterhours → **gap peste SL**, broker te inchide la pretul gap
- Volume real = 0.05-0.15 lot pe trade (5-15 actiuni) — expunere $500-2000/trade
- Liquidity overnight = ZERO → bot **NU TINE pozitii peste 23:00 RO** (force close 22:55)

### 24.4 User behavior patterns
- User raspunde MAJUSCULE = e in graba sau frustrat. **Raspunde scurt, fara introduceri lungi**.
- User cere "doar analiza" → NU implementa, doar prezinta. Asteapta confirmare explicita.
- User accepta backtest disclaimer-ul → da-i adevarul brut, nu numere optimiste.
- User vrea **decizii rapide cu numere clare**, nu raspunsuri vagi cu "ar trebui sa testam mai mult".

### 24.5 VPS gotchas
- **EarnBroker custom MT5 (build 5833)** OBLIGATORIU — generic MetaQuotes (5836) nu e compatibil cu Python lib 5.0.5735
- **PowerShell window inchis = bot oprit.** Nice-to-have: Windows Service.
- **OneDrive nu sync pe VPS** — folosim git deploy
- **Repo privat** — public temporar pentru deploy, private inapoi
- **Task Scheduler can break** dupa update Windows. Verifica periodic.

### 24.6 Crypto symbols available (NEUTILIZATE)
Pe broker EarnBroker exista (din `pips_to_price`):
- `BTCUSD.cfd` — pip = 1.0
- `ETHUSD.cfd` — pip = 0.1
- `SOLUSD.cfd` — pip = 0.01

**STATUS:** netranzactionate. Backtest-uite niciodata. Daca user vrea — necesita strategie diferita (24/7 trading, swap diferit, volatilitate mare).

---

## 25. Quick reference card (cheat sheet)

```
STRATEGIE ACTIVA:      METAL-ONLY (XAU + XAG) — simplificare 18.05 dupa review
SIMBOLURI:             2 (XAUUSD.cfd SELL only, XAGUSD.cfd 0.01 lot fix)
TARGET ZILNIC:         $50-80/zi pe baza performanta reala metale
DD MAX ACCEPTABIL:     10% (~$570) — mai conservator fara stocks
TIMEZONE VPS:          GTB Standard Time (UTC+2 RO) — schimbat 2026-05-11
COMMIT PENDING DEPLOY: (acest commit — METAL-ONLY + fix cooldown d631bcb)
PERFORMANTA REVIEW:    Week 11-15.05: NET -$10 (WR 40%) — metale +$80, stocks/forex -$90
DECIZIE 2026-05-18:    A+B+C aprobat: scot stocks + USDCHF + USDCAD + GBPUSD
USER PREFERENCES:      Romanian, decizii rapide, fara fluff, MAJUSCULE = in graba
NU IMPLEMENTA fara:    Backtest 90+ zile + confirmare explicita user
BUG-URI ACTIVE:        #7 force-close: FIXED + DEPLOYED (testat joi 14.05 ✓)
                       #8 cooldown: fix committed, deploy odata cu METAL-ONLY
                       #10 USDCHF ranging — REZOLVAT prin scoatere simbol
                       #11 stocks weekend retry — REZOLVAT prin scoatere stocks
TASK SCHEDULER VPS:    close_swap_protection_wed/fri fired la 23:55 (gresit)
                       — DE RECREAT cu timpul corect 22:55 RO
```

---

---

## 26. SESIUNE 2026-05-25 — STRESS TEST MASIV + DECIZIE OIL-STRATEGY (PENDING)

### 26.1 Context
- METAL-ONLY (XAU + XAG) deployed 2026-05-18
- Week 18-25 mai: doar 5 trade-uri (3W/2L), NET -$7.66 (cvasi break-even)
- User a cerut backtest comprehensiv pe noi simboluri/strategii inainte de schimbare

### 26.2 Backtest 1: scan volatilitate (TOP 50 simboluri)
Script: `backtesting/scan_volatility.py`
Rezultat: TOP 50 cele mai volatile = 30 CRYPTO + 13 METALS + 5 COMMODITIES + 2 INDICES
CSV: `results/volatility_scan.csv`

### 26.3 Backtest 2: massive (30 simboluri × 3 strategii × 5 TF × 6 perioade)
Script: `backtesting/massive_backtest.py`
Simboluri testate (28):
- Crypto: BTC, ETH, SOL, DSH, AVE
- Metals: XAU, XAG, XPT, XPD, XAGEUR
- Commodities: WTI, BRENT, COFFEE
- Indices: NIKKEI, US2000
- Forex (13): NZDUSD, AUDUSD, AUDJPY, NZDJPY, USDCHF, GBPUSD, USDJPY, CHFJPY, GBPJPY, GBPAUD, AUDCAD, EURUSD, EURGBP, USDCAD, EURJPY

**BUG DETECTAT in primul run:** PnL inflate ~159× pe JPY pairs (formula calcula in JPY, eticheta USD).
**Fix:** folosesc `mt5.symbol_info().trade_tick_value` pentru calcul corect USD. Commit local doar (nu in repo).

CSV: `results/massive_backtest_20260525_1633.csv` (2700 rezultate, valori BUG pe JPY)

### 26.4 Backtest 3: STRESS TEST cu risk constraints (DECISIV)
Script: `backtesting/stress_test_full.py`
Configurare:
- 13 combinatii unice (sym × strategy) — din TOP 20 cu PnL fix aplicat
- 5 TF: M15, M30, H1, H4, D1
- 9 loturi: 0.02-0.10 in pasi de 0.01
- 6 perioade: 30, 60, 90, 120, 150, 180 zile
- **Constraints reale:**
  - Daily loss limit -$53 (zi disabled)
  - BLOW-UP threshold -$570 (10% din $5700)
  - Commission $0.10/0.10 lot proportional

**TOTAL: 585 backtests × 6 perioade = 3510 rezultate** (durata 110 min)
CSV: `results/stress_test_20260525_1939.csv`
Analiza: `backtesting/analyze_stress.py`

### 26.5 REZULTATE STRESS TEST — CONCLUZII

**BLOW-UPS detectate:** 1019 din 3510 (29%!). Multe combinatii crapa chiar la lot 0.02.

**TOP 5 PROFITABILE (90 zile, NO blow-up):**

| # | Simbol | Strategie | TF | Lot | Trades | WR | NET | Pe zi |
|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | **BRENT** | **MeanReversion** | **H1** | **0.08** | 33 | 39% | **+$1519** | **+$17/zi** 🏆 |
| 2 | BRENT | MeanReversion | H1 | 0.07 | 33 | 39% | +$1329 | +$15/zi |
| 3 | BRENT | MeanReversion | H1 | 0.06 | 33 | 39% | +$1140 | +$13/zi |
| 4 | **BRENT** | **Breakout** | **M15** | **0.08** | 76 | 40% | **+$1041** | **+$12/zi** |
| 5 | BRENT | MeanReversion | H1 | 0.05 | 33 | 39% | +$950 | +$11/zi |

**Top 5 LOT MAXIM SIGUR per combinatie:**
- BRENT MeanRev H1: lot 0.08 (0.09 = BLOW-UP)
- BRENT Breakout M15: lot 0.08 (0.09 = BLOW-UP)
- WTI Breakout M15: lot 0.07 (0.08 = BLOW-UP)
- WTI Breakout H1: lot 0.06 (0.07 = BLOW-UP)
- XPTUSD MeanRev M15: lot 0.02 (0.03 = BLOW-UP)

**DESCOPERIRI MAJORE:**
1. ✅ **BRENT** = star absolut, profitabil pe TOATE perioadele 30-180 zile
2. ✅ **WTI** = secondary winner pe Breakout
3. ✅ **MeanReversion + Breakout** >> TrendFollowing pe oil
4. ❌ **CRYPTO ZERO in top 30** (BTC, ETH, SOL nu apar deloc)
5. ❌ **METALE crapa la lot mediu** (XAU MeanRev crapa la 0.02-0.03!)
6. ❌ **TrendFollowing pe oil cracks la lot 0.02-0.03**

### 26.6 STRATEGIE PROPUSA — OIL strategy

**OPTIUNEA A: OIL-ONLY (BRENT + WTI)** — $45/zi estimat
| # | Simbol | Strategie | TF | Lot | Estimare |
|---|---|---|---|---:|---:|
| 1 | BRENT | MeanReversion | H1 | 0.08 | +$17/zi |
| 2 | BRENT | Breakout | M15 | 0.08 | +$12/zi |
| 3 | WTI | Breakout | M15 | 0.07 | +$8.5/zi |
| 4 | WTI | Breakout | H1 | 0.06 | +$7.3/zi |

**OPTIUNEA B: OIL + XPTUSD CONSERVATOR** — $50/zi estimat
- Optiune A + XPTUSD MeanReversion M15 lot 0.02 (+$5/zi)
- Diversificare + €150/luna extra
- Comisioane: 8% din profit XPTUSD (acceptabil)

**Target user:** $50-80/zi (€55-85/zi).
**Estimare realista 50-70% din backtest:** $25-35/zi (sub target).
**Estimare optimista:** $40-50/zi (la marginea target).

### 26.7 DECIZIE PENDING USER

User a plecat la ora 19:55 spre casa. Astept raspuns la:

**1) Setup ales:**
- A) OIL-ONLY (simplu, $45/zi)
- B) OIL + XPTUSD (mai complex, $50/zi)

**2) Durata test:**
- 1 saptamana (5 zile, sample mic)
- 3 saptamani (recomand, sample relevant + kill-switch)
- 1 luna (cel mai sigur statistic)

### 26.8 IMPLEMENTARE NECESARA (cand user aproba)

**Config (config/settings.yaml + strategies.yaml):**
```yaml
symbols: [BRENT.cfd, WTI.cfd, XPTUSD.cfd]  # daca B
active_strategies: [trend_following, breakout, mean_reversion]  # mean_reversion NOU

limits:
  max_open_positions: 5      # era 3
  max_daily_trades: 8        # era 3
  max_positions_per_symbol: 2  # era 1 — BRENT poate avea 2 strategii simultan
```

**Cod (main.py):**
- **MAJOR**: bot-ul actual ruleaza UN strategie/simbol. Pentru oil setup avem nevoie de
  MeanReversion + Breakout pe ACELASI simbol (BRENT). Necesita modificari in `main.py`
  pentru a permite strategie per (simbol, TF) combinatie.
- Lot fix per (simbol, strategie, TF) — adaugare in risk_manager
- Kill-switch DD -$200 (4% din cont)

**Fisiere care vor fi modificate:**
- `config/settings.yaml`
- `config/strategies.yaml`
- `risk/risk_manager.py` (lot per combinatie)
- `main.py` (strategie per simbol)

### 26.9 CHEAT SHEET ACTUALIZAT

```
BOT ACUM:              METAL-ONLY pe VPS (XAU+XAG), commit f07cd48 deployed 18.05
PERFORMANCE 18-25.05:  5 trade-uri, NET -$7.66 (sub target)
STRESS TEST COMPLET:   2026-05-25 19:39, 3510 rezultate in stress_test_20260525_1939.csv

PROPUNERE NOUA:        OIL-ONLY sau OIL+XPTUSD (in dezbatere cu user)
ESTIMARE PROFIT:       $25-50/zi realist (vs target $50-80)

LOT MAX SIGUR confirm:
  BRENT MeanRev H1:    0.08
  BRENT Breakout M15:  0.08
  WTI Breakout M15:    0.07
  WTI Breakout H1:     0.06
  XPTUSD MeanRev M15:  0.02

BLOW-UPS in backtest:  29% combinatii (1019 din 3510)
DECIZIA USER:          Pending (alege A vs B + 1/3/4 saptamani)

NEXT STEP cand revine: User alege strategie → implementez cod + config → deploy VPS
```

### 26.10 Fisiere create azi (2026-05-25)
- `backtesting/scan_volatility.py` — TOP 50 volatile
- `backtesting/massive_backtest.py` — primul scan (cu PnL bug pe JPY)
- `backtesting/stress_test_full.py` — DECISIV cu constraints
- `backtesting/analyze_stress.py` — analiza CSV
- `results/volatility_scan.csv`
- `results/massive_backtest_20260525_1633.csv` (BUG, NU folosi)
- `results/stress_test_20260525_1939.csv` ⭐ (CORECT)

**IMPORTANT pentru sesiune noua Claude:**
Cand revine user, citeste sectiunea 26 INTAI. Decizia OIL-strategy e pending.
Asteapta user sa aleaga (A vs B) si durata test (1 sapt / 3 sapt / 1 luna), apoi implementeaza.

---

**FIN CLAUDE.md.** Daca lipseste ceva important, adauga-l aici si commit imediat.
