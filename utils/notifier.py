"""
Notificari Telegram pentru Trading Bot MT5.
Trimite alerte la tranzactii, erori si raport zilnic.
"""

import asyncio
import os
from datetime import datetime

from utils.logger import setup_logger
from utils.helpers import load_config

logger = setup_logger("notifier")


class TelegramNotifier:
    """Trimite notificari prin Telegram Bot API."""

    def __init__(self):
        config = load_config("settings.yaml")
        tg_config = config.get("notifications", {}).get("telegram", {})

        self.enabled = tg_config.get("enabled", False)
        self.bot_token = tg_config.get("bot_token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = tg_config.get("chat_id", "") or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.notify_trades = tg_config.get("notify_trades", True)
        self.notify_daily = tg_config.get("notify_daily", True)
        self.notify_errors = tg_config.get("notify_errors", True)

        self._bot = None

        if self.enabled and self.bot_token and self.chat_id:
            try:
                from telegram import Bot
                self._bot = Bot(token=self.bot_token)
                logger.info("Telegram notifier activ")
            except ImportError:
                logger.warning("python-telegram-bot nu e instalat. pip install python-telegram-bot")
                self.enabled = False
        elif self.enabled:
            logger.warning("Telegram enabled dar bot_token/chat_id lipsesc. Dezactivat.")
            self.enabled = False

    def _send(self, text: str):
        """Trimite mesaj pe Telegram (sync wrapper peste async)."""
        if not self.enabled or not self._bot:
            return

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                self._bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode="Markdown",
                )
            )
            loop.close()
        except Exception as e:
            logger.error(f"Eroare trimitere Telegram: {e}")

    def notify_bot_started(self):
        """Notificare la pornirea botului."""
        if not self.enabled:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._send(
            f"🟢 *Bot pornit*\n"
            f"📅 {now}\n"
            f"Trading Bot MT5 e activ."
        )

    def notify_bot_stopped(self):
        """Notificare la oprirea botului."""
        if not self.enabled:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._send(
            f"🔴 *Bot oprit*\n"
            f"📅 {now}"
        )

    def notify_trade_opened(
        self,
        symbol: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
        strategy: str = "",
    ):
        """Notificare la deschiderea unei tranzactii."""
        if not self.enabled or not self.notify_trades:
            return

        arrow = "🟢" if direction == "BUY" else "🔴"
        self._send(
            f"{arrow} *{direction} {symbol}*\n"
            f"📊 Strategie: {strategy}\n"
            f"📦 Volume: {volume}\n"
            f"💰 Pret: {price:.5f}\n"
            f"🛑 SL: {sl:.5f}\n"
            f"🎯 TP: {tp:.5f}"
        )

    def notify_trade_closed(
        self,
        symbol: str,
        direction: str,
        pnl: float,
        pips: float = 0,
    ):
        """Notificare la inchiderea unei tranzactii."""
        if not self.enabled or not self.notify_trades:
            return

        icon = "✅" if pnl > 0 else "❌"
        sign = "+" if pnl > 0 else ""
        self._send(
            f"{icon} *Inchis {direction} {symbol}*\n"
            f"💰 PnL: {sign}{pnl:.2f}\n"
            f"📏 Pips: {sign}{pips:.1f}"
        )

    def notify_daily_report(self, stats: dict):
        """Raport zilnic cu statisticile zilei."""
        if not self.enabled or not self.notify_daily:
            return

        pnl = stats.get("pnl", 0)
        trades = stats.get("trades", 0)
        balance = stats.get("balance", 0)
        equity = stats.get("equity", 0)
        open_pos = stats.get("open_positions", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        win_rate = (wins / trades * 100) if trades > 0 else 0

        icon = "📈" if pnl >= 0 else "📉"
        sign = "+" if pnl >= 0 else ""

        self._send(
            f"{icon} *Raport zilnic*\n"
            f"📅 {stats.get('date', datetime.now().strftime('%Y-%m-%d'))}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 PnL: {sign}{pnl:.2f}\n"
            f"📊 Tranzactii: {trades} ({wins}W / {losses}L)\n"
            f"🎯 Win rate: {win_rate:.1f}%\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💵 Balance: {balance:,.2f}\n"
            f"📊 Equity: {equity:,.2f}\n"
            f"📂 Pozitii deschise: {open_pos}"
        )

    def notify_error(self, message: str):
        """Alerta la erori critice."""
        if not self.enabled or not self.notify_errors:
            return

        now = datetime.now().strftime("%H:%M:%S")
        self._send(
            f"⚠️ *EROARE*\n"
            f"🕐 {now}\n"
            f"```\n{message}\n```"
        )

    def notify_max_loss_reached(self, daily_pnl: float, limit_pct: float):
        """Alerta cand pierderea zilnica maxima e atinsa."""
        if not self.enabled or not self.notify_errors:
            return

        self._send(
            f"🚨 *LIMITA PIERDERE ZILNICA ATINSA*\n"
            f"💸 PnL zilnic: {daily_pnl:.2f}\n"
            f"📊 Limita: {limit_pct}%\n"
            f"⏸ Botul a fost oprit automat."
        )
