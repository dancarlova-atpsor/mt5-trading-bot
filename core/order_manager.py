"""
Gestionarea ordinelor pe MetaTrader 5.
Deschidere, inchidere, modificare ordine (market, limit, stop).
"""

from dataclasses import dataclass
from enum import Enum

import MetaTrader5 as mt5

from utils.logger import setup_logger
from utils.helpers import load_config

logger = setup_logger("order_manager")


class OrderType(Enum):
    MARKET_BUY = mt5.ORDER_TYPE_BUY
    MARKET_SELL = mt5.ORDER_TYPE_SELL
    BUY_LIMIT = mt5.ORDER_TYPE_BUY_LIMIT
    SELL_LIMIT = mt5.ORDER_TYPE_SELL_LIMIT
    BUY_STOP = mt5.ORDER_TYPE_BUY_STOP
    SELL_STOP = mt5.ORDER_TYPE_SELL_STOP


@dataclass
class OrderResult:
    success: bool
    order_id: int | None = None
    message: str = ""
    retcode: int = 0


class OrderManager:
    """CRUD pentru ordine pe MT5."""

    def __init__(self, config_path: str = "settings.yaml"):
        self.config = load_config(config_path)
        self.magic = self.config["account"].get("magic_number", 123456)

    def open_market_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "python_bot",
    ) -> OrderResult:
        """Deschide o ordine market (BUY sau SELL)."""
        # Verifica simbol
        info = mt5.symbol_info(symbol)
        if info is None:
            return OrderResult(False, message=f"Simbol invalid: {symbol}")
        if not info.visible:
            mt5.symbol_select(symbol, True)

        # Pret
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return OrderResult(False, message=f"Nu pot obtine pretul pentru {symbol}")

        price = tick.ask if order_type == OrderType.MARKET_BUY else tick.bid

        # Normalizeaza SL/TP la tick_size-ul simbolului (evita Invalid stops)
        digits = info.digits
        tick_size = info.trade_tick_size
        if tick_size > 0:
            sl = round(round(float(sl) / tick_size) * tick_size, digits) if sl else 0.0
            tp = round(round(float(tp) / tick_size) * tick_size, digits) if tp else 0.0
        else:
            sl = round(float(sl), digits) if sl else 0.0
            tp = round(float(tp), digits) if tp else 0.0
        volume = round(float(volume), 2)

        # Asigura distanta minima SL/TP (spread * 3 ca safety margin)
        min_distance = info.point * max(info.spread * 3, 50)
        if sl > 0:
            if order_type == OrderType.MARKET_BUY and price - sl < min_distance:
                sl = round(price - min_distance, digits)
            elif order_type == OrderType.MARKET_SELL and sl - price < min_distance:
                sl = round(price + min_distance, digits)
        if tp > 0:
            if order_type == OrderType.MARKET_BUY and tp - price < min_distance:
                tp = round(price + min_distance, digits)
            elif order_type == OrderType.MARKET_SELL and price - tp < min_distance:
                tp = round(price - min_distance, digits)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type.value,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=f"order_send returnat None: {mt5.last_error()}")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(
                f"Ordine esuata {symbol} {order_type.name}: "
                f"retcode={result.retcode}, comment={result.comment}"
            )
            return OrderResult(False, retcode=result.retcode, message=result.comment)

        logger.info(
            f"Ordine executata: {order_type.name} {volume} {symbol} @ {price} "
            f"| SL={sl} TP={tp} | ID={result.order}"
        )
        return OrderResult(True, order_id=result.order, message="OK", retcode=result.retcode)

    def open_pending_order(
        self,
        symbol: str,
        order_type: OrderType,
        volume: float,
        price: float,
        sl: float = 0.0,
        tp: float = 0.0,
        comment: str = "python_bot",
    ) -> OrderResult:
        """Deschide o ordine pending (limit sau stop)."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return OrderResult(False, message=f"Simbol invalid: {symbol}")
        if not info.visible:
            mt5.symbol_select(symbol, True)

        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": order_type.value,
            "price": price,
            "sl": sl,
            "tp": tp,
            "magic": self.magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=f"order_send returnat None: {mt5.last_error()}")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Pending esuata: retcode={result.retcode}, comment={result.comment}")
            return OrderResult(False, retcode=result.retcode, message=result.comment)

        logger.info(
            f"Pending: {order_type.name} {volume} {symbol} @ {price} | ID={result.order}"
        )
        return OrderResult(True, order_id=result.order, retcode=result.retcode)

    def close_position(self, ticket: int, comment: str = "close_bot") -> OrderResult:
        """Inchide o pozitie deschisa dupa ticket."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return OrderResult(False, message=f"Pozitia {ticket} nu exista")

        pos = position[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return OrderResult(False, message=f"Nu pot obtine pretul pentru {pos.symbol}")

        price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "magic": self.magic,
            "comment": comment,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=f"Close esuat: {mt5.last_error()}")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Close esuat #{ticket}: retcode={result.retcode}")
            return OrderResult(False, retcode=result.retcode, message=result.comment)

        logger.info(f"Pozitia #{ticket} inchisa @ {price}")
        return OrderResult(True, order_id=result.order, retcode=result.retcode)

    def modify_position(
        self, ticket: int, sl: float = None, tp: float = None
    ) -> OrderResult:
        """Modifica SL/TP pe o pozitie existenta."""
        position = mt5.positions_get(ticket=ticket)
        if not position:
            return OrderResult(False, message=f"Pozitia {ticket} nu exista")

        pos = position[0]
        new_sl = sl if sl is not None else pos.sl
        new_tp = tp if tp is not None else pos.tp

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": ticket,
            "sl": new_sl,
            "tp": new_tp,
            "magic": self.magic,
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=f"Modify esuat: {mt5.last_error()}")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"Modify esuat #{ticket}: retcode={result.retcode}")
            return OrderResult(False, retcode=result.retcode, message=result.comment)

        logger.info(f"Pozitia #{ticket} modificata: SL={new_sl}, TP={new_tp}")
        return OrderResult(True, order_id=ticket, retcode=result.retcode)

    def cancel_pending(self, order_id: int) -> OrderResult:
        """Anuleaza o ordine pending."""
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": order_id,
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=f"Cancel esuat: {mt5.last_error()}")

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, retcode=result.retcode, message=result.comment)

        logger.info(f"Ordine pending #{order_id} anulata")
        return OrderResult(True, order_id=order_id, retcode=result.retcode)

    def get_open_positions(self, symbol: str = None) -> list[dict]:
        """Returneaza pozitiile deschise (optional filtrate pe simbol)."""
        if symbol:
            positions = mt5.positions_get(symbol=symbol)
        else:
            positions = mt5.positions_get()

        if positions is None:
            return []

        return [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                "volume": p.volume,
                "open_price": p.price_open,
                "current_price": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "magic": p.magic,
                "comment": p.comment,
            }
            for p in positions
            if p.magic == self.magic  # Doar ordinele botului
        ]

    def get_pending_orders(self, symbol: str = None) -> list[dict]:
        """Returneaza ordinele pending."""
        if symbol:
            orders = mt5.orders_get(symbol=symbol)
        else:
            orders = mt5.orders_get()

        if orders is None:
            return []

        return [
            {
                "ticket": o.ticket,
                "symbol": o.symbol,
                "type": OrderType(o.type).name,
                "volume": o.volume_current,
                "price": o.price_open,
                "sl": o.sl,
                "tp": o.tp,
                "magic": o.magic,
            }
            for o in orders
            if o.magic == self.magic
        ]
