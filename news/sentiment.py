"""
Sentiment Analysis — pulls RSS headlines and computes VADER sentiment.

If the latest headlines have very strong positive or negative sentiment (|score| > threshold),
the bot will skip new trades because the market is likely emotional/news-driven.
"""
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timedelta, timezone
import logging
import urllib.request
import urllib.error

logger = logging.getLogger("ict.news.sentiment")

# Lightweight VADER fallback if vaderSentiment is not installed
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _VADER = SentimentIntensityAnalyzer()
    def _score(text: str) -> float:
        return _VADER.polarity_scores(text)["compound"]
except Exception:
    _VADER = None
    # Very crude fallback: positive/negative word count
    POS = {"up", "rise", "gain", "rally", "surge", "bull", "bullish", "beat", "strong", "jump", "boom", "high", "growth"}
    NEG = {"down", "fall", "drop", "loss", "plunge", "bear", "bearish", "miss", "weak", "crash", "low", "recession", "cut", "fear"}
    def _score(text: str) -> float:
        tokens = text.lower().split()
        if not tokens:
            return 0.0
        p = sum(1 for t in tokens if t in POS)
        n = sum(1 for t in tokens if t in NEG)
        return (p - n) / len(tokens)


@dataclass
class Headline:
    title: str
    published: datetime
    sentiment: float


def fetch_headlines(rss_urls: List[str], lookback_minutes: int = 60) -> List[Headline]:
    """Fetch recent headlines from RSS feeds and compute sentiment.
    Falls back gracefully to an empty list if feeds are unreachable.

    >>> FIX 5: Added socket.setdefaulttimeout(8) so feedparser.parse() never
    blocks the main loop forever. Previously feedparser inherited Python's
    default timeout of None (block forever), which caused the bot to hang
    when Yahoo Finance RSS feeds were slow/dead. Ctrl+C had no Python bytecode
    to interrupt inside the C-level socket call.
    """
    try:
        import feedparser
    except ImportError:
        logger.warning("feedparser not installed, sentiment disabled")
        return []

    headlines: List[Headline] = []
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=lookback_minutes)

    import socket
    _prev_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(8)  # hard 8s per-feed deadline

        for url in rss_urls:
            try:
                feed = feedparser.parse(url)
                # feedparser.parse can return a "bozo_exception" field on
                # network errors instead of raising. Check it.
                if getattr(feed, 'bozo', 0) and not feed.entries:
                    exc = getattr(feed, 'bozo_exception', None)
                    logger.debug(f"RSS feed bozo error ({url}): {exc}")
                    continue
                for entry in feed.entries[:20]:
                    # Parse published time
                    pub = entry.get("published_parsed") or entry.get("updated_parsed")
                    if pub:
                        published = datetime(*pub[:6])
                    else:
                        published = datetime.now(timezone.utc).replace(tzinfo=None)

                    if published < cutoff:
                        continue

                    title = entry.get("title", "")
                    headlines.append(Headline(
                        title=title,
                        published=published,
                        sentiment=_score(title),
                    ))
            except (socket.timeout, urllib.error.URLError, OSError) as e:
                logger.warning(f"RSS feed timeout/error ({url}): {type(e).__name__}: {e}")
                continue
            except Exception as e:
                logger.debug(f"RSS feed error ({url}): {e}")
                continue
    finally:
        socket.setdefaulttimeout(_prev_timeout)  # restore previous default

    return headlines


def current_market_sentiment(headlines: List[Headline]) -> float:
    """Aggregate recent headline sentiment into a single score in [-1, 1]."""
    if not headlines:
        return 0.0
    return sum(h.sentiment for h in headlines) / len(headlines)


def is_sentiment_extreme(score: float, threshold: float = 0.5) -> bool:
    """Return True if |score| exceeds threshold (skip trade)."""
    return abs(score) >= threshold
