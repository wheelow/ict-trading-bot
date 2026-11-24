"""
ICT Block Types — Breaker, Mitigation, Rejection, Propulsion (book Ch.29).

All PD array types the bot should recognize. Each is a dataclass with a common
interface so the strategy can treat them uniformly as entry confluences.

Key definitions from Ch.29:
  - Breaker Block: OB that was BROKEN by strong impulse without pullback reaction.
    Bullish Breaker: up-close candle in most recent Swing High prior to Old Low
    being violated. Key elements: Low-High-Lower Low-Higher High.
  - Mitigation Block: Failure swing (no liquidity grab). Break of Structure before
    breakout. Results in 'M' pattern. Uses ENTIRE down candle incl. wick.
  - Rejection Block: Swing H/L with long wick(s). Price runs beyond body to take
    liquidity before reversing. Entry via stop order.
  - Propulsion Block: Candle trading directly into OB, becomes support. Must never
    see Mean Threshold exceeded.
"""
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
import logging

logger = logging.getLogger("ict.blocks")


@dataclass
class BreakerBlock:
    """A failed Order Block that was broken by strong impulse (Ch.29 p.363-386).
    Bullish: Low-High-Lower Low-Higher High. The swing-high's up-close candle
    becomes the breaker when stops are taken below the old low and price breaks
    above the swing high containing the breaker candle.
    """
    index: int
    timestamp: pd.Timestamp
    direction: str       # "bullish" or "bearish"
    body_top: float
    body_bottom: float
    swing_ref_index: int
    invalidated: bool = False

    @property
    def top(self): return self.body_top
    @property
    def bottom(self): return self.body_bottom


def detect_breaker_blocks(df: pd.DataFrame, lookback: int = 50) -> List[BreakerBlock]:
    """Detect breaker blocks. Requires a swing high/low sequence + structure break.
    Bullish breaker: find sequences of Low-High-LowerLow-HigherHigh.
    The 'Higher High' candle's up-close = breaker block body.
    """
    from .market_structure import find_swings, detect_structure
    blocks = []
    if len(df) < 20:
        return blocks
    swings = find_swings(df, lookback=3)
    if len(swings) < 4:
        return blocks

    opens = df["open"].values
    closes = df["close"].values
    highs = df["high"].values
    idx = df.index

    # Walk through swings looking for Low-High-LowerLow-HigherHigh pattern
    for i in range(2, len(swings) - 1):
        # Look for bullish breaker pattern
        if (swings[i-2].kind == "low" and swings[i-1].kind == "high" and
            swings[i].kind == "low" and swings[i+1].kind == "high"):
            # Check: Low < HigherLow < High < HigherHigh
            if (swings[i].price < swings[i-2].price and
                swings[i+1].price > swings[i-1].price):
                # The swing high (i-1) contains the breaker candle
                sw = swings[i-1]
                if closes[sw.index] > opens[sw.index]:  # up-close candle
                    blocks.append(BreakerBlock(
                        index=sw.index,
                        timestamp=idx[sw.index],
                        direction="bullish",
                        body_top=max(opens[sw.index], closes[sw.index]),
                        body_bottom=min(opens[sw.index], closes[sw.index]),
                        swing_ref_index=i+1,
                    ))

    return blocks[-lookback:]


@dataclass
class MitigationBlock:
    """Failure swing — no liquidity grab (Ch.29 p.393-419).
    Break of Structure before breakout. Results in 'M' pattern.
    Uses the ENTIRE down candle including wick (ICT explicitly says this for mitigation).
    """
    index: int
    timestamp: pd.Timestamp
    direction: str       # "bullish" or "bearish"
    wick_top: float       # full candle high (mitigation uses wicks unlike OB)
    wick_bottom: float    # full candle low

    @property
    def top(self): return self.wick_top
    @property
    def bottom(self): return self.wick_bottom


@dataclass
class RejectionBlock:
    """Swing H/L with long wick(s) — liquidity taken via wick (Ch.29 p.423-453).
    One of few times ICT uses sell stop / buy stop as entry.
    Wick = acts as OB. Body = rejection zone.
    """
    index: int
    timestamp: pd.Timestamp
    direction: str
    body_top: float
    body_bottom: float
    wick_extreme: float   # the long wick side
    wick_size: float

    @property
    def top(self): return self.body_top
    @property
    def bottom(self): return self.body_bottom


def detect_rejection_blocks(df: pd.DataFrame, lookback: int = 50,
                            min_wick_body_ratio: float = 2.0) -> List[RejectionBlock]:
    """Find swing points with long wicks = rejection blocks."""
    from .market_structure import find_swings
    blocks = []
    if len(df) < 10:
        return blocks
    swings = find_swings(df, lookback=3)
    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    closes = df["close"].values
    idx = df.index

    for sw in swings:
        i = sw.index
        body_high = max(opens[i], closes[i])
        body_low = min(opens[i], closes[i])
        body_size = body_high - body_low
        if body_size < 1e-9:
            continue

        if sw.kind == "high":
            upper_wick = highs[i] - body_high
            if upper_wick / body_size >= min_wick_body_ratio:
                blocks.append(RejectionBlock(
                    index=i, timestamp=idx[i], direction="bearish",
                    body_top=body_high, body_bottom=body_low,
                    wick_extreme=highs[i], wick_size=upper_wick,
                ))
        elif sw.kind == "low":
            lower_wick = body_low - lows[i]
            if lower_wick / body_size >= min_wick_body_ratio:
                blocks.append(RejectionBlock(
                    index=i, timestamp=idx[i], direction="bullish",
                    body_top=body_high, body_bottom=body_low,
                    wick_extreme=lows[i], wick_size=lower_wick,
                ))

    return blocks[-lookback:]
