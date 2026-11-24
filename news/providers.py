"""
News providers — a thin swappable abstraction over calendar data sources.

Why this exists
---------------
The historical news investigation (2026-07) established two facts:

1. The ForexFactory free feed (nfs.faireconomy.media/ff_calendar_thisweek.json)
   is the only no-key source of structured economic events, and it only ever
   returns the CURRENT week. It cannot answer "what news happened on July 14?"
   for any past date.

2. Paid providers (FMP, TradingEconomics) gate the economic calendar behind a
   paid plan — a free-tier FMP key returns 402/403 on the calendar endpoint.
   We verified this directly; do not assume otherwise.

So the realistic path to "backtests are news-aware for past dates" is
**archive-as-you-go**: every time the live bot successfully refreshes the
calendar, we persist that week's events to a local SQLite store
(`data/news_calendar.db`). Coverage grows for free as the bot runs. The
backtester then reads from that store instead of a network feed.

The provider abstraction here is deliberately small — it lets a future paid
provider drop in cleanly, but today only ForexFactoryProvider is implemented.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Protocol, runtime_checkable, Tuple
import logging

from .calendar import NewsEvent, fetch_calendar_with_status, FF_THISWEEK_URL

logger = logging.getLogger("ict.news")


class NewsProviderError(RuntimeError):
    """Raised when a provider cannot fulfill a request (bad key, paid wall, etc.)."""


@runtime_checkable
class CalendarProvider(Protocol):
    """Anything that can hand back economic events for a date range."""

    def fetch_range(self, start: date, end: date) -> Tuple[List[NewsEvent], bool, str]:
        """Return (events, ok, error). ok=False means the provider could not fetch."""
        ...


class ForexFactoryProvider:
    """Free ForexFactory feed (current week only).

    Note: this provider can only serve events that fall inside the current
    ForexFactory week file (Mon-Sun). Asking for a historical range that is
    not the current week will return ok=True but an empty list — it is NOT an
    error, the data simply isn't available from this free source. Callers that
    need true history should consult the local archive (`news.archive`).
    """

    name = "forexfactory"

    def __init__(self, url: str = FF_THISWEEK_URL):
        self.url = url

    def fetch_range(self, start: date, end: date) -> Tuple[List[NewsEvent], bool, str]:
        # The free feed ignores date params entirely; it always returns the
        # current week. We fetch once and filter client-side to [start, end].
        events, ok, error = fetch_calendar_with_status(self.url)
        if not ok:
            return [], False, error
        lo = datetime.combine(start, datetime.min.time())
        hi = datetime.combine(end, datetime.max.time())
        in_range = [e for e in events if lo <= e.datetime <= hi]
        return in_range, True, ""

    def fetch_current_week(self) -> Tuple[List[NewsEvent], bool, str]:
        """Convenience: return everything in the live week file, unfiltered."""
        return fetch_calendar_with_status(self.url)


class FMPProvider:
    """Financial Modeling Prep economic calendar — NOT IMPLEMENTED.

    Kept as a stub so the provider slot exists for a future paid key. The free
    tier returns HTTP 402 ("Restricted Endpoint") on
    /stable/economic-calendar, and the legacy /api/v3/economic_calendar is dead
    for new keys (403). We confirmed this empirically; do not re-enable without
    a plan that actually includes the calendar endpoint.
    """

    name = "fmp"

    def __init__(self, api_key: str = ""):
        if not api_key:
            raise NewsProviderError(
                "FMPProvider requires an api_key, and the FMP free tier does NOT "
                "include the economic calendar endpoint (returns 402). A paid plan "
                "is required. Use ForexFactoryProvider + the local archive instead."
            )
        self.api_key = api_key

    def fetch_range(self, start: date, end: date) -> Tuple[List[NewsEvent], bool, str]:
        raise NewsProviderError(
            "FMP calendar integration is not implemented — the free-tier key "
            "cannot reach this endpoint. Falling back to ForexFactory + archive."
        )


def get_default_provider() -> CalendarProvider:
    """The provider used by the live path: ForexFactory (no key, always available)."""
    return ForexFactoryProvider()
