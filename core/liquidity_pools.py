"""
Liquidity Pools — the book's "Draws on Liquidity" (Ch.27, Ch.16, Ch.3).

The book teaches that the market is always seeking liquidity. The primary
liquidity pools, in order of importance, are:
    - Previous Day High / Low           (Ch.27 p.318)  ← easiest, most reliable
    - Previous Week High / Low          (Ch.13)
    - Previous Month High / Low
    - Asian Session High / Low          (Ch.11)
    - London Session High / Low         (Ch.11)
    - Equal Highs / Equal Lows (BSL/SSL) (Ch.3)
    - Prior 3-day swing highs/lows      (Ch.32 p.374: "previous 3 days as range")

Bullish order flow draws toward Buy-Side Liquidity (BSL = above highs).
Bearish order flow draws toward Sell-Side Liquidity (SSL = below lows).

This module is intentionally pure-function: given OHLC dataframes (daily +
intradays), it returns the relevant liquidity levels. The strategy decides
which to use as TP / sweep targets.
"""
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("ict.liquidity_pools")


@dataclass
class LiquidityPool:
    """A resting liquidity level (Draw on Liquidity)."""
    price: float
    kind: str            # "BSL" (buy-side, above) or "SSL" (sell-side, below)
    label: str           # "PDH", "PDL", "PWH", "Asian High", etc.
    timestamp: datetime  # when this level was established
    swept: bool = False


def _to_naive_utc(idx) -> pd.DatetimeIndex:
    """Normalize a DatetimeIndex to tz-naive UTC."""
    try:
        if idx.tz is not None:
            return idx.tz_convert("UTC").tz_localize(None)
    except Exception:
        pass
    return idx


def prior_day_high_low(daily_df: pd.DataFrame, days_back: int = 1) -> Optional[dict]:
    """Previous Day High/Low. Book Ch.27 p.318.
    `daily_df` is a D1 dataframe. Returns {'high':, 'low':, 'date':} for the
    completed day `days_back` trading days ago (1 = yesterday)."""
    if daily_df is None or len(daily_df) < days_back + 1:
        return None
    row = daily_df.iloc[-(days_back + 1)]
    return {
        "high": float(row["high"]),
        "low": float(row["low"]),
        "date": daily_df.index[-(days_back + 1)],
    }


def prior_n_day_range(daily_df: pd.DataFrame, n_days: int = 3) -> Optional[dict]:
    """Prior N-day high/low range. Book Ch.32 p.374: "previous 3 days as range".
    A swing high/low always consists of 3 individual bars, so 3 days is the
    natural reference window."""
    if daily_df is None or len(daily_df) < n_days + 1:
        return None
    window = daily_df.iloc[-(n_days + 1):-1]  # exclude today
    return {
        "high": float(window["high"].max()),
        "low": float(window["low"].min()),
        "start": window.index[0],
        "end": window.index[-1],
    }


def prior_week_high_low(daily_df: pd.DataFrame) -> Optional[dict]:
    """Previous Week High/Low (book Ch.13). Looks back ~7 calendar days
    aligned to week boundaries."""
    if daily_df is None or len(daily_df) < 10:
        return None
    # Group by ISO week, take the second-to-last complete week
    idx = _to_naive_utc(daily_df.index)
    df = daily_df.copy()
    df.index = idx
    df["week"] = [t.isocalendar()[1] for t in df.index]
    df["year"] = [t.isocalendar()[0] for t in df.index]
    # Find prior week = the week before the current week
    cur_week = df.iloc[-1]["week"]
    cur_year = df.iloc[-1]["year"]
    prior = df[(df["week"] != cur_week) | (df["year"] != cur_year)]
    if prior.empty:
        return None
    last_prior_week = prior.iloc[-1]["week"]
    last_prior_year = prior.iloc[-1]["year"]
    pw = prior[(prior["week"] == last_prior_week) & (prior["year"] == last_prior_year)]
    if pw.empty:
        return None
    return {
        "high": float(pw["high"].max()),
        "low": float(pw["low"].min()),
        "start": pw.index[0],
        "end": pw.index[-1],
    }


def prior_month_high_low(daily_df: pd.DataFrame) -> Optional[dict]:
    """Previous Month High/Low."""
    if daily_df is None or len(daily_df) < 30:
        return None
    idx = _to_naive_utc(daily_df.index)
    df = daily_df.copy()
    df.index = idx
    df["month"] = [t.month for t in df.index]
    df["year"] = [t.year for t in df.index]
    cur_m = df.iloc[-1]["month"]; cur_y = df.iloc[-1]["year"]
    prior = df[(df["month"] != cur_m) | (df["year"] != cur_y)]
    if prior.empty:
        return None
    lm = prior.iloc[-1]["month"]; ly = prior.iloc[-1]["year"]
    pm = prior[(prior["month"] == lm) & (prior["year"] == ly)]
    if pm.empty:
        return None
    return {
        "high": float(pm["high"].max()),
        "low": float(pm["low"].min()),
        "start": pm.index[0],
        "end": pm.index[-1],
    }


def session_high_low(intraday_df: pd.DataFrame, start_hour_ny: int, end_hour_ny: int,
                     now_utc: Optional[datetime] = None) -> Optional[dict]:
    """High/low of a session window (NY local hours) on the current day.
    Used for Asian range, London session, NY AM session, etc.
    `intraday_df` should be H1 or smaller with tz-naive UTC index."""
    from .killzones import utc_to_ny
    if intraday_df is None or intraday_df.empty:
        return None
    idx = _to_naive_utc(intraday_df.index)
    # Convert each bar's UTC time to NY local
    ny_hours = []
    ny_dates = []
    for ts in idx:
        ny = utc_to_ny(ts.to_pydatetime())
        ny_hours.append(ny.hour)
        ny_dates.append(ny.date())
    mask = np.array([(start_hour_ny <= h < end_hour_ny) for h in ny_hours])
    if not mask.any():
        return None
    # Restrict to the most recent NY date that has bars in this window
    today_ny = max(ny_dates[i] for i in range(len(ny_dates)) if mask[i])
    date_mask = np.array([d == today_ny for d in ny_dates]) & mask
    if not date_mask.any():
        return None
    sub = intraday_df.iloc[date_mask]
    return {
        "high": float(sub["high"].max()),
        "low": float(sub["low"].min()),
        "start": sub.index[0],
        "end": sub.index[-1],
        "ny_date": today_ny,
    }


def all_liquidity_pools(daily_df: pd.DataFrame, h1_df: pd.DataFrame,
                         entry_df: pd.DataFrame, current_price: float,
                         lookback_eq: int = 20, eq_tolerance: float = 0.0002) -> List[LiquidityPool]:
    """Collect ALL liquidity pools relevant for the current bar, in priority order:
    PDH, PDL, prior-3-day H/L, PWH, PWL, PMH, PML, Asian H/L, London H/L,
    equal highs/lows (BSL/SSL)."""
    pools: List[LiquidityPool] = []

    pd = prior_day_high_low(daily_df, days_back=1)
    if pd:
        pools.append(LiquidityPool(pd["high"], "BSL", "PDH", pd["date"]))
        pools.append(LiquidityPool(pd["low"], "SSL", "PDL", pd["date"]))

    p3 = prior_n_day_range(daily_df, n_days=3)
    if p3:
        pools.append(LiquidityPool(p3["high"], "BSL", "Prior3D High", p3["end"]))
        pools.append(LiquidityPool(p3["low"], "SSL", "Prior3D Low", p3["end"]))

    pw = prior_week_high_low(daily_df)
    if pw:
        pools.append(LiquidityPool(pw["high"], "BSL", "PWH", pw["end"]))
        pools.append(LiquidityPool(pw["low"], "SSL", "PWL", pw["end"]))

    pm = prior_month_high_low(daily_df)
    if pm:
        pools.append(LiquidityPool(pm["high"], "BSL", "PMH", pm["end"]))
        pools.append(LiquidityPool(pm["low"], "SSL", "PML", pm["end"]))

    # Session highs/lows (Asian 20-00 NY, London 02-05 NY) from H1
    asian = session_high_low(h1_df, 20, 24)
    if asian is None:
        # try wrap: 0:00 NY hour too
        asian0 = session_high_low(h1_df, 0, 1)
        if asian0:
            asian = asian0
    if asian:
        pools.append(LiquidityPool(asian["high"], "BSL", "Asian High", asian["end"]))
        pools.append(LiquidityPool(asian["low"], "SSL", "Asian Low", asian["end"]))

    london = session_high_low(h1_df, 2, 5)
    if london:
        pools.append(LiquidityPool(london["high"], "BSL", "London High", london["end"]))
        pools.append(LiquidityPool(london["low"], "SSL", "London Low", london["end"]))

    # Equal highs/lows (BSL/SSL) on entry timeframe
    try:
        from .liquidity import find_liquidity_levels
        eq_levels = find_liquidity_levels(entry_df, lookback=lookback_eq, tolerance=eq_tolerance)
        for lv in eq_levels:
            label = "EQH" if lv.kind == "BSL" else "EQL"
            pools.append(LiquidityPool(lv.price, lv.kind, label, lv.formed_at, swept=lv.swept))
    except Exception as e:
        logger.debug(f"equal-highs lookup failed: {e}")

    return pools


def nearest_opposing_pool(pools: List[LiquidityPool], direction: str,
                          current_price: float) -> Optional[LiquidityPool]:
    """For BUY: nearest unswept BSL above current_price.
    For SELL: nearest unswept SSL below current_price. (TP targeting.)"""
    if direction == "BUY":
        cands = [p for p in pools if p.kind == "BSL" and not p.swept and p.price > current_price]
        if not cands:
            return None
        return min(cands, key=lambda p: p.price)
    else:
        cands = [p for p in pools if p.kind == "SSL" and not p.swept and p.price < current_price]
        if not cands:
            return None
        return max(cands, key=lambda p: p.price)
