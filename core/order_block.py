"""
Order Block Detection — ICT Concept (book-aligned, body-based).

Book Ch.29 p.367-377:
  - An Order Block is "the candlestick that initiated a move via market-maker
    manipulation. A large concentration of MM limit orders around OB."
  - Bullish OB: last bearish candle before a strong bullish impulse (BOS up).
  - Bearish OB: last bullish candle before a strong bearish impulse (BOS down).
  - "Use BODY (open to close), NOT wicks. Wicks are unreliable across brokers."
  - Mean Threshold = 50% of the body. Best OB will not see price trade below
    the midpoint of the entire body (bearish) or above midpoint (bullish).
  - Look for OBs ONLY at highs and lows of the market (reversal places).

  - Stop loss: low of Bullish OB / high of Bearish OB (p.376).
  - "Last bearish candle before impulse up = BULLISH OB"
  - "Last bullish candle before impulse down = BEARISH OB"
"""
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
import numpy as np
import logging

from .market_structure import find_swings, detect_structure, SwingPoint, StructureEvent

logger = logging.getLogger("ict.order_block")


@dataclass
class OrderBlock:
    index: int
    timestamp: pd.Timestamp
    direction: str        # "bullish" (expect price to bounce up) or "bearish" (expect price to drop)
    body_top: float       # upper boundary of OB body (max of open, close)
    body_bottom: float    # lower boundary of OB body (min of open, close)
    body_size: float       # |close - open| (body only, NO wicks)
    mean_threshold: float  # 50% of body — book p.369
    wick_high: float       # full candle high (for reference only)
    wick_low: float        # full candle low (for reference only)
    mitigation: bool = False

    @property
    def top(self) -> float:
        return self.body_top

    @property
    def bottom(self) -> float:
        return self.body_bottom


def detect_order_blocks(df: pd.DataFrame, lookback: int = 50,
                        structure_events: Optional[List[StructureEvent]] = None) -> List[OrderBlock]:
    """Detect order blocks based on BOS/CHoCH events.
    
    Book-aligned (Ch.29): Uses BODY (open→close), not wicks.
    Mean Threshold = 50% of body.
    """
    obs: List[OrderBlock] = []
    if len(df) < 5:
        return obs

    if structure_events is None:
        swings = find_swings(df, lookback=5)
        structure_events = detect_structure(swings, buffer_pips=2.0)

    opens = df["open"].values
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    idx = df.index

    for ev in structure_events:
        anchor = ev.reference_swing.index if ev.reference_swing else 0
        search_start = max(0, anchor - 30)
        search_end = anchor

        if ev.direction == "bullish":
            # Bullish OB: last bearish (close < open) candle before the break
            for i in range(search_end, search_start - 1, -1):
                if closes[i] < opens[i]:
                    body_top = max(opens[i], closes[i])    # close < open, so open > close
                    body_bottom = min(opens[i], closes[i])
                    body_size = opens[i] - closes[i]
                    obs.append(OrderBlock(
                        index=i,
                        timestamp=idx[i],
                        direction="bullish",
                        body_top=body_top,
                        body_bottom=body_bottom,
                        body_size=body_size,
                        mean_threshold=body_bottom + body_size * 0.5,  # 50% of body
                        wick_high=highs[i],
                        wick_low=lows[i],
                    ))
                    break
        else:  # bearish break
            # Bearish OB: last bullish (close > open) candle before the break
            for i in range(search_end, search_start - 1, -1):
                if closes[i] > opens[i]:
                    body_top = max(opens[i], closes[i])
                    body_bottom = min(opens[i], closes[i])
                    body_size = closes[i] - opens[i]
                    obs.append(OrderBlock(
                        index=i,
                        timestamp=idx[i],
                        direction="bearish",
                        body_top=body_top,
                        body_bottom=body_bottom,
                        body_size=body_size,
                        mean_threshold=body_bottom + body_size * 0.5,
                        wick_high=highs[i],
                        wick_low=lows[i],
                    ))
                    break

    # Mark mitigation — has price traded INTO the body since creation?
    for ob in obs:
        for j in range(ob.index + 1, len(df)):
            if ob.direction == "bullish":
                # Price came back down into OB body (wick_low to body_top)
                if lows[j] <= ob.body_top and closes[j] > ob.body_bottom:
                    ob.mitigation = True
                    break
            else:
                if highs[j] >= ob.body_bottom and closes[j] < ob.body_top:
                    ob.mitigation = True
                    break

    return obs[-lookback:] if len(obs) > lookback else obs


def find_nearest_unmitigated_ob(obs: List[OrderBlock], current_price: float, direction: str) -> Optional[OrderBlock]:
    """Find nearest unmitigated OB for a given direction.
    For bullish setups: look for bullish OB body_top below current price.
    For bearish setups: look for bearish OB body_bottom above current price.
    """
    candidates = []
    for ob in obs:
        if ob.mitigation:
            continue
        if direction == "bullish" and ob.direction == "bullish" and ob.body_top < current_price:
            candidates.append(ob)
        elif direction == "bearish" and ob.direction == "bearish" and ob.body_bottom > current_price:
            candidates.append(ob)

    if not candidates:
        return None

    if direction == "bullish":
        return max(candidates, key=lambda o: o.body_top)
    else:
        return min(candidates, key=lambda o: o.body_bottom)
