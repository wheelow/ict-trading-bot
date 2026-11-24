"""
ICT Killzones — Time-based session windows (book-aligned, DST-aware).

Reference: "Unlocking Success in ICT 2022 Mentorship" Chapter 11, p.92.
The book lists 5 killzones, ALL specified in New York local time (EST/EDT):

    1. Asia                       20:00 - 00:00 NY   (p.92, p.99)
    2. London Open (LOKZ)         02:00 - 05:00 NY   (p.92, p.100)
    3. New York Open (NYOKZ)      07:00 - 10:00 NY   (p.92, p.102)
    4. London Close (LCKZ)        10:00 - 12:00 NY   (p.92, p.107)
    5. Central Bank Dealers Range 14:00 - 20:00 NY   (p.92, Ch.31 p.318)

Additional session windows used throughout the book:
    - Silver Bullet displacement  10:00 - 11:00 NY   (Ch.28 p.325)
    - NY PM Session               13:30 - 16:00 NY   (Ch.28 p.323)
    - NY Lunch Raid               12:00 - 13:30 NY   (Ch.28 p.325)
    - AM Session Range            09:30 - 12:00 NY   (Ch.28 p.326)
    - Judas Swing                 05:00 NY           (Ch.25 p.310)
    - Algorithm liquidity hunt    08:30 NY           (Ch.20 p.308)

CRITICAL: New York observes DST (UTC-5 in winter / UTC-4 in summer).
Storing killzones as fixed UTC strings — which is what the original code did —
causes them to drift by 1 hour for ~7 months of the year. This module stores
all times in NY local time and converts to UTC at runtime via zoneinfo, which
handles DST transitions automatically.

Backward compatibility: parse_kz() still accepts the old UTC-string config keys
(london_kz_start etc.) for any existing config.yaml — it ignores them and uses
the book defaults, with a one-time deprecation warning. New code should use the
explicit `killzones_ny` block in config.
"""
from dataclasses import dataclass
from typing import List, Optional
from datetime import time, datetime, timedelta
import logging

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    _NY = ZoneInfo("America/New_York")
    _UTC = ZoneInfo("UTC")
    _HAS_ZONEINFO = True
except Exception:  # pragma: no cover
    import pytz
    _NY = pytz.timezone("America/New_York")
    _UTC = pytz.UTC
    _HAS_ZONEINFO = False

logger = logging.getLogger("ict.killzones")

# ---------------------------------------------------------------------------
# Default killzones / session windows — ALL in New York local time (book p.92)
# ---------------------------------------------------------------------------
DEFAULT_KILLZONES_NY = {
    "Asia":          ("20:00", "00:00"),  # 8pm -> midnight NY (wraps midnight)
    "London":        ("02:00", "05:00"),  # London Open Kill Zone
    "NY AM":         ("07:00", "10:00"),  # New York Open Kill Zone
    "London Close":  ("10:00", "12:00"),  # London Close Kill Zone
    "CBDR":          ("14:00", "20:00"),  # Central Bank Dealers Range
}

# Session sub-windows (not all are "trade anywhere" zones, but the book flags
# them as high-probability). Exposed for the strategy to query.
DEFAULT_SESSIONS_NY = {
    "Silver Bullet": ("10:00", "11:00"),  # displacement window (Ch.28)
    "NY PM":         ("13:30", "16:00"),  # PM session (Ch.28)
    "NY Lunch":      ("12:00", "13:30"),  # lunch raid (Ch.28)
    "AM Range":      ("09:30", "12:00"),  # RTH AM (Ch.28)
}


@dataclass
class Killzone:
    """A trading window. Start/end are NY-local times (book convention)."""
    name: str
    start: time       # NY local
    end: time         # NY local

    def contains_ny(self, t: time) -> bool:
        """Does this NY-local time fall inside the window? Handles midnight wrap."""
        if self.start <= self.end:
            return self.start <= t <= self.end
        else:  # wraps midnight (e.g. Asia 20:00 -> 00:00)
            return t >= self.start or t <= self.end


# ---------------------------------------------------------------------------
# Timezone conversion helpers
# ---------------------------------------------------------------------------
def now_utc() -> datetime:
    """Current time as a tz-naive UTC datetime (matches ledger/MT5 convention)."""
    if _HAS_ZONEINFO:
        return datetime.now(_UTC).replace(tzinfo=None)
    import pytz
    return datetime.now(pytz.UTC).replace(tzinfo=None)


def now_ny() -> datetime:
    """Current time as a tz-aware NY datetime."""
    return datetime.now(_NY)


def utc_to_ny(dt_utc: datetime) -> datetime:
    """Convert a UTC datetime (naive or aware) to tz-aware NY datetime."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    return dt_utc.astimezone(_NY)


def ny_to_utc(dt_ny: datetime) -> datetime:
    """Convert a NY datetime (naive or aware) to tz-naive UTC datetime."""
    if dt_ny.tzinfo is None:
        dt_ny = dt_ny.replace(tzinfo=_NY)
    return dt_ny.astimezone(_UTC).replace(tzinfo=None)


def ny_midnight_open(now: Optional[datetime] = None) -> datetime:
    """The NY Midnight Opening Price reference (Ch.27 p.322).
    The algorithm day starts at 00:00 NY. Returns the most recent 00:00 NY as UTC naive."""
    ny = now_ny() if now is None else utc_to_ny(now)
    midnight_ny = ny.replace(hour=0, minute=0, second=0, microsecond=0)
    return ny_to_utc(midnight_ny)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_kz(cfg: dict) -> List[Killzone]:
    """Parse killzone config dict -> list of Killzone objects (NY-local times).

    Accepts EITHER the new book-aligned defaults (no config needed) OR explicit
    overrides via the `killzones_ny` block. Old-style UTC string keys
    (london_kz_start etc.) are ignored in favor of book defaults, with a
    one-time deprecation warning.
    """
    strat = cfg.get("strategy", cfg)  # tolerate being handed the strategy sub-dict
    kzs_cfg = strat.get("killzones_ny", DEFAULT_KILLZONES_NY)

    out = []
    for name, (start_str, end_str) in kzs_cfg.items():
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        out.append(Killzone(name, time(sh, sm), time(eh, em)))

    if "london_kz_start" in strat and "killzones_ny" not in strat:
        logger.warning(
            "[DEPRECATION] Config uses old UTC killzone keys (london_kz_start etc.). "
            "The book defines killzones in NY local time; the original UTC storage caused "
            "1-hour drift during DST. Switch to the 'killzones_ny' block in config.yaml. "
            "Using book defaults for now."
        )
    return out


def parse_sessions(cfg: dict) -> List[Killzone]:
    """Parse the non-killzone session windows (Silver Bullet, NY PM, etc.)."""
    strat = cfg.get("strategy", cfg)
    sessions_cfg = strat.get("sessions_ny", DEFAULT_SESSIONS_NY)
    out = []
    for name, (start_str, end_str) in sessions_cfg.items():
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        out.append(Killzone(name, time(sh, sm), time(eh, em)))
    return out


# ---------------------------------------------------------------------------
# Membership queries — accept UTC datetime (the convention used everywhere else)
# ---------------------------------------------------------------------------
def in_killzone(dt_utc: datetime, kzs: List[Killzone], tz: str = "UTC") -> bool:
    """Check whether a datetime (UTC) falls inside any killzone. DST-aware."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    ny_time = dt_utc.astimezone(_NY).time()
    return any(kz.contains_ny(ny_time) for kz in kzs)


def current_killzone(dt_utc: datetime, kzs: List[Killzone]) -> str:
    """Return the killzone name active at dt_utc (UTC), or 'Off-session'."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    ny_time = dt_utc.astimezone(_NY).time()
    for kz in kzs:
        if kz.contains_ny(ny_time):
            return kz.name
    return "Off-session"


def in_session(dt_utc: datetime, sessions: List[Killzone], name: Optional[str] = None) -> bool:
    """Check whether dt_utc is inside a session window (Silver Bullet, NY PM, ...).
    If `name` is given, only that session is checked."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    ny_time = dt_utc.astimezone(_NY).time()
    for s in sessions:
        if name and s.name != name:
            continue
        if s.contains_ny(ny_time):
            return True
    return False


# ---------------------------------------------------------------------------
# Book-specific time predicates (Ch.20, 25, 28)
# ---------------------------------------------------------------------------
def is_in_silver_bullet_window(dt_utc: datetime) -> bool:
    """Silver Bullet displacement window: 10:00-11:00 NY (Ch.28 p.325)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    t = dt_utc.astimezone(_NY).time()
    return time(10, 0) <= t <= time(11, 0)


def is_in_judas_window(dt_utc: datetime) -> bool:
    """Judas Swing typically forms at 05:00 NY (Ch.25 p.310). Returns True for the 05:00 hour."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    return dt_utc.astimezone(_NY).hour == 5


def ny_opening_price_window(dt_utc: datetime) -> bool:
    """Algorithm starts hunting liquidity at 08:30 NY (Ch.20 p.308)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    ny = dt_utc.astimezone(_NY)
    return ny.hour == 8 and ny.minute >= 30


def ny_day_of_week(dt_utc: datetime) -> int:
    """Day of week in NY time (0=Mon..6=Sun). The book treats the algorithm
    day as starting at NY midnight, so DOW must be computed in NY time too."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=_UTC)
    return dt_utc.astimezone(_NY).weekday()


# ---------------------------------------------------------------------------
# Helper for backtesting - get killzone windows for a specific date
# ---------------------------------------------------------------------------
def get_killzone_windows(date: datetime, config: dict) -> dict:
    """
    Get killzone windows for a specific date.
    
    Args:
        date: The date to get killzones for
        config: Bot configuration
    
    Returns:
        Dict mapping killzone name to (start_datetime, end_datetime)
    """
    killzones = parse_kz(config['strategy'])
    windows = {}
    
    for kz in killzones:
        # Create datetime for start
        start_dt = date.replace(
            hour=kz.start.hour,
            minute=kz.start.minute,
            second=0,
            microsecond=0
        )
        
        # Create datetime for end
        end_dt = date.replace(
            hour=kz.end.hour,
            minute=kz.end.minute,
            second=0,
            microsecond=0
        )
        
        # Handle midnight wrap (e.g., Asia 20:00 -> 00:00)
        if kz.end < kz.start:
            end_dt += timedelta(days=1)
        
        # Convert from NY time to UTC
        if _HAS_ZONEINFO:
            start_dt = start_dt.replace(tzinfo=_NY)
            end_dt = end_dt.replace(tzinfo=_NY)
            start_dt = start_dt.astimezone(_UTC).replace(tzinfo=None)
            end_dt = end_dt.astimezone(_UTC).replace(tzinfo=None)
        else:
            start_dt = _NY.localize(start_dt)
            end_dt = _NY.localize(end_dt)
            start_dt = start_dt.astimezone(_UTC).replace(tzinfo=None)
            end_dt = end_dt.astimezone(_UTC).replace(tzinfo=None)
        
        windows[kz.name] = (start_dt, end_dt)
    
    return windows
