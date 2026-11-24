"""
Dealing Range — Premium/Discount/Equilibrium (book Ch.16 p.99-103).

The book's core bias rule (p.100):
    "Are we at Premium zone? When we are at premium zone or the algorithm
     takes buy side liquidity, we anticipate price will go down.
     Are we at Discount zone? Similarly when we at discount zone or the
     algorithm takes sell side liquidity, we anticipate higher prices."

Definitions (p.101-103):
    - Equilibrium = 50% line between a swing high and a swing low.
    - Premium     = above equilibrium (sell bias).
    - Discount    = below equilibrium (buy bias).

The "Dealing Range" is the most recent significant swing-high to swing-low
(or vice versa). The book teaches nested premium/discount: each range has
its own premium/discount subdivisions.

This module computes the dealing range from a given timeframe and tells the
strategy whether the current price is in premium (sell bias) or discount
(buy bias).
"""
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd
import numpy as np
import logging

from .market_structure import find_swings

logger = logging.getLogger("ict.dealing_range")


@dataclass
class DealingRange:
    high: float
    low: float
    equilibrium: float
    swing_high_ts: pd.Timestamp
    swing_low_ts: pd.Timestamp
    direction_formed: str   # "up" if low-then-high, "down" if high-then-low

    @property
    def size(self) -> float:
        return self.high - self.low

    def position(self, price: float) -> float:
        """Position of price within the range as a 0..1 fraction.
        0 = at the low, 1 = at the high, 0.5 = equilibrium."""
        if self.size <= 0:
            return 0.5
        return (price - self.low) / self.size

    def is_premium(self, price: float) -> bool:
        """True if price is above equilibrium (sell bias zone)."""
        return price > self.equilibrium

    def is_discount(self, price: float) -> bool:
        """True if price is below equilibrium (buy bias zone)."""
        return price < self.equilibrium

    def premium_discount_pct(self, price: float) -> float:
        """+1.0 at the high (deep premium), -1.0 at the low (deep discount), 0 at equilibrium."""
        if self.size <= 0:
            return 0.0
        return 2.0 * (price - self.equilibrium) / self.size


def compute_dealing_range(df: pd.DataFrame, lookback: int = 5,
                          recent_bars: int = 100) -> Optional[DealingRange]:
    """Compute the dealing range = most recent significant swing-high to swing-low.

    Args:
        df: OHLC dataframe (typically H1 per the book's "use 1h for bias").
        lookback: fractal lookback for swing detection.
        recent_bars: only consider the last N bars (avoids stale ranges).

    Returns:
        DealingRange (high, low, equilibrium) or None if not enough swings.
    """
    if df is None or len(df) < lookback * 2 + 1:
        return None
    sub = df.iloc[-recent_bars:] if len(df) > recent_bars else df
    swings = find_swings(sub, lookback=lookback)
    if len(swings) < 2:
        return None

    # Find the most recent swing high and most recent swing low
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if not highs or not lows:
        return None
    last_high = highs[-1]
    last_low = lows[-1]

    # Direction formed: if low came first then high -> "up" leg
    direction = "up" if last_low.index < last_high.index else "down"

    high_price = float(sub["high"].iloc[last_high.index])
    low_price = float(sub["low"].iloc[last_low.index])
    if high_price <= low_price:
        return None

    return DealingRange(
        high=high_price,
        low=low_price,
        equilibrium=(high_price + low_price) / 2.0,
        swing_high_ts=sub.index[last_high.index],
        swing_low_ts=sub.index[last_low.index],
        direction_formed=direction,
    )


def fib_levels_in_range(rng: DealingRange) -> dict:
    """Standard fib levels within the dealing range (book Ch.16).
    OTE zone = 0.62-0.79 retracement. Equilibrium = 0.50."""
    size = rng.size
    return {
        "1.000": rng.high,
        "0.79": rng.high - 0.79 * size,  # OTE upper (deep discount)
        "0.705": rng.high - 0.705 * size,  # OTE sweet spot
        "0.62": rng.high - 0.62 * size,   # OTE lower
        "0.50": rng.equilibrium,
        "0.382": rng.high - 0.382 * size,
        "0.236": rng.high - 0.236 * size,
        "0.000": rng.low,
    }


def bias_from_dealing_range(rng: DealingRange, current_price: float) -> Tuple[str, float]:
    """Return (bias_direction, confidence) from premium/discount logic.
    Discount -> bullish bias. Premium -> bearish bias.
    Confidence scales with how deep into the zone price is."""
    if rng.is_discount(current_price):
        depth = (rng.equilibrium - current_price) / max(rng.size, 1e-9)
        return "bullish", min(1.0, depth * 2.0)  # 0..1
    elif rng.is_premium(current_price):
        depth = (current_price - rng.equilibrium) / max(rng.size, 1e-9)
        return "bearish", min(1.0, depth * 2.0)
    else:
        return "neutral", 0.0
