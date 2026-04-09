//+------------------------------------------------------------------+
//|                                               ExecutionEA.mq5     |
//|                    Trading Bot MT5 — Execution Expert Advisor      |
//|                                                                    |
//|  EA complet cu ROL DUAL:                                           |
//|  1. Primeste si executa semnale de la Python (signal.json)         |
//|  2. Logica independenta EMA crossover (backup cand Python e off)   |
//|  3. Trailing stop nativ                                            |
//|  4. Scrie status inapoi catre Python (status.json)                 |
//+------------------------------------------------------------------+
#property copyright "Trading Bot MT5"
#property version   "1.00"
#property strict

//--- Moduri de operare
enum ENUM_EA_MODE
{
   MODE_SIGNAL_ONLY  = 0,    // Doar semnale de la Python
   MODE_INDEPENDENT  = 1,    // Doar logica proprie (EMA cross)
   MODE_BOTH         = 2     // Ambele — Python + fallback independent
};

//--- Input parameters: General
input ENUM_EA_MODE InpMode           = MODE_BOTH;        // Mod de operare
input double       InpLotSize        = 0.01;             // Lot size default
input int          InpMagicNumber    = 123456;           // Magic number
input int          InpSlippage       = 10;               // Slippage maxim (points)
input string       InpSignalFile     = "python_signal.json";  // Fisier semnal
input string       InpStatusFile     = "ea_status.json";      // Fisier status

//--- Input parameters: Signal
input int          InpSignalTimeout  = 30;               // Timeout semnal Python (secunde)
input int          InpFallbackMinutes= 5;                // Dupa X min fara semnal, activeaza independent

//--- Input parameters: Independent Strategy (EMA Cross)
input int          InpEmaFast        = 20;               // EMA rapida
input int          InpEmaSlow        = 50;               // EMA lenta
input double       InpIndepSL_Pips   = 50;               // SL in pips (independent)
input double       InpIndepTP_Pips   = 100;              // TP in pips (independent)
input double       InpIndepLotSize   = 0.01;             // Lot size (independent)

//--- Input parameters: Trailing Stop
input bool         InpUseTrailing    = true;             // Activeaza trailing stop
input int          InpTrailActivation= 30;               // Activare trailing (pips)
input int          InpTrailDistance  = 15;               // Distanta trailing (pips)

//--- Input parameters: Limite
input int          InpMaxPositions   = 3;                // Max pozitii deschise simultan

//--- Variabile globale
datetime lastSignalTime     = 0;       // Timestamp ultimul semnal procesat
datetime lastPythonActivity = 0;       // Ultima activitate de la Python
int      emaFastHandle      = INVALID_HANDLE;
int      emaSlowHandle      = INVALID_HANDLE;
double   emaFastBuf[];
double   emaSlowBuf[];

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
{
   // Creaza handlere EMA pentru logica independenta
   if(InpMode == MODE_INDEPENDENT || InpMode == MODE_BOTH)
   {
      emaFastHandle = iMA(_Symbol, PERIOD_H1, InpEmaFast, 0, MODE_EMA, PRICE_CLOSE);
      emaSlowHandle = iMA(_Symbol, PERIOD_H1, InpEmaSlow, 0, MODE_EMA, PRICE_CLOSE);

      if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE)
      {
         Print("Eroare la crearea indicatorilor EMA!");
         return(INIT_FAILED);
      }

      ArraySetAsSeries(emaFastBuf, true);
      ArraySetAsSeries(emaSlowBuf, true);
   }

   lastPythonActivity = TimeCurrent();

   string modeStr = "";
   if(InpMode == MODE_SIGNAL_ONLY) modeStr = "SIGNAL_ONLY";
   else if(InpMode == MODE_INDEPENDENT) modeStr = "INDEPENDENT";
   else modeStr = "BOTH";

   Print("ExecutionEA initializat | Mod=", modeStr,
         " | Magic=", InpMagicNumber,
         " | EMA ", InpEmaFast, "/", InpEmaSlow,
         " | Trailing=", InpUseTrailing);

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(emaFastHandle != INVALID_HANDLE) IndicatorRelease(emaFastHandle);
   if(emaSlowHandle != INVALID_HANDLE) IndicatorRelease(emaSlowHandle);
   Print("ExecutionEA oprit. Motiv: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. Procesare semnale Python
   if(InpMode == MODE_SIGNAL_ONLY || InpMode == MODE_BOTH)
   {
      bool signalProcessed = ProcessSignalFile();
      if(signalProcessed)
         lastPythonActivity = TimeCurrent();
   }

   // 2. Logica independenta (EMA cross)
   if(InpMode == MODE_INDEPENDENT || InpMode == MODE_BOTH)
   {
      bool shouldRunIndependent = true;

      // In mod BOTH, ruleaza independent doar daca Python e offline
      if(InpMode == MODE_BOTH)
      {
         long minutesSincePython = (long)(TimeCurrent() - lastPythonActivity) / 60;
         shouldRunIndependent = (minutesSincePython >= InpFallbackMinutes);

         if(shouldRunIndependent && minutesSincePython == InpFallbackMinutes)
         {
            Print("Python inactiv de ", InpFallbackMinutes,
                  " minute — activez logica independenta");
         }
      }

      if(shouldRunIndependent)
         ProcessIndependentStrategy();
   }

   // 3. Trailing stop pe pozitiile deschise
   if(InpUseTrailing)
      ManageTrailingStop();
}

//+------------------------------------------------------------------+
//| Procesare semnale din signal.json                                  |
//+------------------------------------------------------------------+
bool ProcessSignalFile()
{
   if(!FileIsExist(InpSignalFile))
      return false;

   int handle = FileOpen(InpSignalFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return false;

   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle);
   FileClose(handle);

   if(StringLen(content) == 0)
      return false;

   // Parseaza JSON
   long   timestamp = GetJsonLong(content, "timestamp");
   string symbol    = GetJsonString(content, "symbol");
   string action    = GetJsonString(content, "action");
   double volume    = GetJsonDouble(content, "volume");
   double sl        = GetJsonDouble(content, "sl");
   double tp        = GetJsonDouble(content, "tp");
   int    magic     = (int)GetJsonLong(content, "magic");
   string comment   = GetJsonString(content, "comment");

   if(timestamp <= 0 || StringLen(symbol) == 0 || StringLen(action) == 0)
      return false;

   // Timeout check
   long currentTime = (long)TimeCurrent();
   if(currentTime - timestamp > InpSignalTimeout)
      return false;

   // Deja procesat?
   if(timestamp <= (long)lastSignalTime)
      return false;

   // Verificare numar pozitii
   if(CountMyPositions() >= InpMaxPositions)
   {
      Print("Max pozitii atins (", InpMaxPositions, ") — semnal ignorat");
      WriteStatusFile(0, symbol, action, 0, volume, "REJECTED", "Max positions reached");
      lastSignalTime = (datetime)timestamp;
      return true;
   }

   // Defaults
   if(magic == 0) magic = InpMagicNumber;
   if(volume <= 0) volume = InpLotSize;

   // Executa
   ulong ticket = 0;
   double price = 0;
   string errorMsg = "";
   bool success = false;

   if(action == "BUY")
      success = ExecuteMarketOrder(symbol, ORDER_TYPE_BUY, volume, sl, tp, magic, comment, ticket, price, errorMsg);
   else if(action == "SELL")
      success = ExecuteMarketOrder(symbol, ORDER_TYPE_SELL, volume, sl, tp, magic, comment, ticket, price, errorMsg);

   lastSignalTime = (datetime)timestamp;

   WriteStatusFile(ticket, symbol, action, price, volume,
                   success ? "FILLED" : "ERROR", errorMsg);

   if(success)
      Print("[SIGNAL] ", action, " ", volume, " ", symbol, " @ ", price, " | Ticket=", ticket);
   else
      Print("[SIGNAL] Eroare: ", action, " ", symbol, " | ", errorMsg);

   return true;
}

//+------------------------------------------------------------------+
//| Logica independenta — EMA Crossover                                |
//+------------------------------------------------------------------+
void ProcessIndependentStrategy()
{
   // Verificare numar pozitii
   if(CountMyPositions() >= InpMaxPositions)
      return;

   // Verificare: un singur semnal per bara (evita semnale repetate)
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, PERIOD_H1, 0);
   if(currentBarTime == lastBarTime)
      return;

   // Copiaza valorile EMA
   if(CopyBuffer(emaFastHandle, 0, 0, 3, emaFastBuf) < 3) return;
   if(CopyBuffer(emaSlowHandle, 0, 0, 3, emaSlowBuf) < 3) return;

   // EMA Crossover pe barele anterioare (1 si 2 — bara 0 nu e inchisa)
   double fastPrev = emaFastBuf[2];
   double slowPrev = emaSlowBuf[2];
   double fastCurr = emaFastBuf[1];
   double slowCurr = emaSlowBuf[1];

   // Verifica daca avem deja pozitie pe acest simbol
   if(HasPositionOnSymbol(_Symbol))
      return;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   // BUY: EMA fast crosseaza peste EMA slow
   if(fastPrev < slowPrev && fastCurr > slowCurr)
   {
      double sl = ask - InpIndepSL_Pips * point * 10;
      double tp = ask + InpIndepTP_Pips * point * 10;

      ulong ticket = 0;
      double price = 0;
      string errorMsg = "";

      if(ExecuteMarketOrder(_Symbol, ORDER_TYPE_BUY, InpIndepLotSize,
                            sl, tp, InpMagicNumber, "EMA_Cross_BUY",
                            ticket, price, errorMsg))
      {
         Print("[INDEP] BUY ", _Symbol, " @ ", price,
               " | EMA_fast=", fastCurr, " EMA_slow=", slowCurr);
         lastBarTime = currentBarTime;
      }
   }

   // SELL: EMA fast crosseaza sub EMA slow
   if(fastPrev > slowPrev && fastCurr < slowCurr)
   {
      double sl = bid + InpIndepSL_Pips * point * 10;
      double tp = bid - InpIndepTP_Pips * point * 10;

      ulong ticket = 0;
      double price = 0;
      string errorMsg = "";

      if(ExecuteMarketOrder(_Symbol, ORDER_TYPE_SELL, InpIndepLotSize,
                            sl, tp, InpMagicNumber, "EMA_Cross_SELL",
                            ticket, price, errorMsg))
      {
         Print("[INDEP] SELL ", _Symbol, " @ ", price,
               " | EMA_fast=", fastCurr, " EMA_slow=", slowCurr);
         lastBarTime = currentBarTime;
      }
   }
}

//+------------------------------------------------------------------+
//| Trailing Stop Management                                           |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double activationDist = InpTrailActivation * point * 10;
   double trailDist      = InpTrailDistance * point * 10;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      string posSymbol = PositionGetString(POSITION_SYMBOL);
      long   posType   = PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);

      double bid = SymbolInfoDouble(posSymbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(posSymbol, SYMBOL_ASK);

      if(posType == POSITION_TYPE_BUY)
      {
         double profit = bid - openPrice;
         if(profit >= activationDist)
         {
            double newSL = bid - trailDist;
            // Rotunjeste la digits
            int digits = (int)SymbolInfoInteger(posSymbol, SYMBOL_DIGITS);
            newSL = NormalizeDouble(newSL, digits);

            if(newSL > currentSL)
            {
               ModifyPosition(ticket, newSL, currentTP);
            }
         }
      }
      else if(posType == POSITION_TYPE_SELL)
      {
         double profit = openPrice - ask;
         if(profit >= activationDist)
         {
            double newSL = ask + trailDist;
            int digits = (int)SymbolInfoInteger(posSymbol, SYMBOL_DIGITS);
            newSL = NormalizeDouble(newSL, digits);

            if(currentSL == 0 || newSL < currentSL)
            {
               ModifyPosition(ticket, newSL, currentTP);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Modifica SL/TP pe o pozitie                                        |
//+------------------------------------------------------------------+
bool ModifyPosition(ulong ticket, double sl, double tp)
{
   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action   = TRADE_ACTION_SLTP;
   request.position = ticket;
   request.sl       = sl;
   request.tp       = tp;

   if(!OrderSend(request, result))
   {
      Print("Modify esuat #", ticket, ": ", result.retcode, " ", result.comment);
      return false;
   }

   if(result.retcode != TRADE_RETCODE_DONE)
   {
      Print("Modify retcode #", ticket, ": ", result.retcode);
      return false;
   }

   Print("[TRAIL] #", ticket, " SL=", sl);
   return true;
}

//+------------------------------------------------------------------+
//| Executa ordine market                                              |
//+------------------------------------------------------------------+
bool ExecuteMarketOrder(string symbol, ENUM_ORDER_TYPE orderType,
                        double volume, double sl, double tp,
                        int magic, string comment,
                        ulong &ticket, double &price, string &errorMsg)
{
   if(!SymbolSelect(symbol, true))
   {
      errorMsg = "Simbol indisponibil: " + symbol;
      return false;
   }

   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   price = (orderType == ORDER_TYPE_BUY) ? ask : bid;

   // Normalizeaza SL/TP
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);

   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action       = TRADE_ACTION_DEAL;
   request.symbol       = symbol;
   request.volume       = volume;
   request.type         = orderType;
   request.price        = price;
   request.sl           = sl;
   request.tp           = tp;
   request.magic        = magic;
   request.comment      = comment;
   request.deviation    = InpSlippage;
   request.type_filling = ORDER_FILLING_IOC;

   if(!OrderSend(request, result))
   {
      errorMsg = "OrderSend esuat: " + IntegerToString(result.retcode) + " " + result.comment;
      return false;
   }

   if(result.retcode != TRADE_RETCODE_DONE)
   {
      errorMsg = "Retcode: " + IntegerToString(result.retcode) + " " + result.comment;
      return false;
   }

   ticket = result.order;
   price  = result.price;
   return true;
}

//+------------------------------------------------------------------+
//| Numara pozitiile deschise cu magic number-ul nostru                 |
//+------------------------------------------------------------------+
int CountMyPositions()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Verifica daca avem pozitie pe un simbol                            |
//+------------------------------------------------------------------+
bool HasPositionOnSymbol(string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber &&
         PositionGetString(POSITION_SYMBOL) == symbol)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Scrie ea_status.json                                               |
//+------------------------------------------------------------------+
void WriteStatusFile(ulong ticket, string symbol, string action,
                     double price, double volume,
                     string status, string error)
{
   int handle = FileOpen(InpStatusFile, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("Nu pot scrie status file!");
      return;
   }

   string json = "{\n";
   json += "  \"timestamp\": " + IntegerToString((long)TimeCurrent()) + ",\n";
   json += "  \"ticket\": " + IntegerToString((long)ticket) + ",\n";
   json += "  \"symbol\": \"" + symbol + "\",\n";
   json += "  \"action\": \"" + action + "\",\n";
   json += "  \"price\": " + DoubleToString(price, 5) + ",\n";
   json += "  \"volume\": " + DoubleToString(volume, 2) + ",\n";
   json += "  \"status\": \"" + status + "\",\n";
   json += "  \"error\": \"" + error + "\"\n";
   json += "}";

   FileWriteString(handle, json);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| JSON Parsing Helpers                                               |
//+------------------------------------------------------------------+
string GetJsonString(const string &json, const string key)
{
   string searchKey = "\"" + key + "\"";
   int pos = StringFind(json, searchKey);
   if(pos == -1) return "";

   int colonPos = StringFind(json, ":", pos + StringLen(searchKey));
   if(colonPos == -1) return "";

   int startQuote = StringFind(json, "\"", colonPos + 1);
   if(startQuote == -1) return "";

   int endQuote = StringFind(json, "\"", startQuote + 1);
   if(endQuote == -1) return "";

   return StringSubstr(json, startQuote + 1, endQuote - startQuote - 1);
}

double GetJsonDouble(const string &json, const string key)
{
   string searchKey = "\"" + key + "\"";
   int pos = StringFind(json, searchKey);
   if(pos == -1) return 0;

   int colonPos = StringFind(json, ":", pos + StringLen(searchKey));
   if(colonPos == -1) return 0;

   string numStr = "";
   int i = colonPos + 1;
   int len = StringLen(json);
   while(i < len && (StringGetCharacter(json, i) == ' ' || StringGetCharacter(json, i) == '\t'))
      i++;
   while(i < len)
   {
      ushort ch = StringGetCharacter(json, i);
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-')
         numStr += CharToString((uchar)ch);
      else
         break;
      i++;
   }
   return StringToDouble(numStr);
}

long GetJsonLong(const string &json, const string key)
{
   string searchKey = "\"" + key + "\"";
   int pos = StringFind(json, searchKey);
   if(pos == -1) return 0;

   int colonPos = StringFind(json, ":", pos + StringLen(searchKey));
   if(colonPos == -1) return 0;

   string numStr = "";
   int i = colonPos + 1;
   int len = StringLen(json);
   while(i < len && (StringGetCharacter(json, i) == ' ' || StringGetCharacter(json, i) == '\t'))
      i++;
   while(i < len)
   {
      ushort ch = StringGetCharacter(json, i);
      if((ch >= '0' && ch <= '9') || ch == '-')
         numStr += CharToString((uchar)ch);
      else
         break;
      i++;
   }
   return StringToInteger(numStr);
}
//+------------------------------------------------------------------+
