"""
Analiza sentiment din stiri financiare.
Foloseste NewsAPI.org pentru articole si keyword scoring pentru sentiment.
"""

import os
import time
from datetime import datetime, timedelta

import requests

from utils.logger import setup_logger
from utils.helpers import load_config

logger = setup_logger("sentiment")

# Cuvinte cheie pentru analiza sentiment
BULLISH_KEYWORDS = [
    "rally", "surge", "gain", "rise", "bullish", "higher", "beat", "exceed",
    "strong", "recovery", "rebound", "breakout", "soar", "jump", "boom",
    "optimism", "growth", "positive", "buy", "upgrade", "hawkish",
    "record high", "all-time high", "outperform",
]

BEARISH_KEYWORDS = [
    "crash", "fall", "drop", "decline", "bearish", "lower", "miss", "weak",
    "recession", "selloff", "sell-off", "plunge", "dump", "slump",
    "pessimism", "contraction", "negative", "sell", "downgrade", "dovish",
    "record low", "underperform", "fear", "crisis", "collapse",
]

# Mapare simbol -> query de cautare stiri
SYMBOL_QUERIES = {
    "EURUSD": "EUR USD forex euro dollar",
    "GBPUSD": "GBP USD forex pound dollar",
    "XAUUSD": "gold price XAU",
    "XAGUSD": "silver price XAG",
    "USDJPY": "USD JPY forex yen",
    "US500": "S&P 500 stock market",
    "US30": "Dow Jones stock market",
}


class SentimentAnalyzer:
    """Analizeaza sentimentul stirilor financiare."""

    def __init__(self):
        config = load_config("settings.yaml")
        sent_config = config.get("sentiment", {})

        self.enabled = sent_config.get("enabled", False)
        self.api_key = sent_config.get("newsapi_key", "") or os.environ.get("NEWSAPI_KEY", "")
        self.min_score = sent_config.get("min_score_threshold", 0.3)
        self.check_interval = sent_config.get("check_interval_minutes", 60)

        # Cache per simbol
        self._cache: dict[str, dict] = {}
        self._last_check: dict[str, float] = {}

        if self.enabled and self.api_key:
            logger.info("Sentiment Analyzer activ (NewsAPI)")
        elif self.enabled:
            logger.warning("Sentiment enabled dar NEWSAPI_KEY lipseste — folosesc scraping gratuit")

    def get_symbol_sentiment(self, symbol: str) -> dict:
        """Returneaza sentimentul pentru un simbol.

        Returns:
            {"score": float, "articles": int, "direction": str, "headlines": list}
        """
        if not self.enabled:
            return {"score": 0.0, "articles": 0, "direction": "NEUTRAL", "headlines": []}

        symbol_clean = symbol.upper().replace(".CFD", "")

        # Check cache
        now = time.time()
        if (
            symbol_clean in self._cache
            and now - self._last_check.get(symbol_clean, 0) < self.check_interval * 60
        ):
            return self._cache[symbol_clean]

        # Fetch si analizeaza
        query = SYMBOL_QUERIES.get(symbol_clean, symbol_clean)
        articles = self._fetch_news(query)
        sentiment = self._analyze_articles(articles)

        self._cache[symbol_clean] = sentiment
        self._last_check[symbol_clean] = now

        logger.info(
            f"Sentiment {symbol_clean}: {sentiment['direction']} "
            f"(score={sentiment['score']:.2f}, {sentiment['articles']} articole)"
        )
        return sentiment

    def _fetch_news(self, query: str, hours_back: int = 12) -> list[dict]:
        """Descarca stiri de la NewsAPI sau surse gratuite."""
        if self.api_key:
            return self._fetch_newsapi(query, hours_back)
        return self._fetch_free_news(query)

    def _fetch_newsapi(self, query: str, hours_back: int = 12) -> list[dict]:
        """Descarca stiri de la NewsAPI.org."""
        from_date = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")

        url = "https://newsapi.org/v2/everything"
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 20,
            "apiKey": self.api_key,
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            return [
                {
                    "title": a.get("title", ""),
                    "description": a.get("description", ""),
                    "source": a.get("source", {}).get("name", ""),
                    "published": a.get("publishedAt", ""),
                }
                for a in data.get("articles", [])
                if a.get("title")
            ]
        except Exception as e:
            logger.warning(f"NewsAPI eroare: {e}")
            return []

    def _fetch_free_news(self, query: str) -> list[dict]:
        """Scrapeaza stiri de pe Google News RSS (gratuit, fara API key)."""
        url = f"https://news.google.com/rss/search?q={query}+when:1d&hl=en-US&gl=US&ceid=US:en"
        headers = {"User-Agent": "Mozilla/5.0"}

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "xml")
            items = soup.find_all("item", limit=20)

            return [
                {
                    "title": item.find("title").get_text(strip=True) if item.find("title") else "",
                    "description": "",
                    "source": item.find("source").get_text(strip=True) if item.find("source") else "",
                    "published": item.find("pubDate").get_text(strip=True) if item.find("pubDate") else "",
                }
                for item in items
                if item.find("title")
            ]
        except Exception as e:
            logger.warning(f"Google News RSS eroare: {e}")
            return []

    def _analyze_articles(self, articles: list[dict]) -> dict:
        """Analizeaza sentimentul din lista de articole."""
        if not articles:
            return {"score": 0.0, "articles": 0, "direction": "NEUTRAL", "headlines": []}

        total_score = 0.0
        headlines = []

        for article in articles:
            text = f"{article.get('title', '')} {article.get('description', '')}".lower()
            score = self._score_text(text)
            total_score += score
            if article.get("title"):
                headlines.append(article["title"])

        avg_score = total_score / len(articles)

        if avg_score > self.min_score:
            direction = "BULLISH"
        elif avg_score < -self.min_score:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return {
            "score": round(avg_score, 3),
            "articles": len(articles),
            "direction": direction,
            "headlines": headlines[:5],
        }

    @staticmethod
    def _score_text(text: str) -> float:
        """Scoreaza un text pe baza cuvintelor bullish/bearish."""
        bullish_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
        bearish_count = sum(1 for kw in BEARISH_KEYWORDS if kw in text)

        total = bullish_count + bearish_count
        if total == 0:
            return 0.0

        return (bullish_count - bearish_count) / total
