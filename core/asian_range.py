"""
Asian Range — book Ch.11 p.96-99, Ch.22 p.299-301.

The Asian session (20:00-00:00 NY) sets the day's directional context:
    - Asia consolidating (10-15 pts ES rule of thumb) -> London/NY expansion.
    - Asia trending (>15 pts ES) -> avoid London Open KZ trades; wait for NY.

The Asian range high/low become the day's first liquidity pools. The London
Judas Swing typically raids one side of the Asian range before reversing.

This module computes the Asian range from H1 data and classifies it
(consolidating vs trending) per the book's thresholds.
"""
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import logging

from .killzones import utc_to_ny, _NY
from .liquidity_pools import session_high_low

logger = logging.getLogger("ict.asian_range")

# Book p.99: "Asia rarely > 15 points (ES/SP500)". Book p.301: consolidating Asia
# is 10-15 pts ES; >15-20 = trending. For indices (US30/US100/US500) we accept a
# per-symbol threshold in price units (approx ES-equivalent scaled by volatility).
DEFAULT_TREND_THRESHOLD_PTS = {
    "US30":   120.0,   # ~ 15 ES pts * 8 (US30 ~8x ES vol)
    "US100":  90.0,    # ~ 15 ES pts * 6
    "US500":  15.0,    # ES itself
    "NAS100": 90.0,
    "SP500":  15.0,
}


@dataclass
class AsianRange:
    high: float
    low: float
    start: pd.Timestamp
    end: pd.Timestamp
    range_size: float
    classification: str   # "consolidating" | "trending" | "unknown"
    ny_date: object

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


def compute_asian_range(h1_df: pd.DataFrame, symbol: str = "US500",
                        trend_threshold_pts: Optional[float] = None) -> Optional[AsianRange]:
    """Compute the Asian session range (20:00-00:00 NY) for the most recent day.
    Returns None if insufficient data."""
    if h1_df is None or h1_df.empty:
        return None
    sess = session_high_low(h1_df, start_hour_ny=20, end_hour_ny=24)
    if sess is None:
        # Some feeds break at midnight; include the 00:00 NY bar too
        sess0 = session_high_low(h1_df, start_hour_ny=0, end_hour_ny=1)
        if sess0 is None:
            return None
        sess = sess0
    size = sess["high"] - sess["low"]
    threshold = trend_threshold_pts or DEFAULT_TREND_THRESHOLD_PTS.get(symbol, 15.0)
    if size >= threshold:
        classification = "trending"
    elif size >= threshold * 0.5:
        classification = "consolidating"
    else:
        classification = "consolidating"  # very tight = still consolidating
    return AsianRange(
        high=sess["high"],
        low=sess["low"],
        start=sess["start"],
        end=sess["end"],
        range_size=size,
        classification=classification,
        ny_date=sess.get("ny_date"),
    )


def is_asian_sweep(range_: AsianRange, current_price: float, buffer: float = 0.0) -> str:
    """Has price swept beyond the Asian range?
    Returns "above" (swept Asian High), "below" (swept Asian Low), or "inside"."""
    if current_price > range_.high + buffer:
        return "above"
    if current_price < range_.low - buffer:
        return "below"
    return "inside"
