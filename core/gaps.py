"""
Gaps — NWOG/NDOG and Liquidity Voids (book Ch.23, Ch.24).

NWOG (New Week Opening Gap): range between Friday close and Sunday open.
  - "A real dynamic fair value level" (Ch.23 p.303).
  - Up-gap = support, down-gap = resistance.
  - Treat like an FVG; the algorithm delivers price to NWOGs to offer fair value.
  - Keep last 4-5 weeks' NWOGs on chart for reference.

NDOG (New Day Opening Gap): range between 5pm close and 6pm open (RTH/Eth gap).
  - (Ch.23 p.305)

Event Horizon (Ch.23 p.304):
  - Halfway between a NWOG and the closest prior NWOG.
  - Creates a surge toward the NWOG it got close to but didn't reach.

Liquidity Void (Ch.24 p.306):
  - Sudden large-range movement with tiny/no wicks, one-directional.
  - "Price spent very little time trading at those prices."
  - Once on the other side, it's Fair Value — market will want to return.
  - Algorithm seeks to rebalance inefficiently traded ranges.
"""
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("ict.gaps")


@dataclass
class Gap:
    kind: str           # "NWOG", "NDOG", "LiquidityVoid"
    top: float
    bottom: float
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    midpoint: float = 0.0
    size: float = 0.0
    filled: bool = False
    label: str = ""      # e.g. "NWOG Jul 14-17"

    def __post_init__(self):
        self.size = self.top - self.bottom
        self.midpoint = (self.top + self.bottom) / 2.0

    def is_support(self) -> bool:
        """Up-gap (bottom < top, gap up) acts as support."""
        return self.kind in ("NWOG", "NDOG") and self.bottom > 0

    def is_resistance(self) -> bool:
        """Down-gap acts as resistance."""
        return self.kind in ("NWOG", "NDOG") and self.top > 0


def detect_nwog(daily_df: pd.DataFrame, intraday_df: Optional[pd.DataFrame] = None,
                lookback_weeks: int = 5) -> List[Gap]:
    """Detect New Week Opening Gaps.
    NWOG = range between Friday close and the next open (Sunday for forex, Monday for equities).
    On RTH equity charts (ES/NQ), use 5m bars to see the Sunday/Monday gap.
    Returns the most recent `lookback_weeks` gaps.
    """
    gaps: List[Gap] = []
    if daily_df is None or len(daily_df) < 10:
        return gaps

    # Find consecutive Fri->Mon (or Fri->Sun for 24h markets)
    idx = daily_df.index
    for i in range(1, len(daily_df)):
        try:
            dow_prev = idx[i-1].weekday()
            dow_cur = idx[i].weekday()
        except Exception:
            continue
        # If previous day was Friday (4) and current is Monday (0), or if there's
        # a gap in trading days > 2 days
        if dow_prev == 4 and dow_cur == 0:
            prev_close = float(daily_df.iloc[i-1]["close"])
            cur_open = float(daily_df.iloc[i]["open"])
            if prev_close != cur_open:
                top = max(prev_close, cur_open)
                bottom = min(prev_close, cur_open)
                if top - bottom > 0:
                    direction = "up" if cur_open > prev_close else "down"
                    gaps.append(Gap(
                        kind="NWOG",
                        top=top, bottom=bottom,
                        start_time=idx[i-1], end_time=idx[i],
                        label=f"NWOG {idx[i-1].strftime('%b %d')}-{idx[i].strftime('%b %d')} ({direction})",
                    ))
    # Return most recent
    return gaps[-lookback_weeks:] if len(gaps) > lookback_weeks else gaps


def detect_liquidity_voids(df: pd.DataFrame, lookback: int = 50,
                           min_size_atr_multiple: float = 2.0) -> List[Gap]:
    """Detect Liquidity Voids — sudden large-range candles with tiny wicks.
    A void = candle where body is > min_size_atr_multiple * ATR and
    wick-to-body ratio is very small (< 0.15). These are 'vacuums' of liquidity.
    """
    gaps: List[Gap] = []
    if df is None or len(df) < 20:
        return gaps
    sub = df.iloc[-lookback:]
    # Compute ATR
    trs = []
    for i in range(1, len(sub)):
        tr = max(
            sub["high"].iloc[i] - sub["low"].iloc[i],
            abs(sub["high"].iloc[i] - sub["close"].iloc[i-1]),
            abs(sub["low"].iloc[i] - sub["close"].iloc[i-1]),
        )
        trs.append(tr)
    atr = np.mean(trs[-14:]) if trs else 1.0
    min_body = atr * min_size_atr_multiple

    for i in range(1, len(sub)):
        body_high = max(sub["open"].iloc[i], sub["close"].iloc[i])
        body_low = min(sub["open"].iloc[i], sub["close"].iloc[i])
        body = body_high - body_low
        full_range = sub["high"].iloc[i] - sub["low"].iloc[i]
        if body >= min_body and full_range > 0:
            wick_ratio = (full_range - body) / full_range
            if wick_ratio < 0.15:  # tiny wicks = void
                direction = "bullish" if sub["close"].iloc[i] > sub["open"].iloc[i] else "bearish"
                gaps.append(Gap(
                    kind="LiquidityVoid",
                    top=body_high, bottom=body_low,
                    start_time=sub.index[i], end_time=sub.index[i],
                    label=f"LV {direction} @ {sub.index[i]}",
                    size=body,
                ))
    return gaps


def event_horizons(nwog_list: List[Gap]) -> List[Gap]:
    """Compute Event Horizons between consecutive NWOGs (Ch.23 p.304).
    Event Horizon = midpoint between two closest NWOGs.
    """
    if len(nwog_list) < 2:
        return []
    horizons = []
    for i in range(len(nwog_list) - 1):
        g1 = nwog_list[i]
        g2 = nwog_list[i + 1]
        mid_top = (g1.top + g2.top) / 2.0
        mid_bot = (g1.bottom + g2.bottom) / 2.0
        horizons.append(Gap(
            kind="EventHorizon",
            top=mid_top, bottom=mid_bot,
            start_time=g1.start_time, end_time=g2.end_time,
            label=f"EH {g1.label} <-> {g2.label}",
        ))
    return horizons


def nearest_unfilled_gap(gaps: List[Gap], current_price: float,
                        direction: str) -> Optional[Gap]:
    """Find nearest unfilled gap for retracement. Bullish setups look for
    gaps below price (support); bearish look for gaps above (resistance)."""
    cands = [g for g in gaps if not g.filled]
    if direction == "BUY":
        below = [g for g in cands if g.top < current_price]
        if not below:
            return None
        return max(below, key=lambda g: g.top)  # nearest to price
    else:
        above = [g for g in cands if g.bottom > current_price]
        if not above:
            return None
        return min(above, key=lambda g: g.bottom)
