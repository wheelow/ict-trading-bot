"""
Economic Calendar — pulls high-impact news events from ForexFactory (free JSON feed).

The bot blocks trades within +/- N minutes of any high-impact event that matches
configured keywords (NFP, CPI, FOMC, Fed Chair, Interest Rate, etc.).

Robustness notes
----------------
* `fetch_calendar` retries with exponential backoff so a transient DNS hiccup or
  network blip does NOT silently disable news protection. It returns a tuple
  `(events, ok, error)` so callers can distinguish "failed" from "succeeded but
  empty" — these used to produce the identical log line, which hid real failures.
* The default feed is `ff_calendar_today.json` (today's events), not
  `..._thisweek.json`. A rolling-window helper keeps events within +/- N hours
  of now by combining today + tomorrow files.
* Backwards compatibility: `fetch_calendar` still returns a plain `List[NewsEvent]`
  when called the old way (positional url or kwarg), preserving the contract every
  existing call site expects. Use `fetch_calendar_with_status()` for the tuple form.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
from datetime import datetime, timedelta, timezone
import json
import logging
import time
import urllib.request
import urllib.error

logger = logging.getLogger("ict.news")

# ForexFactory free JSON feeds (hosted on the nfs.faireconomy.media CDN).
# NOTE: the CDN only serves weekly files. There is no "today.json" despite the
# name being intuitive — it 404s. So we fetch thisweek.json (Mon-Sun) and apply
# a client-side rolling window to focus on events relevant to NOW.
FF_TODAY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"  # alias kept for compat
FF_TOMORROW_URL = FF_TODAY_URL
FF_THISWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Retry policy for calendar fetches. A failure here used to blind the bot to news
# for 5 minutes (the NewsFilter cache TTL). Retry in-process first, then let the
# caller shorten its cache so the next attempt comes sooner.
_FETCH_TIMEOUT = 12
_RETRY_BACKOFFS = [0.5, 1.5, 4.0]  # seconds to sleep before attempts 2, 3, 4


@dataclass
class NewsEvent:
    title: str
    country: str
    currency: str
    datetime: datetime   # UTC
    impact: str          # "high", "medium", "low"
    forecast: str
    previous: str


def _parse_ff_item(item: dict) -> Optional[NewsEvent]:
    """Convert one ForexFactory JSON item into a NewsEvent (UTC), or None on parse failure."""
    try:
        dt_str = item.get("date", "")
        if not dt_str:
            return None
        # ForexFactory returns times like "2024-01-05T13:30:00-05:00" (EST)
        try:
            dt_local = datetime.fromisoformat(dt_str)
        except ValueError:
            # Some feeds don't include tz offset — assume US/Eastern.
            from zoneinfo import ZoneInfo
            est = ZoneInfo("America/New_York")
            dt_local = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=est)
        dt_utc = dt_local.astimezone(timezone.utc).replace(tzinfo=None) if dt_local.tzinfo else dt_local

        return NewsEvent(
            title=item.get("title", ""),
            country=item.get("country", ""),
            currency=item.get("currency", ""),
            datetime=dt_utc,
            impact=item.get("impact", "").lower(),
            forecast=item.get("forecast", ""),
            previous=item.get("previous", ""),
        )
    except Exception:
        return None


def _raw_fetch(url: str, timeout: int = _FETCH_TIMEOUT) -> List[dict]:
    """Low-level GET that returns parsed JSON (a list). Raises on any error."""
    req = urllib.request.Request(url, headers={"User-Agent": "ICT-Bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_calendar_with_status(
    url: str = FF_TODAY_URL,
    timeout: int = _FETCH_TIMEOUT,
    retries: Optional[List[float]] = None,
) -> Tuple[List[NewsEvent], bool, str]:
    """Fetch the ForexFactory calendar with retry/backoff.

    Returns:
        (events, ok, error):
          * ok=True, error=""  -> fetched successfully (events may legitimately be [])
          * ok=False, error=.. -> every attempt failed; events is []
    """
    backoffs = _RETRY_BACKOFFS if retries is None else retries
    last_err = ""
    for attempt, backoff in enumerate([0.0] + backoffs):
        if backoff:
            time.sleep(backoff)
        try:
            data = _raw_fetch(url, timeout=timeout)
            events = []
            for item in data:
                ev = _parse_ff_item(item)
                if ev is not None:
                    events.append(ev)
            return events, True, ""
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            logger.debug(f"Calendar fetch attempt {attempt + 1}/{len(backoffs) + 1} failed for {url}: {last_err}")

    # All attempts exhausted.
    return [], False, last_err


def fetch_calendar(url: str = FF_TODAY_URL) -> List[NewsEvent]:
    """Backwards-compatible fetch: returns just the event list.

    Retries internally with backoff. On total failure logs a WARNING and returns
    []. Kept as a List-returning wrapper so existing call sites keep working;
    new code should prefer `fetch_calendar_with_status()` to tell failure apart
    from "succeeded but empty".
    """
    events, ok, error = fetch_calendar_with_status(url=url)
    if not ok:
        logger.warning(f"Failed to fetch economic calendar after retries: {error}")
    return events


def fetch_calendar_rolling(window_hours: int = 48, timeout: int = _FETCH_TIMEOUT) -> Tuple[List[NewsEvent], bool, str]:
    """Fetch the weekly ForexFactory feed and keep events within +/- window_hours of now.

    The ForexFactory CDN only serves a whole-week file, so we pull that once and
    narrow it client-side to a rolling window centered on now. This makes the
    "next upcoming event" log line meaningful and keeps the working set small.
    Returns (events, ok, error): ok=True on a successful fetch (events may be []
    if nothing falls in the window, e.g. early Monday before London).
    """
    events, ok, error = fetch_calendar_with_status(FF_THISWEEK_URL, timeout=timeout)
    if not ok:
        return [], False, error

    now = datetime.utcnow()
    cutoff_start = now - timedelta(hours=window_hours)
    cutoff_end = now + timedelta(hours=window_hours)
    windowed = [e for e in events if cutoff_start <= e.datetime <= cutoff_end]
    return windowed, True, ""


def is_high_impact(event: NewsEvent, keywords: List[str]) -> bool:
    """Check whether a news event is high-impact based on configured keywords."""
    if event.impact == "high":
        return True
    title_lower = event.title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def filter_high_impact(events: List[NewsEvent], keywords: List[str]) -> List[NewsEvent]:
    return [e for e in events if is_high_impact(e, keywords)]


def is_within_news_window(now: datetime, events: List[NewsEvent], minutes_before: int = 15, minutes_after: int = 15) -> tuple:
    """Check whether `now` is within the news-block window of any high-impact event.
    Returns (True, event) if blocked, else (False, None).
    """
    for ev in events:
        window_start = ev.datetime - timedelta(minutes=minutes_before)
        window_end = ev.datetime + timedelta(minutes=minutes_after)
        if window_start <= now <= window_end:
            return True, ev
    return False, None


# === ADDED FOR ICT BOOK IMPLEMENTATION ===

def get_upcoming_high_impact_events(days_ahead: int = 7) -> List[NewsEvent]:
    """
    Get high-impact events for next N days.
    Useful for weekly planning per book's guidance.
    """
    try:
        events, ok, error = fetch_calendar_with_status(FF_THISWEEK_URL)
        if not ok:
            logger.error(f"Failed to fetch upcoming events: {error}")
            return []

        now = datetime.utcnow()
        cutoff = now + timedelta(days=days_ahead)

        high_impact = [e for e in events if e.impact == "high" and now <= e.datetime <= cutoff]
        return high_impact
    except Exception as e:
        logger.error(f"Failed to fetch upcoming events: {e}")
        return []

def is_high_impact_news_within_window(timestamp: datetime, events: List[NewsEvent], window_minutes: int = 15) -> bool:
    """
    Check if high-impact news is within ±window_minutes.
    Book uses ±15 minutes as standard.
    """
    for event in events:
        if event.impact != "high":
            continue

        time_diff = abs((event.datetime - timestamp).total_seconds() / 60)

        if time_diff <= window_minutes:
            return True

    return False
