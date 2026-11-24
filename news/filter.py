"""
News Filter — the unified gate that decides whether the bot is allowed to trade right now.

Combines:
1. Economic calendar (block trades near high-impact events)
2. Sentiment filter (skip trades when sentiment is extreme)
"""
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import logging
import time
import pandas as pd
import time

from .calendar import (
    fetch_calendar, fetch_calendar_rolling, filter_high_impact,
    is_within_news_window, NewsEvent,
)
from .sentiment import (
    fetch_headlines, current_market_sentiment, is_sentiment_extreme, Headline,
)

logger = logging.getLogger("ict.news")


@dataclass
class NewsFilterConfig:
    enabled: bool = True
    block_minutes_before: int = 15
    block_minutes_after: int = 15
    high_impact_keywords: List[str] = None
    calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    sentiment_enabled: bool = True
    sentiment_threshold: float = 0.5
    rss_feeds: List[str] = None
    # Live feed shape: when use_today_feed is True the filter pulls today +
    # tomorrow and keeps events within +/- rolling_window_hours of now, instead
    # of dumping the whole week. This makes "next event" logging meaningful.
    use_today_feed: bool = True
    rolling_window_hours: int = 48

    def __post_init__(self):
        if self.high_impact_keywords is None:
            self.high_impact_keywords = ["NFP", "CPI", "FOMC", "Fed Chair", "Interest Rate"]
        if self.rss_feeds is None:
            self.rss_feeds = []


class NewsFilter:
    """Caches the calendar and headlines to avoid hammering the network on every tick."""

    def __init__(self, cfg: NewsFilterConfig):
        self.cfg = cfg
        self._events: List[NewsEvent] = []
        self._headlines: List[Headline] = []
        self._last_calendar_fetch = 0.0
        self._last_headline_fetch = 0.0
        self._cache_ttl_ok = 300      # 5 min between refreshes when the feed is healthy
        self._cache_ttl_failed = 60   # retry aggressively when the last fetch failed
        # Calendar fetch status — exposed so the startup self-check and any
        # monitoring can tell whether news protection is actually live.
        self.calendar_ok: bool = True
        self.calendar_error: str = ""

    def _refresh_calendar(self, force: bool = False):
        # Adaptive TTL: retry quickly after a failure, throttle when healthy.
        ttl = self._cache_ttl_failed if not self.calendar_ok else self._cache_ttl_ok
        if not force and (time.time() - self._last_calendar_fetch <= ttl):
            return

        if self.cfg.use_today_feed:
            # Fetch the FULL week (for archiving) then narrow the working set
            # to the rolling window. This ensures every event is persisted for
            # backtests, not just the ones near now.
            from .calendar import fetch_calendar_with_status, FF_THISWEEK_URL
            raw_events, ok, error = fetch_calendar_with_status(FF_THISWEEK_URL)
            if ok:
                # Archive ALL raw events before applying the rolling window
                try:
                    from . import archive
                    new_rows = archive.archive_events(raw_events)
                    if new_rows:
                        logger.debug(f"Archived {new_rows} new news event(s) for backtests")
                except Exception as ae:
                    logger.debug(f"News archive write skipped: {ae}")
                # Apply rolling window
                now = datetime.utcnow()
                cutoff_start = now - timedelta(hours=self.cfg.rolling_window_hours)
                cutoff_end = now + timedelta(hours=self.cfg.rolling_window_hours)
                events = [e for e in raw_events if cutoff_start <= e.datetime <= cutoff_end]
            else:
                events = raw_events  # [] on failure
                error = error
        else:
            # Legacy whole-week feed (explicitly configured calendar_url).
            from .calendar import fetch_calendar_with_status
            raw_events, ok, error = fetch_calendar_with_status(self.cfg.calendar_url)
            if ok:
                try:
                    from . import archive
                    new_rows = archive.archive_events(raw_events)
                    if new_rows:
                        logger.debug(f"Archived {new_rows} new news event(s) for backtests")
                except Exception as ae:
                    logger.debug(f"News archive write skipped: {ae}")
            events = raw_events

        self._last_calendar_fetch = time.time()
        self.calendar_ok = ok
        self.calendar_error = error

        if not ok:
            # HONEST LOGGING: a failure no longer masquerades as "0 high-impact
            # events". News protection is effectively OFF until the next retry.
            logger.warning(
                f"Calendar FETCH FAILED — news protection OFF "
                f"(will retry in {self._cache_ttl_failed}s): {error}"
            )
            # Keep the last good event list if we have one (stale-but-better-than-none);
            # if we never had one, _events stays [] so trades are NOT blocked by news.
            return

        self._events = filter_high_impact(events, self.cfg.high_impact_keywords)

        # Show the next upcoming event so the operator can sanity-check coverage
        # against what they see on forexfactory.com.
        now = datetime.utcnow()
        upcoming = sorted((e for e in self._events if e.datetime >= now), key=lambda e: e.datetime)
        if upcoming:
            nxt = upcoming[0]
            logger.info(
                f"Calendar OK: {len(self._events)} high-impact events in window "
                f"(next: {nxt.title} @ {nxt.datetime:%Y-%m-%d %H:%M} UTC, {nxt.country})"
            )
        else:
            logger.info(f"Calendar OK: {len(self._events)} high-impact events in window (none upcoming)")

    def _refresh_headlines(self, force: bool = False):
        if not self.cfg.sentiment_enabled:
            return
        if force or (time.time() - self._last_headline_fetch > self._cache_ttl_ok):
            self._headlines = fetch_headlines(self.cfg.rss_feeds, lookback_minutes=60)
            self._last_headline_fetch = time.time()
            score = current_market_sentiment(self._headlines)
            logger.info(f"Headlines refreshed: {len(self._headlines)} | sentiment={score:+.2f}")

    def can_trade(self, now: datetime, direction: str = None) -> tuple:
        """Returns (allowed: bool, reason: str).

        Args:
            now: current timestamp
            direction: optional "BUY" or "SELL". When provided, also blocks
                       contra-sentiment trades (e.g. don't SHORT when sentiment
                       is strongly bullish). This prevents the scenario where
                       the bot waits for sentiment to drop below the extreme
                       threshold, then immediately trades *against* the
                       prevailing sentiment.
        """
        if not self.cfg.enabled:
            return True, "News filter disabled"

        self._refresh_calendar()

        # Fail closed: unknown live news state must not weaken the protection gate.
        if not self.calendar_ok:
            return False, f"News calendar unavailable: {self.calendar_error}"

        # Economic calendar check
        blocked, ev = is_within_news_window(
            now, self._events,
            self.cfg.block_minutes_before,
            self.cfg.block_minutes_after,
        )
        if blocked:
            return False, f"News block: {ev.title} @ {ev.datetime:%H:%M UTC}"

        # Sentiment check
        if self.cfg.sentiment_enabled:
            self._refresh_headlines()
            score = current_market_sentiment(self._headlines)
            if is_sentiment_extreme(score, self.cfg.sentiment_threshold):
                return False, f"Sentiment extreme: {score:+.2f}"

            # >>> DIRECTIONAL SENTIMENT BLOCK: don't trade against the prevailing
            # sentiment. If sentiment is positive (bullish), only allow BUYs; if
            # negative (bearish), only allow SELLs. Trades aligned with sentiment
            # are allowed regardless of magnitude (unless above extreme threshold).
            # This prevents the 2026-07-20 scenario: sentiment +0.49 (bullish),
            # the bot waited for it to drop below 0.50, then immediately shorted.
            threshold = self.cfg.sentiment_threshold * 0.5  # half the extreme threshold
            if direction is not None:
                if score >= threshold and direction == "SELL":
                    return False, f"Sentiment contra-SELL: {score:+.2f} (bullish, not shorting)"
                elif score <= -threshold and direction == "BUY":
                    return False, f"Sentiment contra-BUY: {score:+.2f} (bearish, not buying)"

        return True, "OK"

    @property
    def current_sentiment_score(self) -> float:
        """Return the cached sentiment score (for logging/cross-referencing)."""
        if not self._headlines:
            return 0.0
        return current_market_sentiment(self._headlines)

    def upcoming_events(self, within_minutes: int = 60) -> List[NewsEvent]:
        """Return high-impact events in the next N minutes (for logging/UI)."""
        self._refresh_calendar()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return [
            e for e in self._events
            if 0 <= (e.datetime - now).total_seconds() <= within_minutes * 60
        ]


# === ADDED FOR ICT BOOK IMPLEMENTATION (Ch.18) ===

def is_nfp_week(timestamp: pd.Timestamp, calendar_events: list) -> bool:
    """
    Check if current week contains NFP (Nonfarm Payroll) event.
    NFP is typically first Friday of each month.
    
    Book reference: Ch.18 p.242 - "Avoid Thursday & Friday of NFP week"
    """
    import pandas as pd
    current_week = timestamp.isocalendar()[1]
    
    for event in calendar_events:
        event_title = event.get("title", "").upper()
        if "NFP" in event_title or "NONFARM" in event_title or "NON-FARM" in event_title:
            try:
                event_time = pd.to_datetime(event.get("datetime", event.get("time", "")))
                if event_time.isocalendar()[1] == current_week:
                    return True
            except:
                continue
    
    return False

def is_fomc_day(timestamp: pd.Timestamp, calendar_events: list) -> bool:
    """
    Check if today is FOMC meeting day.
    
    Book reference: Ch.18 p.242 - "FOMC days are taboo"
    """
    import pandas as pd
    today = timestamp.date()
    
    for event in calendar_events:
        if "FOMC" in event.get("title", "").upper():
            try:
                event_time = pd.to_datetime(event.get("datetime", event.get("time", "")))
                event_date = event_time.date()
                if event_date == today:
                    return True
            except:
                continue
    
    return False

def should_block_trade_by_news(timestamp: pd.Timestamp, calendar_events: list) -> tuple:
    """
    Comprehensive news blocking per ICT book.
    
    Returns:
        (should_block: bool, reason: str)
    """
    import pandas as pd
    
    # FOMC day - block all day
    if is_fomc_day(timestamp, calendar_events):
        return (True, "FOMC day - book blocks all trading (Ch.18)")
    
    # NFP week Thu/Fri - block
    if is_nfp_week(timestamp, calendar_events):
        if timestamp.dayofweek >= 3:  # Thursday or Friday
            return (True, "NFP week Thu/Fri - book blocks (Ch.18)")
    
    # Monday after NFP - block
    if timestamp.dayofweek == 0:  # Monday
        last_week = timestamp - pd.Timedelta(days=7)
        for event in calendar_events:
            if "NFP" in event.get("title", "").upper():
                try:
                    event_time = pd.to_datetime(event.get("datetime", event.get("time", "")))
                    if last_week <= event_time <= timestamp:
                        return (True, "Monday after NFP - book blocks (Ch.18)")
                except:
                    continue
    
    return (False, "No news blocks")
