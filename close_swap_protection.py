"""
Close Swap Protection — inchide pozitiile profitabile inainte de:
  1. Miercuri 22:55 RO (inainte de swap triplu noaptea de miercuri spre joi)
  2. Vineri 22:55 RO  (inainte de weekend)

Inchide doar pozitiile cu PnL > $5 (profit minim peste comisioane).
Pozitiile pe minus raman deschise (sansa recuperare).

Usage (manual):
    python close_swap_protection.py

Usage (automat via Task Scheduler):
    Setezi 2 task-uri saptamanale:
    - Miercuri 22:55: python close_swap_protection.py
    - Vineri 22:55:   python close_swap_protection.py
"""

import os
import sys
from datetime import datetime

import MetaTrader5 as mt5

from utils.logger import setup_logger

logger = setup_logger("close_swap_protection")

# Config
MIN_PROFIT_USD = 5.0  # Inchide doar daca PnL > $5
MAGIC_NUMBER = 123456  # Magic number bot-ului


def close_position(ticket: int, symbol: str, volume: float, position_type: int) -> bool:
    """Inchide o pozitie deschisa via order_send."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Nu pot obtine pretul pentru {symbol}")
        return False

    # Direction inversa pentru close
    order_type = mt5.ORDER_TYPE_SELL if position_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if position_type == mt5.ORDER_TYPE_BUY else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "position": ticket,
        "price": price,
        "deviation": 50,
        "magic": MAGIC_NUMBER,
        "comment": "swap_protection_close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        logger.error(f"Order send return None pentru #{ticket}")
        return False
    if result.retcode == mt5.TRADE_RETCODE_DONE:
        logger.info(f"INCHIS #{ticket} {symbol} {volume}lot @ {price:.5f}")
        return True
    logger.error(f"Order failed #{ticket}: retcode={result.retcode} comment={result.comment}")
    return False


def main():
    now = datetime.now()
    logger.info(f"=== Close Swap Protection START ({now.strftime('%A %Y-%m-%d %H:%M:%S')}) ===")

    weekday = now.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri
    weekday_name = now.strftime("%A")

    if weekday == 2:
        logger.info("MIERCURI seara — protectie inainte swap triplu (Wed→Thu = 3x swap)")
    elif weekday == 4:
        logger.info("VINERI seara — protectie inainte weekend (Fri→Mon = 3 zile swap)")
    else:
        logger.info(f"Azi e {weekday_name}, scriptul ruleaza doar miercuri sau vineri. Exit.")
        return

    # Conectare MT5 — folosim env vars sau path default VPS
    mt5_path = os.environ.get("MT5_TERMINAL_PATH", r"C:\Users\Administrator\Desktop\EarnBrokerMT5\terminal64.exe")

    if not mt5.initialize(path=mt5_path, timeout=60000):
        logger.error(f"Init MT5 esuat: {mt5.last_error()}")
        # Fallback fara path
        if not mt5.initialize(timeout=60000):
            logger.error(f"Init MT5 fallback esuat: {mt5.last_error()}")
            sys.exit(1)

    info = mt5.account_info()
    if info is None:
        logger.error("Nu pot obtine account info")
        mt5.shutdown()
        sys.exit(1)

    logger.info(f"Conectat: cont {info.login} | Balance {info.balance:.2f} | Equity {info.equity:.2f}")

    # Itereaza pozitii deschise
    positions = mt5.positions_get()
    if not positions:
        logger.info("Nu exista pozitii deschise. Exit.")
        mt5.shutdown()
        return

    closed_count = 0
    skipped_count = 0
    total_realized_profit = 0.0

    logger.info(f"Verific {len(positions)} pozitii deschise...")
    for p in positions:
        # Filter: doar pozitiile bot-ului (magic 123456)
        if p.magic != MAGIC_NUMBER:
            continue

        # PnL = profit + swap + commission (commission din IN deal nu apare aici, doar floating)
        pnl = p.profit + p.swap

        if pnl > MIN_PROFIT_USD:
            logger.info(f"INCHIDERE #{p.ticket} {p.symbol} | PnL +${pnl:.2f} > ${MIN_PROFIT_USD}")
            if close_position(p.ticket, p.symbol, p.volume, p.type):
                closed_count += 1
                total_realized_profit += pnl
        else:
            logger.info(f"SKIP #{p.ticket} {p.symbol} | PnL ${pnl:.2f} (sub pragul ${MIN_PROFIT_USD} sau pierdere)")
            skipped_count += 1

    logger.info(f"=== REZUMAT ===")
    logger.info(f"Inchise: {closed_count} pozitii | Profit realizat: ${total_realized_profit:.2f}")
    logger.info(f"Skip-uite: {skipped_count} pozitii (sub prag sau pierdere)")
    logger.info(f"=== Close Swap Protection END ===")

    mt5.shutdown()


if __name__ == "__main__":
    main()
