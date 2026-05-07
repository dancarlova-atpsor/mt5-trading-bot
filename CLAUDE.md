# CLAUDE.md — Context complet pentru sesiuni Claude Code

> **Fisier critic.** Citeste-l in INTREGIME inainte sa propui modificari.
> Userul (Dan, SC Luxuria Trans & Travel SRL) opereaza un bot de trading MT5 pe cont demo EarnBroker. Foloseste Romanian / Romglish in conversatii.

Ultima actualizare: **2026-05-07** (deploy strategie HIBRID + STOCKS pe VPS).

---

## 1. Statusul curent al proiectului

**Bot ruleaza pe VPS** (MyForexVPS Silver Windows 2019, $14.99/luna), conectat la **MetaTrader 5 EarnBroker custom build 5833**.

**Strategie activa:** HIBRID FOREX/METALS + STOCKS US (deployed 2026-05-07 ~15:04 RO).

**Cont MT5 demo:** `9047479` @ EarnBroker-Sharp ECN, balance ~$98,624 USD (cont mare demo), magic number `123456`, leverage 1:100, simulated_balance pentru risc = `5700 USD` (echivalent 5300 EUR target real).

**Target zilnic estimat:** $48-69/zi (€55-85/zi). Backtest 90 zile: forex/metals +$3000-4500, stocks +$1721.

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

**FIN CLAUDE.md.** Daca lipseste ceva important, adauga-l aici si commit imediat.
