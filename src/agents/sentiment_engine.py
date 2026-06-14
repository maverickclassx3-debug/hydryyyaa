import feedparser
from textblob import TextBlob
import logging

class SentimentEngine:
    def __init__(self):
        # Yahoo Finance RSS Feed endpoint
        self.base_url = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={}&region=US&lang=en-US"

    def analyze_ticker(self, ticker: str) -> dict:
        """Fetches live headlines and calculates a polarity score (-1.0 to 1.0)."""
        try:
            feed = feedparser.parse(self.base_url.format(ticker))
            if not feed.entries:
                return {"sentiment_score": 0, "signal": "Neutral ⚪", "headlines": 0}

            total_polarity = 0
            # Analyze top 10 most recent headlines
            news_count = min(10, len(feed.entries))
            for entry in feed.entries[:news_count]:
                analysis = TextBlob(entry.title)
                total_polarity += analysis.sentiment.polarity

            avg_polarity = total_polarity / news_count

            if avg_polarity > 0.05:
                signal = "Bullish 🟢"
            elif avg_polarity < -0.05:
                signal = "Bearish 🔴"
            else:
                signal = "Neutral ⚪"

            return {
                "sentiment_score": round(avg_polarity, 2),
                "signal": signal,
                "headlines": news_count
            }
        except Exception as e:
            logging.error(f"Sentiment Engine failed for {ticker}: {e}")
            return {"sentiment_score": 0, "signal": "Error ⚠️", "headlines": 0}
