"""
Fair Value Gap (FVG) Detection — ICT Concept

A Fair Value Gap is a 3-candle imbalance where:
- Bullish FVG: candle[i-1].high < candle[i+1].low  (gap up)
- Bearish FVG: candle[i-1].low > candle[i+1].high  (gap down)

These zones act as magnets — price tends to return to fill them before continuing.
"""
from dataclasses import dataclass
from typing import List
import pandas as pd
import numpy as np


@dataclass
class FVG:
    index: int
    start_time: pd.Timestamp
    direction: str       # "bullish" or "bearish"
    top: float           # upper boundary
    bottom: float        # lower boundary
    size: float          # gap size in price units
    filled: bool = False # whether price has returned to fill the gap


def detect_fvgs(df: pd.DataFrame, min_size: float = 0.0, lookback: int = 50) -> List[FVG]:
    """Detect unfilled FVGs in the last `lookback` candles.
    An FVG is "filled" when a later candle's body covers the entire gap.
    """
    fvgs: List[FVG] = []
    if len(df) < 3:
        return fvgs

    start = max(0, len(df) - lookback - 2)
    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    closes = df["close"].values
    idx = df.index

    for i in range(start + 1, len(df) - 1):
        # Bullish FVG: gap between candle[i-1].high and candle[i+1].low
        if lows[i + 1] > highs[i - 1]:
            top = lows[i + 1]
            bottom = highs[i - 1]
            size = top - bottom
            if size >= min_size:
                fvgs.append(FVG(
                    index=i,
                    start_time=idx[i],
                    direction="bullish",
                    top=top,
                    bottom=bottom,
                    size=size,
                ))

        # Bearish FVG: gap between candle[i-1].low and candle[i+1].high
        elif highs[i + 1] < lows[i - 1]:
            top = lows[i - 1]
            bottom = highs[i + 1]
            size = top - bottom
            if size >= min_size:
                fvgs.append(FVG(
                    index=i,
                    start_time=idx[i],
                    direction="bearish",
                    top=top,
                    bottom=bottom,
                    size=size,
                ))

    # Mark filled FVGs (any later candle whose body covered the gap)
    for fvg in fvgs:
        for j in range(fvg.index + 2, len(df)):
            body_low = min(opens[j], closes[j])
            body_high = max(opens[j], closes[j])
            if body_low <= fvg.bottom and body_high >= fvg.top:
                fvg.filled = True
                break

    return fvgs


def find_nearest_unfilled_fvg(fvgs: List[FVG], current_price: float, direction: str) -> FVG:
    """Find the nearest unfilled FVG that price might retrace into.
    For bullish setups: look for bullish FVG below current price.
    For bearish setups: look for bearish FVG above current price.
    """
    candidates = []
    for fvg in fvgs:
        if fvg.filled:
            continue
        if direction == "bullish" and fvg.direction == "bullish" and fvg.top < current_price:
            candidates.append(fvg)
        elif direction == "bearish" and fvg.direction == "bearish" and fvg.bottom > current_price:
            candidates.append(fvg)

    if not candidates:
        return None

    # Nearest to current price
    if direction == "bullish":
        return max(candidates, key=lambda f: f.top)
    else:
        return min(candidates, key=lambda f: f.bottom)
