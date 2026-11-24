"""
Liquidity Detection — ICT Concept

Liquidity = resting orders above equal highs / below equal lows.
- Buy-Side Liquidity (BSL): above equal highs (stops of shorts)
- Sell-Side Liquidity (SSL): below equal lows (stops of longs)

A "liquidity sweep" (or stop hunt) happens when price briefly penetrates the
liquidity pool then reverses — this is the classic ICT entry trigger.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple
import pandas as pd
import numpy as np


@dataclass
class LiquidityLevel:
    price: float
    kind: str            # "BSL" (buy-side, above) or "SSL" (sell-side, below)
    formed_at: pd.Timestamp
    touches: int         # number of times price tapped this level
    swept: bool = False
    swept_at: Optional[pd.Timestamp] = None


def find_liquidity_levels(df: pd.DataFrame, lookback: int = 20, tolerance: float = 0.0001) -> List[LiquidityLevel]:
    """Find equal highs and lows (liquidity pools) in the last `lookback` candles."""
    if len(df) < lookback:
        lookback = len(df)

    window = df.iloc[-lookback:]
    highs = window["high"].values
    lows = window["low"].values
    idx = window.index

    levels: List[LiquidityLevel] = []
    used_indices = set()

    # Find equal highs (BSL)
    for i in range(len(highs)):
        if i in used_indices:
            continue
        cluster = [i]
        for j in range(i + 1, len(highs)):
            if abs(highs[i] - highs[j]) <= tolerance * highs[i]:
                cluster.append(j)
                used_indices.add(j)
        if len(cluster) >= 2:
            levels.append(LiquidityLevel(
                price=float(np.mean([highs[k] for k in cluster])),
                kind="BSL",
                formed_at=idx[cluster[0]],
                touches=len(cluster),
            ))

    # Find equal lows (SSL)
    used_indices = set()
    for i in range(len(lows)):
        if i in used_indices:
            continue
        cluster = [i]
        for j in range(i + 1, len(lows)):
            if abs(lows[i] - lows[j]) <= tolerance * lows[i]:
                cluster.append(j)
                used_indices.add(j)
        if len(cluster) >= 2:
            levels.append(LiquidityLevel(
                price=float(np.mean([lows[k] for k in cluster])),
                kind="SSL",
                formed_at=idx[cluster[0]],
                touches=len(cluster),
            ))

    return levels


@dataclass
class LiquiditySweep:
    level: LiquidityLevel
    sweep_time: pd.Timestamp
    sweep_price: float
    direction: str       # "bullish_sweep" (swept SSL then reversed up) or "bearish_sweep" (swept BSL then reversed down)


def detect_liquidity_sweeps(df: pd.DataFrame, levels: List[LiquidityLevel], buffer_pips: float = 2.0) -> List[LiquiditySweep]:
    """Detect when price sweeps a liquidity level then reverses.
    A sweep = candle's wick pierces the level but closes back on the opposite side.
    """
    sweeps = []
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    idx = df.index

    for level in levels:
        for i in range(1, len(df)):
            if idx[i] <= level.formed_at:
                continue
            if level.swept:
                continue

            if level.kind == "BSL" and highs[i] > level.price + buffer_pips:
                # price pierced above BSL — check if it closed back below
                if closes[i] < level.price:
                    sweeps.append(LiquiditySweep(
                        level=level,
                        sweep_time=idx[i],
                        sweep_price=highs[i],
                        direction="bearish_sweep",
                    ))
                    level.swept = True
                    level.swept_at = idx[i]
                    break

            elif level.kind == "SSL" and lows[i] < level.price - buffer_pips:
                if closes[i] > level.price:
                    sweeps.append(LiquiditySweep(
                        level=level,
                        sweep_time=idx[i],
                        sweep_price=lows[i],
                        direction="bullish_sweep",
                    ))
                    level.swept = True
                    level.swept_at = idx[i]
                    break

    return sweeps
