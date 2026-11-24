"""
ICT Trading Filters (Chapter 16, 18, 25, 32)

Implements book's filtering logic:
- Day-of-week filter (prefer Mon/Tue/Wed)
- News-day blocking (NFP week, FOMC)
- Dealing range premium/discount filter
- Displacement quality gate
"""
from dataclasses import dataclass
from typing import Optional, Tuple
import pandas as pd
import logging
from datetime import datetime, time

logger = logging.getLogger("ict.filters")

@dataclass
class FilterResult:
    """Filter evaluation result"""
    passed: bool
    reason: str
    severity: str  # "block", "warn", "pass"

def check_day_of_week(timestamp: pd.Timestamp, strict: bool = False) -> FilterResult:
    """
    Day-of-week filter (Ch.25, Ch.32)
    
    Book guidance:
    - Best days: Monday, Tuesday, Wednesday
    - Acceptable: Thursday (with caution)
    - Avoid: Friday (especially after Thursday met HTF objective)
    
    Args:
        timestamp: Current timestamp
        strict: If True, block Thu/Fri entirely
        
    Returns:
        FilterResult
    """
    weekday = timestamp.dayofweek  # 0=Mon, 6=Sun
    day_name = timestamp.strftime("%A")
    
    if weekday in [0, 1, 2]:  # Mon, Tue, Wed
        return FilterResult(
            passed=True,
            reason=f"{day_name} - optimal trading day",
            severity="pass"
        )
    
    if weekday == 3:  # Thursday
        if strict:
            return FilterResult(
                passed=False,
                reason=f"{day_name} - blocked in strict mode",
                severity="block"
            )
        return FilterResult(
            passed=True,
            reason=f"{day_name} - acceptable (use caution)",
            severity="warn"
        )
    
    # Friday or weekend
    return FilterResult(
        passed=False if strict else True,
        reason=f"{day_name} - high risk (book recommends avoid)",
        severity="block" if strict else "warn"
    )

def check_nfp_week(timestamp: pd.Timestamp, news_events: list) -> FilterResult:
    """
    NFP week filter (Ch.18 p.242)
    
    Book rule: "Avoid Thursday & Friday of NFP week"
    NFP = First Friday of month (Nonfarm Payroll report)
    
    Args:
        timestamp: Current timestamp
        news_events: List of upcoming news events
        
    Returns:
        FilterResult
    """
    # Check if any news event is NFP this week
    is_nfp_week = False
    for event in news_events:
        event_title = event.get("title", "")
        if "NFP" in event_title or "Nonfarm" in event_title:
            try:
                event_time = pd.to_datetime(event.get("time", event.get("datetime", "")))
                if event_time.isocalendar()[1] == timestamp.isocalendar()[1]:  # Same week
                    is_nfp_week = True
                    break
            except:
                continue
    
    if is_nfp_week and timestamp.dayofweek >= 3:  # Thursday or Friday
        return FilterResult(
            passed=False,
            reason="NFP week Thu/Fri - book blocks (Ch.18)",
            severity="block"
        )
    
    return FilterResult(passed=True, reason="Not NFP week constraint", severity="pass")

def check_fomc_day(timestamp: pd.Timestamp, news_events: list) -> FilterResult:
    """
    FOMC day filter (Ch.18 p.242)
    
    Book rule: "FOMC days are taboo for trading"
    
    Args:
        timestamp: Current timestamp
        news_events: List of upcoming news events
        
    Returns:
        FilterResult
    """
    today = timestamp.date()
    
    for event in news_events:
        if "FOMC" in event.get("title", ""):
            try:
                event_time = pd.to_datetime(event.get("time", event.get("datetime", "")))
                event_date = event_time.date()
                if event_date == today:
                    return FilterResult(
                        passed=False,
                        reason="FOMC day - book blocks all-day (Ch.18)",
                        severity="block"
                    )
            except:
                continue
    
    return FilterResult(passed=True, reason="Not FOMC day", severity="pass")

def check_monday_after_nfp(timestamp: pd.Timestamp, news_events: list) -> FilterResult:
    """
    Monday-after-NFP filter (Ch.18 p.242)
    
    Book rule: "Monday following NFP is taboo if no holiday"
    
    Args:
        timestamp: Current timestamp
        news_events: List of recent news events
        
    Returns:
        FilterResult
    """
    if timestamp.dayofweek != 0:  # Not Monday
        return FilterResult(passed=True, reason="Not Monday", severity="pass")
    
    # Check if last week had NFP
    last_week = timestamp - pd.Timedelta(days=7)
    for event in news_events:
        event_title = event.get("title", "")
        if "NFP" in event_title or "Nonfarm" in event_title:
            try:
                event_time = pd.to_datetime(event.get("time", event.get("datetime", "")))
                if last_week <= event_time <= timestamp:
                    return FilterResult(
                        passed=False,
                        reason="Monday after NFP - book blocks (Ch.18)",
                        severity="block"
                    )
            except:
                continue
    
    return FilterResult(passed=True, reason="No recent NFP", severity="pass")

def check_dealing_range_position(
    current_price: float,
    dealing_range_high: float,
    dealing_range_low: float,
    direction: str,
    strictness: float = 0.5
) -> FilterResult:
    """
    Dealing range premium/discount filter (Ch.16 p.200)
    
    Book rule: "Buy in discount, sell in premium"
    
    Args:
        current_price: Current market price
        dealing_range_high: High of dealing range
        dealing_range_low: Low of dealing range
        direction: "BUY" or "SELL"
        strictness: 0.0-1.0, how strictly to enforce (0.5 = at equilibrium is neutral)
        
    Returns:
        FilterResult
    """
    if dealing_range_high <= dealing_range_low:
        return FilterResult(passed=True, reason="Invalid dealing range", severity="warn")
    
    # Calculate position in range (0 = low, 1 = high)
    range_size = dealing_range_high - dealing_range_low
    position = (current_price - dealing_range_low) / range_size
    
    # Equilibrium = 0.5
    equilibrium = 0.5
    
    if direction == "BUY":
        # Should be in discount (below 0.5)
        if position <= equilibrium - (strictness * 0.2):
            return FilterResult(
                passed=True,
                reason=f"In discount ({position:.1%}) - valid for BUY",
                severity="pass"
            )
        elif position <= equilibrium + (strictness * 0.1):
            return FilterResult(
                passed=True,
                reason=f"Near equilibrium ({position:.1%}) - acceptable for BUY",
                severity="warn"
            )
        else:
            return FilterResult(
                passed=False,
                reason=f"In premium ({position:.1%}) - invalid for BUY",
                severity="block"
            )
    
    else:  # SELL
        # Should be in premium (above 0.5)
        if position >= equilibrium + (strictness * 0.2):
            return FilterResult(
                passed=True,
                reason=f"In premium ({position:.1%}) - valid for SELL",
                severity="pass"
            )
        elif position >= equilibrium - (strictness * 0.1):
            return FilterResult(
                passed=True,
                reason=f"Near equilibrium ({position:.1%}) - acceptable for SELL",
                severity="warn"
            )
        else:
            return FilterResult(
                passed=False,
                reason=f"In discount ({position:.1%}) - invalid for SELL",
                severity="block"
            )

def check_displacement_quality(
    displacement_size: float,
    atr: float,
    min_multiplier: float = 1.5
) -> FilterResult:
    """
    Displacement quality filter (Ch.19 p.246)
    
    Book rule: "Displacement must show institutional sponsorship"
    Measured as displacement >= N * ATR
    
    Args:
        displacement_size: Size of displacement move
        atr: Average True Range
        min_multiplier: Minimum ATR multiplier (book uses 1.5-2.0)
        
    Returns:
        FilterResult
    """
    if atr <= 0:
        return FilterResult(passed=True, reason="Invalid ATR", severity="warn")
    
    multiplier = displacement_size / atr
    
    if multiplier >= min_multiplier * 1.5:  # Strong
        return FilterResult(
            passed=True,
            reason=f"Strong displacement ({multiplier:.1f}x ATR)",
            severity="pass"
        )
    elif multiplier >= min_multiplier:  # Acceptable
        return FilterResult(
            passed=True,
            reason=f"Adequate displacement ({multiplier:.1f}x ATR)",
            severity="warn"
        )
    else:  # Weak
        return FilterResult(
            passed=False,
            reason=f"Weak displacement ({multiplier:.1f}x ATR < {min_multiplier}x)",
            severity="block"
        )

def check_historical_day_confidence(
    timestamp: pd.Timestamp,
    day_confidence: dict,
    strict: bool = False,
) -> FilterResult:
    """Override day-of-week filter with actual historical win rates from the ledger.

    If the ledger has meaningful data for the current day of week, use the
    real win rate instead of book rules.  This is Feedback Loop #3 —
    time_travel/weekly stats feed back into the live filter.

    Args:
        timestamp: Current time.
        day_confidence: Dict[int, dict] from ledger.get_day_confidence().
                        Keys are day_of_week (0=Mon..4=Fri), values have
                        win_rate, total, wins, losses.
        strict: If True, apply a higher bar (require 40%+ win rate).

    Returns:
        FilterResult
    """
    weekday = timestamp.dayofweek
    day_name = timestamp.strftime("%A")

    if weekday not in day_confidence:
        # No data yet — fall back to book defaults
        if weekday in (0, 1, 2):
            return FilterResult(passed=True, reason=f"{day_name} — book default (no historical data)", severity="pass")
        return FilterResult(passed=True, reason=f"{day_name} — book default (no historical data)", severity="warn")

    stats = day_confidence[weekday]
    wr = stats["win_rate"]
    n = stats["total"]

    # Need at least 5 trades to trust the data
    if n < 5:
        if weekday in (0, 1, 2):
            return FilterResult(passed=True, reason=f"{day_name} — {wr:.0f}% WR (n={n}, insufficient)", severity="pass")
        return FilterResult(passed=True, reason=f"{day_name} — {wr:.0f}% WR (n={n}, insufficient)", severity="warn")

    threshold = 40.0 if strict else 30.0
    label = f"{day_name} — {wr:.0f}% WR ({stats['wins']}W/{stats['losses']}L, n={n})"

    if wr >= threshold * 1.3:
        return FilterResult(passed=True, reason=f"{label} — strongly favorable", severity="pass")
    elif wr >= threshold:
        return FilterResult(passed=True, reason=f"{label} — acceptable", severity="warn")
    else:
        return FilterResult(passed=False, reason=f"{label} — below {threshold:.0f}% threshold", severity="block")


def apply_all_filters(
    timestamp: pd.Timestamp,
    current_price: float,
    direction: str,
    displacement_size: float,
    atr: float,
    dealing_range: Optional[Tuple[float, float]],
    news_events: list,
    config: dict
) -> Tuple[bool, list]:
    """
    Apply all filters and return aggregate result.
    
    Args:
        timestamp: Current time
        current_price: Current market price
        direction: "BUY" or "SELL"
        displacement_size: Size of displacement
        atr: Average True Range
        dealing_range: (low, high) of dealing range
        news_events: List of news events
        config: Filter configuration
        
    Returns:
        (passed: bool, reasons: list of str)
    """
    results = []
    
    # Day of week
    dow_result = check_day_of_week(
        timestamp, 
        strict=config.get("strict_day_filter", False)
    )
    results.append(dow_result)
    
    # NFP week
    nfp_result = check_nfp_week(timestamp, news_events)
    results.append(nfp_result)
    
    # FOMC day
    fomc_result = check_fomc_day(timestamp, news_events)
    results.append(fomc_result)
    
    # Monday after NFP
    mon_result = check_monday_after_nfp(timestamp, news_events)
    results.append(mon_result)
    
    # Dealing range position
    if dealing_range:
        dr_result = check_dealing_range_position(
            current_price,
            dealing_range[1],
            dealing_range[0],
            direction,
            strictness=config.get("dealing_range_strictness", 0.5)
        )
        results.append(dr_result)
    
    # Displacement quality
    disp_result = check_displacement_quality(
        displacement_size,
        atr,
        min_multiplier=config.get("min_displacement_atr", 1.5)
    )
    results.append(disp_result)
    
    # Aggregate: pass if NO blocks
    blocks = [r for r in results if r.severity == "block"]
    warnings = [r for r in results if r.severity == "warn"]
    
    passed = len(blocks) == 0
    
    # Build reason list
    reasons = []
    for r in results:
        if not r.passed or r.severity == "warn":
            reasons.append(f"[{r.severity.upper()}] {r.reason}")
    
    if passed and not warnings:
        reasons = ["All filters passed"]
    
    return passed, reasons
