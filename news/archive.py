"""
News archive — a local SQLite cache of economic events.

Purpose
-------
The ForexFactory free feed only ever returns the *current* week. For backtests
to be news-aware on past dates, there must be a historical record. Rather than
depend on a paid API (verified unavailable on the FMP free tier), we archive
**as we go**: every successful live calendar refresh writes its events here.

The store is keyed by (country, title, datetime_utc) so re-archiving the same
week is idempotent — duplicate inserts are ignored, never duplicated.

Schema
------
    events(
        country      TEXT,
        title        TEXT,
        event_time   TEXT,   -- ISO8601 UTC, e.g. '2026-07-22 07:00:00'
        currency     TEXT,
        impact       TEXT,   -- 'high' | 'medium' | 'low'
        forecast     TEXT,
        previous     TEXT,
        archived_at  TEXT,   -- ISO8601 of when we stored it (debugging)
        PRIMARY KEY (country, title, event_time)
    )

Querying is by date range; `has_coverage` lets the backtester decide whether a
date is covered (block on news) or should be skipped gracefully.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple
import sqlite3

from .calendar import NewsEvent

logger = logging.getLogger("ict.news")

# Resolve the DB path relative to the repo root (one level up from this file),
# under data/. This matches the existing ledger DBs.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "data", "news_calendar.db")


def _connect(db_path: str = _DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")  # concurrent reader during backtest
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            country     TEXT,
            title       TEXT,
            event_time  TEXT,
            currency    TEXT,
            impact      TEXT,
            forecast    TEXT,
            previous    TEXT,
            archived_at TEXT,
            PRIMARY KEY (country, title, event_time)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time)"
    )
    conn.commit()
    return conn


def _event_time_str(e: NewsEvent) -> str:
    """Normalize an event's UTC datetime to a stable ISO string for storage."""
    dt = e.datetime
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def archive_events(events: List[NewsEvent], db_path: str = _DEFAULT_DB_PATH) -> int:
    """Bulk-insert events, ignoring duplicates. Returns the number of NEW rows.

    Safe to call repeatedly with overlapping data (e.g. every 5-minute refresh)
    — the PRIMARY KEY dedupes, so only genuinely new events increment the count.
    """
    if not events:
        return 0
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (
            e.country or "",
            e.title or "",
            _event_time_str(e),
            e.currency or "",
            (e.impact or "").lower(),
            e.forecast or "",
            e.previous or "",
            now_str,
        )
        for e in events
    ]
    conn = _connect(db_path)
    try:
        cur = conn.executemany(
            "INSERT OR IGNORE INTO events "
            "(country, title, event_time, currency, impact, forecast, previous, archived_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_events_range(
    start: date, end: date, db_path: str = _DEFAULT_DB_PATH
) -> List[NewsEvent]:
    """Return all archived events whose UTC time falls within [start, end] (inclusive)."""
    lo = datetime.combine(start, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
    hi = datetime.combine(end, datetime.max.time()).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT country, title, event_time, currency, impact, forecast, previous "
            "FROM events WHERE event_time BETWEEN ? AND ? ORDER BY event_time",
            (lo, hi),
        )
        out: List[NewsEvent] = []
        for row in cur.fetchall():
            country, title, tstr, currency, impact, forecast, previous = row
            try:
                dt = datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            out.append(
                NewsEvent(
                    title=title,
                    country=country,
                    currency=currency,
                    datetime=dt,
                    impact=impact,
                    forecast=forecast,
                    previous=previous,
                )
            )
        return out
    finally:
        conn.close()


def get_events_for_day(day: date, db_path: str = _DEFAULT_DB_PATH) -> List[NewsEvent]:
    """Convenience: all events for a single calendar day (UTC)."""
    return get_events_range(day, day, db_path)


def has_coverage(day: date, db_path: str = _DEFAULT_DB_PATH) -> bool:
    """Fast check: do we have ANY archived event for this day?

    Used by the backtester to decide block-vs-skip. A day with zero archived
    rows is treated as "no coverage" (skip gracefully) rather than "no news
    happened that day" — because we can't tell the difference without history.
    """
    lo = datetime.combine(day, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
    hi = datetime.combine(day, datetime.max.time()).strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT 1 FROM events WHERE event_time BETWEEN ? AND ? LIMIT 1", (lo, hi)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def coverage_summary(
    start: date, end: date, db_path: str = _DEFAULT_DB_PATH
) -> Tuple[int, int]:
    """Return (covered_days, total_days) in [start, end] — for status logging."""
    total = 0
    covered = 0
    conn = _connect(db_path)
    try:
        d = start
        while d <= end:
            total += 1
            lo = datetime.combine(d, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
            hi = datetime.combine(d, datetime.max.time()).strftime("%Y-%m-%d %H:%M:%S")
            cur = conn.execute(
                "SELECT 1 FROM events WHERE event_time BETWEEN ? AND ? LIMIT 1", (lo, hi)
            )
            if cur.fetchone() is not None:
                covered += 1
            d += timedelta(days=1)
    finally:
        conn.close()
    return covered, total


def get_db_path() -> str:
    """Expose the resolved DB path for diagnostics/logging."""
    return _DEFAULT_DB_PATH
