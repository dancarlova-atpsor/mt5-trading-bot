"""
Strategie bazata pe sentimentul stirilor financiare.
BUY daca sentiment bullish, SELL daca bearish, HOLD daca neutru.
"""

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal
from utils.sentiment_analyzer import SentimentAnalyzer
from utils.logger import setup_logger

logger = setup_logger("news_strategy")


class NewsSentiment(BaseStrategy):
    """Trading pe baza sentimentului din stiri."""

    def __init__(self, params: dict):
        super().__init__("NewsSentiment", params)
        self.sentiment_analyzer = SentimentAnalyzer()
        self.min_articles = params.get("min_articles", 3)
        self.min_score = params.get("min_score", 0.3)
        self._current_symbol: str = ""

    def set_current_symbol(self, symbol: str):
        """Seteaza simbolul curent (apelat din main loop inainte de analyze)."""
        self._current_symbol = symbol

    def calculate_indicators(self, data: pd.DataFrame) -> pd.DataFrame:
        """Nu modifica datele — sentimentul vine din stiri, nu din pret."""
        return data

    def analyze(self, data: pd.DataFrame) -> Signal:
        """Analizeaza sentimentul stirilor pentru simbolul curent."""
        if not self._current_symbol:
            return Signal.HOLD

        sentiment = self.sentiment_analyzer.get_symbol_sentiment(self._current_symbol)

        articles = sentiment.get("articles", 0)
        score = sentiment.get("score", 0.0)
        direction = sentiment.get("direction", "NEUTRAL")

        # Nu tranzactiona daca nu sunt suficiente articole
        if articles < self.min_articles:
            logger.debug(
                f"{self._current_symbol}: doar {articles} articole (minim {self.min_articles}) — HOLD"
            )
            return Signal.HOLD

        if score > self.min_score and direction == "BULLISH":
            logger.info(
                f"SEMNAL BUY (sentiment) — {self._current_symbol}: "
                f"score={score:.2f}, {articles} articole, {direction}"
            )
            return Signal.BUY

        if score < -self.min_score and direction == "BEARISH":
            logger.info(
                f"SEMNAL SELL (sentiment) — {self._current_symbol}: "
                f"score={score:.2f}, {articles} articole, {direction}"
            )
            return Signal.SELL

        return Signal.HOLD
