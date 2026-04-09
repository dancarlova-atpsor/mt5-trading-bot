//+------------------------------------------------------------------+
//|                                             SignalReceiver.mq5    |
//|                         Trading Bot MT5 — Signal Receiver EA      |
//|                                                                    |
//|  EA simplu: citeste semnale din python_signal.json,                |
//|  executa ordine market, scrie status in ea_status.json.            |
//+------------------------------------------------------------------+
#property copyright "Trading Bot MT5"
#property version   "1.00"
#property strict

//--- Input parameters
input double   InpLotSize       = 0.01;      // Lot size default (suprascris de semnal)
input int      InpMagicNumber   = 123456;     // Magic number
input int      InpSignalTimeout = 30;         // Timeout semnal (secunde)
input int      InpSlippage      = 10;         // Slippage maxim (points)
input string   InpSignalFile    = "python_signal.json";  // Fisier semnal
input string   InpStatusFile    = "ea_status.json";      // Fisier status

//--- Variabile globale
datetime lastSignalTime = 0;    // Timestamp ultimul semnal procesat

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("SignalReceiver EA initializat | Magic=", InpMagicNumber,
         " | Timeout=", InpSignalTimeout, "s");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                            |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Print("SignalReceiver EA oprit. Motiv: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   // Citeste si proceseaza semnalul de la Python
   ProcessSignalFile();
}

//+------------------------------------------------------------------+
//| Citeste signal.json si executa daca e valid                       |
//+------------------------------------------------------------------+
void ProcessSignalFile()
{
   // Verifica daca fisierul exista
   if(!FileIsExist(InpSignalFile))
      return;

   // Deschide fisierul
   int handle = FileOpen(InpSignalFile, FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
      return;

   // Citeste tot continutul
   string content = "";
   while(!FileIsEnding(handle))
   {
      content += FileReadString(handle);
   }
   FileClose(handle);

   if(StringLen(content) == 0)
      return;

   // Parseaza JSON manual (MQL5 nu are JSON nativ)
   long timestamp = GetJsonLong(content, "timestamp");
   string symbol  = GetJsonString(content, "symbol");
   string action  = GetJsonString(content, "action");
   double volume  = GetJsonDouble(content, "volume");
   double sl      = GetJsonDouble(content, "sl");
   double tp      = GetJsonDouble(content, "tp");
   int    magic   = (int)GetJsonLong(content, "magic");
   string comment = GetJsonString(content, "comment");

   // Validari
   if(timestamp <= 0 || StringLen(symbol) == 0 || StringLen(action) == 0)
      return;

   // Verifica timeout — ignora semnale vechi
   long currentTime = (long)TimeCurrent();
   if(currentTime - timestamp > InpSignalTimeout)
      return;

   // Verifica daca am procesat deja acest semnal
   if(timestamp <= (long)lastSignalTime)
      return;

   // Seteaza magic din semnal sau default
   if(magic == 0)
      magic = InpMagicNumber;

   // Seteaza volume din semnal sau default
   if(volume <= 0)
      volume = InpLotSize;

   // Executa ordinea
   bool success = false;
   ulong ticket = 0;
   double price = 0;
   string errorMsg = "";

   if(action == "BUY")
   {
      success = ExecuteMarketOrder(symbol, ORDER_TYPE_BUY, volume, sl, tp,
                                    magic, comment, ticket, price, errorMsg);
   }
   else if(action == "SELL")
   {
      success = ExecuteMarketOrder(symbol, ORDER_TYPE_SELL, volume, sl, tp,
                                    magic, comment, ticket, price, errorMsg);
   }
   else
   {
      Print("Actiune necunoscuta: ", action);
      return;
   }

   // Marcheaza semnalul ca procesat
   lastSignalTime = (datetime)timestamp;

   // Scrie status
   WriteStatusFile(ticket, symbol, action, price, volume,
                   success ? "FILLED" : "ERROR", errorMsg);

   if(success)
   {
      Print("Ordine executata: ", action, " ", volume, " ", symbol,
            " @ ", price, " | Ticket=", ticket);
   }
   else
   {
      Print("Ordine esuata: ", action, " ", symbol, " | Eroare: ", errorMsg);
   }
}

//+------------------------------------------------------------------+
//| Executa ordine market                                              |
//+------------------------------------------------------------------+
bool ExecuteMarketOrder(string symbol, ENUM_ORDER_TYPE orderType,
                        double volume, double sl, double tp,
                        int magic, string comment,
                        ulong &ticket, double &price, string &errorMsg)
{
   // Verifica daca simbolul e disponibil
   if(!SymbolSelect(symbol, true))
   {
      errorMsg = "Simbol indisponibil: " + symbol;
      return false;
   }

   // Obtine pretul
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   price = (orderType == ORDER_TYPE_BUY) ? ask : bid;

   // Pregateste request-ul
   MqlTradeRequest request = {};
   MqlTradeResult  result  = {};

   request.action    = TRADE_ACTION_DEAL;
   request.symbol    = symbol;
   request.volume    = volume;
   request.type      = orderType;
   request.price     = price;
   request.sl        = sl;
   request.tp        = tp;
   request.magic     = magic;
   request.comment   = comment;
   request.deviation = InpSlippage;
   request.type_filling = ORDER_FILLING_IOC;

   // Trimite ordinea
   if(!OrderSend(request, result))
   {
      errorMsg = "OrderSend esuat: retcode=" + IntegerToString(result.retcode) +
                 " " + result.comment;
      return false;
   }

   if(result.retcode != TRADE_RETCODE_DONE)
   {
      errorMsg = "Retcode: " + IntegerToString(result.retcode) +
                 " " + result.comment;
      return false;
   }

   ticket = result.order;
   price  = result.price;
   return true;
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
//| Functii helper JSON parsing (basic)                                |
//+------------------------------------------------------------------+
string GetJsonString(const string &json, const string key)
{
   string searchKey = "\"" + key + "\"";
   int pos = StringFind(json, searchKey);
   if(pos == -1) return "";

   // Gaseste : dupa cheie
   int colonPos = StringFind(json, ":", pos + StringLen(searchKey));
   if(colonPos == -1) return "";

   // Gaseste primul "
   int startQuote = StringFind(json, "\"", colonPos + 1);
   if(startQuote == -1) return "";

   // Gaseste al doilea "
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

   // Extrage valoarea numerica
   string numStr = "";
   int i = colonPos + 1;
   int len = StringLen(json);
   // Skip spatii
   while(i < len && (StringGetCharacter(json, i) == ' ' || StringGetCharacter(json, i) == '\t'))
      i++;
   // Citeste numarul
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
