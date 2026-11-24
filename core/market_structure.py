"""
Market Structure Detection — Break of Structure (BOS) and Change of Character (CHoCH)

ICT Concept:
- BOS = continuation signal. Price breaks the previous high (in uptrend) or low (in downtrend).
- CHoCH = reversal signal. First opposite-side break after a trend.

The bot uses swing highs/lows (fractal-based) to identify structure.
"""
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
import numpy as np


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str   # "high" or "low"
    timestamp: pd.Timestamp


def find_swings(df: pd.DataFrame, lookback: int = 5) -> List[SwingPoint]:
    """Find swing highs/lows using fractal logic.
    A swing high has `lookback` lower highs on each side; vice versa for lows.
    """
    swings = []
    highs = df["high"].values
    lows = df["low"].values
    idx = df.index  # keep tz info from MT5 data (don't use .values which strips tz)

    for i in range(lookback, len(df) - lookback):
        # swing high
        is_high = all(highs[i] > highs[i - k] for k in range(1, lookback + 1)) and \
                  all(highs[i] > highs[i + k] for k in range(1, lookback + 1))
        if is_high:
            swings.append(SwingPoint(i, highs[i], "high", idx[i]))

        # swing low
        is_low = all(lows[i] < lows[i - k] for k in range(1, lookback + 1)) and \
                 all(lows[i] < lows[i + k] for k in range(1, lookback + 1))
        if is_low:
            swings.append(SwingPoint(i, lows[i], "low", idx[i]))

    return swings


@dataclass
class StructureEvent:
    type: str           # "BOS" or "CHoCH"
    direction: str      # "bullish" or "bearish"
    price: float
    timestamp: pd.Timestamp
    reference_swing: SwingPoint


def detect_structure(swings: List[SwingPoint], buffer_pips: float, pip_value: float = 1.0) -> List[StructureEvent]:
    """Detect BOS and CHoCH from a list of swing points.
    Returns events in chronological order.
    """
    events = []
    if len(swings) < 3:
        return events

    # Track the current trend: "up", "down", or None
    current_trend = None
    last_high: Optional[SwingPoint] = None
    last_low: Optional[SwingPoint] = None

    sorted_swings = sorted(swings, key=lambda s: s.index)

    for sw in sorted_swings:
        if sw.kind == "high":
            if last_high is not None and sw.price > last_high.price + buffer_pips * pip_value:
                # price broke above last swing high
                if current_trend == "up":
                    events.append(StructureEvent("BOS", "bullish", sw.price, sw.timestamp, last_high))
                elif current_trend == "down":
                    events.append(StructureEvent("CHoCH", "bullish", sw.price, sw.timestamp, last_high))
                    current_trend = "up"
                else:
                    current_trend = "up"
                    events.append(StructureEvent("BOS", "bullish", sw.price, sw.timestamp, last_high))
            last_high = sw
        else:  # low
            if last_low is not None and sw.price < last_low.price - buffer_pips * pip_value:
                if current_trend == "down":
                    events.append(StructureEvent("BOS", "bearish", sw.price, sw.timestamp, last_low))
                elif current_trend == "up":
                    events.append(StructureEvent("CHoCH", "bearish", sw.price, sw.timestamp, last_low))
                    current_trend = "down"
                else:
                    current_trend = "down"
                    events.append(StructureEvent("BOS", "bearish", sw.price, sw.timestamp, last_low))
            last_low = sw

    return events


def latest_structure_state(df: pd.DataFrame, lookback: int = 5, buffer_pips: float = 2.0) -> dict:
    """Return the latest market structure state for a given dataframe."""
    swings = find_swings(df, lookback=lookback)
    events = detect_structure(swings, buffer_pips=buffer_pips)
    if not events:
        return {"trend": "unknown", "last_event": None, "swings": swings}
    last = events[-1]
    return {
        "trend": "bullish" if last.direction == "bullish" else "bearish",
        "last_event": last,
        "swings": swings,
    }
