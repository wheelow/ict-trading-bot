"""
News Trader — Learns to Trade News Events Profitably

Three-phase approach:
1. OBSERVE (now): Watch every news event, record what happens
2. LEARN (after 20 events): Identify patterns per event type
3. TRADE (after 50 events): Attempt post-news setups when win rate > 60%

NEVER trades during the news spike (first 5 minutes) — too risky.
Only trades 15-60 minutes AFTER news, when ICT setups appear.

News events tracked:
- NFP (Nonfarm Payrolls) — 1st Friday of month
- CPI (Consumer Price Index) — monthly
- FOMC (Federal Open Market Committee) — 8x per year
- Fed Chair speeches
- Interest Rate decisions
- Unemployment claims
- GDP, PMI, Retail Sales

For each event, the bot records:
- Pre-news price (5 min before)
- Spike direction and magnitude
- Post-news behavior (reversal vs continuation)
- Whether ICT setups appeared 15-60 min after
- Outcome of those setups (once enough data)
"""
import sqlite3
import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import json
import os

from .memory import TradeLedger

logger = logging.getLogger("ict.news_trader")


@dataclass
class NewsObservation:
    """A complete observation of a news event and its aftermath."""
    id: Optional[int] = None
    event_title: str = ""
    event_type: str = ""           # NFP, CPI, FOMC, Fed Chair, etc.
    event_time: str = ""           # ISO format
    currency: str = ""             # USD, EUR, etc.
    impact: str = ""               # high, medium, low

    # Pre-news state
    pre_price_us30: float = 0
    pre_price_us100: float = 0
    pre_price_us500: float = 0

    # Spike (first 5 minutes)
    spike_direction: str = ""      # up, down, none
    spike_magnitude_points: float = 0

    # Post-news behavior (15-60 min after)
    post_behavior: str = ""        # reversal, continuation, choppy, trend
    price_15min_after: float = 0
    price_30min_after: float = 0
    price_60min_after: float = 0

    # ICT setup appearance
    setup_appeared_15_60min: bool = False
    setup_type: str = ""           # OTE, FVG, OB, liquidity_sweep
    setup_direction: str = ""      # bullish, bearish

    # Outcome (filled later if setup was traded)
    setup_outcome: str = ""        # won, lost, not_traded, pending

    # Notes
    notes: str = ""


class NewsTrader:
    """Learns to trade news events through observation and pattern recognition."""

    def __init__(self, ledger: TradeLedger, broker=None):
        self.ledger = ledger
        self.broker = broker
        self.db_path = ledger.db_path
        self._init_news_tables()
        self.pending_observations: Dict[str, NewsObservation] = {}  # event_key -> observation
        self.observations_this_session = 0
        logger.info("News trader initialized — observing news events to learn patterns")

    def _init_news_tables(self):
        """Create news observation tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_title TEXT,
                event_type TEXT,
                event_time TEXT,
                currency TEXT,
                impact TEXT,
                pre_price_us30 REAL,
                pre_price_us100 REAL,
                pre_price_us500 REAL,
                spike_direction TEXT,
                spike_magnitude_points REAL,
                post_behavior TEXT,
                price_15min_after REAL,
                price_30min_after REAL,
                price_60min_after REAL,
                setup_appeared_15_60min INTEGER,
                setup_type TEXT,
                setup_direction TEXT,
                setup_outcome TEXT,
                notes TEXT,
                created_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                pattern_name TEXT,
                occurrence_count INTEGER,
                reversal_rate REAL,
                continuation_rate REAL,
                setup_success_rate REAL,
                avg_spike_points REAL,
                notes TEXT,
                last_updated TEXT
            )
        """)
        conn.commit()
        conn.close()

    def observe_upcoming_event(self, event: Dict, broker) -> Optional[NewsObservation]:
        """Called when a high-impact news event is approaching.
        Records pre-news state and schedules post-news analysis.
        """
        event_time = event.get("datetime") or event.get("date")
        if not event_time:
            return None

        # Parse event time
        if isinstance(event_time, str):
            try:
                event_dt = datetime.fromisoformat(event_time.replace("Z", ""))
            except Exception:
                try:
                    event_dt = datetime.strptime(event_time, "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    return None
        else:
            event_dt = event_time

        # Normalize to naive UTC
        if hasattr(event_dt, 'tzinfo') and event_dt.tzinfo is not None:
            event_dt = event_dt.replace(tzinfo=None)

        event_key = f"{event.get('title', '')}_{event_dt.isoformat()}"

        # Don't double-record
        if event_key in self.pending_observations:
            return self.pending_observations[event_key]

        # Determine event type
        title = event.get("title", "").lower()
        event_type = "other"
        if "nonfarm" in title or "nfp" in title:
            event_type = "NFP"
        elif "cpi" in title or "consumer price" in title:
            event_type = "CPI"
        elif "fomc" in title or "federal open" in title:
            event_type = "FOMC"
        elif "fed chair" in title or "powell" in title:
            event_type = "Fed_Chair"
        elif "interest rate" in title or "rate decision" in title:
            event_type = "Rate_Decision"
        elif "unemployment" in title or "claims" in title:
            event_type = "Unemployment"
        elif "gdp" in title:
            event_type = "GDP"
        elif "pmi" in title:
            event_type = "PMI"
        elif "retail sales" in title:
            event_type = "Retail_Sales"

        # Record pre-news prices
        pre_prices = {}
        for symbol in ["US30", "US100", "US500"]:
            try:
                pre_prices[symbol] = broker.get_tick(symbol)
            except Exception:
                pre_prices[symbol] = 0

        observation = NewsObservation(
            event_title=event.get("title", ""),
            event_type=event_type,
            event_time=event_dt.isoformat(),
            currency=event.get("currency", "USD"),
            impact=event.get("impact", "high"),
            pre_price_us30=pre_prices.get("US30", 0),
            pre_price_us100=pre_prices.get("US100", 0),
            pre_price_us500=pre_prices.get("US500", 0),
        )

        self.pending_observations[event_key] = observation
        logger.info(f"[NEWS] Observing upcoming {event_type}: {observation.event_title} @ {event_dt}")
        return observation

    def analyze_post_news(self, event_key: str, broker) -> Optional[NewsObservation]:
        """Called 60 minutes after a news event to record what happened."""
        if event_key not in self.pending_observations:
            return None

        obs = self.pending_observations[event_key]
        try:
            event_dt = datetime.fromisoformat(obs.event_time)
        except Exception:
            return None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        minutes_after = (now - event_dt).total_seconds() / 60

        if minutes_after < 60:
            # Not enough time has passed yet
            return None

        # Fetch price history since the event
        try:
            df = broker.get_history("US30", "M5", count=60)
            if df.empty or len(df) < 50:
                return None

            # Find the bar closest to event time
            df_naive = df.index
            if hasattr(df.index[0], 'tzinfo') and df.index[0].tzinfo is not None:
                df_naive = df.index.tz_localize(None)

            event_idx = None
            for i, ts in enumerate(df_naive):
                if abs((ts - event_dt).total_seconds()) < 300:  # within 5 min
                    event_idx = i
                    break

            if event_idx is None or event_idx >= len(df) - 12:
                return None

            # Spike analysis (first 5 min = 1 M5 bar)
            pre_close = df["close"].iloc[event_idx - 1] if event_idx > 0 else obs.pre_price_us30
            spike_high = df["high"].iloc[event_idx]
            spike_low = df["low"].iloc[event_idx]
            spike_close = df["close"].iloc[event_idx]

            spike_up = spike_high - pre_close
            spike_down = pre_close - spike_low

            if spike_up > spike_down:
                obs.spike_direction = "up"
                obs.spike_magnitude_points = spike_up
            elif spike_down > 0:
                obs.spike_direction = "down"
                obs.spike_magnitude_points = spike_down
            else:
                obs.spike_direction = "none"
                obs.spike_magnitude_points = 0

            # Post-news prices (15, 30, 60 min after)
            obs.price_15min_after = df["close"].iloc[min(event_idx + 3, len(df) - 1)]
            obs.price_30min_after = df["close"].iloc[min(event_idx + 6, len(df) - 1)]
            obs.price_60min_after = df["close"].iloc[min(event_idx + 12, len(df) - 1)]

            # Determine post-news behavior
            price_15 = obs.price_15min_after
            price_60 = obs.price_60min_after
            spike_dir = obs.spike_direction

            if spike_dir == "up":
                if price_60 < pre_close:
                    obs.post_behavior = "reversal"  # spiked up, then reversed down
                elif price_60 > price_15:
                    obs.post_behavior = "continuation"  # kept going up
                else:
                    obs.post_behavior = "choppy"
            elif spike_dir == "down":
                if price_60 > pre_close:
                    obs.post_behavior = "reversal"  # spiked down, then reversed up
                elif price_60 < price_15:
                    obs.post_behavior = "continuation"  # kept going down
                else:
                    obs.post_behavior = "choppy"
            else:
                obs.post_behavior = "choppy"

            # Check if ICT setup appeared (simplified — check for FVG or large wick)
            for i in range(event_idx + 3, min(event_idx + 12, len(df) - 1)):
                # FVG check
                if df["low"].iloc[i + 1] > df["high"].iloc[i - 1]:
                    obs.setup_appeared_15_60min = True
                    obs.setup_type = "FVG"
                    obs.setup_direction = "bullish"
                    break
                elif df["high"].iloc[i + 1] < df["low"].iloc[i - 1]:
                    obs.setup_appeared_15_60min = True
                    obs.setup_type = "FVG"
                    obs.setup_direction = "bearish"
                    break
                # Large wick check (reversal signal)
                body = abs(df["close"].iloc[i] - df["open"].iloc[i])
                upper_wick = df["high"].iloc[i] - max(df["open"].iloc[i], df["close"].iloc[i])
                lower_wick = min(df["open"].iloc[i], df["close"].iloc[i]) - df["low"].iloc[i]
                if body > 0 and (upper_wick > 2 * body or lower_wick > 2 * body):
                    obs.setup_appeared_15_60min = True
                    obs.setup_type = "wick_reversal"
                    obs.setup_direction = "bullish" if lower_wick > upper_wick else "bearish"
                    break

            # Save to database
            self._save_observation(obs)
            self.observations_this_session += 1

            logger.info(f"[NEWS] {obs.event_type} analysis complete:")
            logger.info(f"  Spike: {obs.spike_direction} ({obs.spike_magnitude_points:.0f} pts)")
            logger.info(f"  Post-news: {obs.post_behavior}")
            logger.info(f"  Setup appeared: {obs.setup_appeared_15_60min} ({obs.setup_type} {obs.setup_direction})")

            # Remove from pending
            del self.pending_observations[event_key]

            # Update patterns
            self._update_patterns(obs)

            return obs

        except Exception as e:
            logger.debug(f"[NEWS] Post-news analysis error: {e}")
            return None

    def _save_observation(self, obs: NewsObservation):
        """Save observation to database."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT INTO news_observations (
                    event_title, event_type, event_time, currency, impact,
                    pre_price_us30, pre_price_us100, pre_price_us500,
                    spike_direction, spike_magnitude_points, post_behavior,
                    price_15min_after, price_30min_after, price_60min_after,
                    setup_appeared_15_60min, setup_type, setup_direction,
                    setup_outcome, notes, created_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                obs.event_title, obs.event_type, obs.event_time, obs.currency, obs.impact,
                obs.pre_price_us30, obs.pre_price_us100, obs.pre_price_us500,
                obs.spike_direction, obs.spike_magnitude_points, obs.post_behavior,
                obs.price_15min_after, obs.price_30min_after, obs.price_60min_after,
                int(obs.setup_appeared_15_60min), obs.setup_type, obs.setup_direction,
                obs.setup_outcome, obs.notes,
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[NEWS] Save error: {e}")

    def _update_patterns(self, obs: NewsObservation):
        """Update aggregated patterns for this event type."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # Get all observations for this event type
            rows = conn.execute(
                "SELECT * FROM news_observations WHERE event_type = ?",
                (obs.event_type,)
            ).fetchall()

            if len(rows) < 3:
                conn.close()
                return

            # Compute stats
            total = len(rows)
            reversals = sum(1 for r in rows if r["post_behavior"] == "reversal")
            continuations = sum(1 for r in rows if r["post_behavior"] == "continuation")
            setups_appeared = sum(1 for r in rows if r["setup_appeared_15_60min"])
            avg_spike = sum(r["spike_magnitude_points"] or 0 for r in rows) / total

            reversal_rate = reversals / total * 100
            continuation_rate = continuations / total * 100
            setup_rate = setups_appeared / total * 100

            # Update or insert pattern
            existing = conn.execute(
                "SELECT id FROM news_patterns WHERE event_type = ?",
                (obs.event_type,)
            ).fetchone()

            notes = f"Reversal {reversal_rate:.0f}%, Continuation {continuation_rate:.0f}%, Setup rate {setup_rate:.0f}%"

            if existing:
                conn.execute("""
                    UPDATE news_patterns SET
                        occurrence_count = ?,
                        reversal_rate = ?,
                        continuation_rate = ?,
                        setup_success_rate = ?,
                        avg_spike_points = ?,
                        notes = ?,
                        last_updated = ?
                    WHERE event_type = ?
                """, (total, reversal_rate, continuation_rate, setup_rate, avg_spike,
                      notes, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                      obs.event_type))
            else:
                conn.execute("""
                    INSERT INTO news_patterns (
                        event_type, pattern_name, occurrence_count, reversal_rate,
                        continuation_rate, setup_success_rate, avg_spike_points,
                        notes, last_updated
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (obs.event_type, f"{obs.event_type}_pattern", total, reversal_rate,
                      continuation_rate, setup_rate, avg_spike, notes,
                      datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))

            conn.commit()
            conn.close()

            logger.info(f"[NEWS] Pattern updated for {obs.event_type}: "
                       f"reversal={reversal_rate:.0f}%, continuation={continuation_rate:.0f}%, "
                       f"setup_rate={setup_rate:.0f}% (n={total})")

        except Exception as e:
            logger.debug(f"[NEWS] Pattern update error: {e}")

    def should_trade_post_news(self, event_type: str, minutes_after: int) -> Tuple[bool, str]:
        """Decide whether to allow trading after a news event.

        Args:
            event_type: NFP, CPI, FOMC, etc.
            minutes_after: how many minutes since the news event

        Returns:
            (allowed: bool, reason: str)
        """
        # Phase 1: Always block first 15 minutes (spike + initial volatility)
        if minutes_after < 15:
            return False, f"News spike protection (first 15 min)"

        # Phase 2: Block first 5 min always, then check patterns
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM news_patterns WHERE event_type = ?",
                (event_type,)
            ).fetchone()
            conn.close()

            if row is None:
                # Unknown event type — default to blocking for 30 min
                if minutes_after < 30:
                    return False, f"Unknown news event — blocking 30 min"
                return True, f"Unknown news event — allowing after 30 min"

            # We have pattern data
            occurrence_count = row["occurrence_count"]
            setup_rate = row["setup_success_rate"] or 0
            reversal_rate = row["reversal_rate"] or 0

            # Need at least 10 observations to start allowing
            if occurrence_count < 10:
                if minutes_after < 30:
                    return False, f"{event_type} still learning (n={occurrence_count}/10) — block 30 min"
                return True, f"{event_type} still learning (n={occurrence_count}/10) — allow after 30 min"

            # Phase 3: We have enough data — make informed decision
            # Allow trading if setup rate is decent (>40%)
            if setup_rate >= 40 and minutes_after >= 15:
                return True, f"{event_type} post-news trading allowed (setup_rate={setup_rate:.0f}%, n={occurrence_count})"

            # Block if setup rate is poor
            if setup_rate < 40:
                return False, f"{event_type} post-news setups rare ({setup_rate:.0f}%) — blocking"

            return True, f"{event_type} allowing post-news trade"

        except Exception as e:
            logger.debug(f"[NEWS] should_trade error: {e}")
            return False, f"News check error — blocking for safety"

    def get_pattern_stats(self) -> List[Dict]:
        """Get all learned news patterns."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM news_patterns ORDER BY occurrence_count DESC").fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_observation_count(self) -> int:
        """Get total number of news observations."""
        try:
            conn = sqlite3.connect(self.db_path)
            count = conn.execute("SELECT COUNT(*) FROM news_observations").fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def get_status(self) -> Dict:
        """Get current news trader status."""
        total = self.get_observation_count()
        patterns = self.get_pattern_stats()

        if total < 10:
            phase = "OBSERVE — watching news events to learn"
        elif total < 50:
            phase = "LEARN — identifying patterns per event type"
        else:
            phase = "TRADE — attempting post-news setups when profitable"

        return {
            "phase": phase,
            "total_observations": total,
            "patterns_learned": len(patterns),
            "pending_observations": len(self.pending_observations),
            "session_observations": self.observations_this_session,
        }

    def print_stats(self):
        """Print news trading stats to log."""
        status = self.get_status()
        logger.info("=" * 70)
        logger.info("[NEWS TRADER] STATUS")
        logger.info("=" * 70)
        logger.info(f"Phase: {status['phase']}")
        logger.info(f"Total observations: {status['total_observations']}")
        logger.info(f"Patterns learned: {status['patterns_learned']}")
        logger.info(f"Pending: {status['pending_observations']}")

        patterns = self.get_pattern_stats()
        if patterns:
            logger.info("\nLearned patterns:")
            for p in patterns:
                logger.info(f"  {p['event_type']}: n={p['occurrence_count']} | "
                           f"reversal={p['reversal_rate']:.0f}% | "
                           f"continuation={p['continuation_rate']:.0f}% | "
                           f"setup_rate={p['setup_success_rate']:.0f}% | "
                           f"avg_spike={p['avg_spike_points']:.0f}pts")
        else:
            logger.info("\nNo patterns learned yet — need more observations")
        logger.info("=" * 70)
