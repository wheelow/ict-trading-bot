"""
ICT Bot Enhancements - Addresses 6 Core Limitations

This module adds intelligent layers on top of the mechanical ICT 2022 Model:
1. Quality score weighting (Discretion)
2. Support/resistance detection (Chart reading)
3. Red flag detection (Intuition)
4. Market regime detection (Adaptation)
5. News sentiment alignment (News interpretation)
6. Multi-timeframe confluence (MTF weighting)

These enhancements make the bot more "human-like" in its decision-making.
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger("ict.enhancements")

# ============================================================================
# 1. SUPPORT/RESISTANCE DETECTION (Chart Reading)
# ============================================================================

@dataclass
class KeyLevel:
    price: float
    strength: int  # How many times tested
    level_type: str  # "support" or "resistance"
    last_test: pd.Timestamp

def detect_key_levels(df: pd.DataFrame, lookback: int = 100, tolerance_pct: float = 0.002) -> List[KeyLevel]:
    """
    Detect horizontal support/resistance levels that price respects.
    
    Args:
        df: Price dataframe
        lookback: Bars to analyze
        tolerance_pct: Price clustering tolerance (0.2% default)
    
    Returns:
        List of KeyLevel objects
    """
    from core.market_structure import find_swings
    
    if len(df) < lookback:
        return []
    
    window_df = df.iloc[-lookback:]
    swings = find_swings(window_df, lookback=3)
    
    if not swings:
        return []
    
    # Cluster swing points by price
    levels = []
    processed = set()
    
    for i, swing in enumerate(swings):
        if i in processed:
            continue
        
        price = swing.price
        cluster = [swing]
        processed.add(i)
        
        # Find all swings within tolerance
        for j, other in enumerate(swings[i+1:], start=i+1):
            if abs(other.price - price) / price < tolerance_pct:
                cluster.append(other)
                processed.add(j)
        
        # Only keep levels tested 3+ times
        if len(cluster) >= 3:
            avg_price = sum(s.price for s in cluster) / len(cluster)
            level_type = "support" if cluster[0].kind == "low" else "resistance"
            last_test = max(s.timestamp for s in cluster)
            
            levels.append(KeyLevel(
                price=avg_price,
                strength=len(cluster),
                level_type=level_type,
                last_test=last_test
            ))
    
    return sorted(levels, key=lambda x: x.strength, reverse=True)

def is_near_key_level(price: float, levels: List[KeyLevel], tolerance_pct: float = 0.003) -> Optional[KeyLevel]:
    """Check if price is near a key support/resistance level"""
    for level in levels:
        if abs(price - level.price) / price < tolerance_pct:
            return level
    return None

# ============================================================================
# 2. RED FLAG DETECTION (Intuition)
# ============================================================================

def detect_red_flags(df: pd.DataFrame, setup, news_events: list) -> Tuple[float, List[str]]:
    """
    Detect warning signs that make a setup lower quality.
    
    Returns:
        (quality_penalty, list_of_flags)
    """
    flags = []
    penalty = 0.0
    
    # Red Flag 1: Choppy consolidation (reduces quality)
    if is_choppy_market(df.tail(20)):
        flags.append("choppy_consolidation")
        penalty += 0.15
        logger.debug("Red flag: Choppy market detected")
    
    # Red Flag 2: Very small FVG (< 0.3% of price)
    fvg_size_pct = (setup.fvg.size / setup.entry_price) * 100
    if fvg_size_pct < 0.3:
        flags.append("tiny_fvg")
        penalty += 0.10
        logger.debug(f"Red flag: FVG too small ({fvg_size_pct:.2f}%)")
    
    # Red Flag 3: Low volume hour (if displacement happened 12pm-2pm NY)
    hour = setup.fvg.start_time.hour
    if 12 <= hour <= 14:  # Lunch hour
        flags.append("low_volume_hour")
        penalty += 0.15
        logger.debug("Red flag: Displacement during lunch hour")
    
    # Red Flag 4: End of day (if setup forms after 3pm)
    if hour >= 15:
        flags.append("late_day_setup")
        penalty += 0.20
        logger.debug("Red flag: Setup forming late in day")
    
    # Red Flag 5: News within 1 hour
    if has_news_within_hour(setup.fvg.start_time, news_events):
        flags.append("news_proximity")
        penalty += 0.10
        logger.debug("Red flag: News event within 1 hour")
    
    return penalty, flags

def is_choppy_market(df: pd.DataFrame) -> bool:
    """Detect if recent price action is choppy/consolidating"""
    if len(df) < 10:
        return False
    
    # Calculate range compression
    highs = df['high'].values
    lows = df['low'].values
    
    total_range = max(highs) - min(lows)
    avg_candle_range = np.mean(highs - lows)
    
    # If total range is < 3x avg candle range, it's choppy
    return total_range < (avg_candle_range * 3)

def has_news_within_hour(timestamp: pd.Timestamp, news_events: list) -> bool:
    """Check if high-impact news within ±60 minutes"""
    for event in news_events:
        try:
            event_time = pd.to_datetime(event.get('time', event.get('datetime', '')))
            diff_minutes = abs((event_time - timestamp).total_seconds() / 60)
            if diff_minutes <= 60 and event.get('impact', '') == 'high':
                return True
        except:
            continue
    return False

# ============================================================================
# 3. MARKET REGIME DETECTION (Adaptation)
# ============================================================================

def detect_market_regime(df_h1: pd.DataFrame, df_d1: pd.DataFrame) -> str:
    """
    Classify current market: trending, ranging, volatile, or normal.
    
    Returns:
        "trending" - Best for ICT setups
        "ranging" - Avoid (lots of false breaks)
        "volatile" - Avoid (news-driven chaos)
        "normal" - Standard conditions
    """
    if len(df_h1) < 20 or len(df_d1) < 20:
        return "normal"
    
    # Calculate ADR (Average Daily Range)
    daily_ranges = df_d1['high'] - df_d1['low']
    adr_20 = daily_ranges.tail(20).mean()
    today_range = df_d1.iloc[-1]['high'] - df_d1.iloc[-1]['low']
    
    # Calculate trend strength (simple directional movement)
    h1_closes = df_h1['close'].tail(20)
    trend_strength = abs(h1_closes.iloc[-1] - h1_closes.iloc[0]) / h1_closes.iloc[0]
    
    # Classify
    if trend_strength > 0.015 and today_range > adr_20 * 0.8:
        return "trending"  # Strong move + normal/expanded range
    elif trend_strength < 0.005:
        return "ranging"   # Very little net movement
    elif today_range > adr_20 * 1.5:
        return "volatile"  # Abnormally large range
    else:
        return "normal"

def get_regime_min_quality(regime: str) -> float:
    """Get minimum quality score based on market regime"""
    regime_thresholds = {
        "trending": 0.65,   # More opportunities
        "normal": 0.70,     # Standard
        "ranging": 0.85,    # Only pristine setups
        "volatile": 1.00,   # Skip trading (impossible threshold)
    }
    return regime_thresholds.get(regime, 0.70)

# ============================================================================
# 4. NEWS SENTIMENT ALIGNMENT (News Interpretation)
# ============================================================================

def check_news_bias_alignment(direction: str, sentiment_score: float, news_events: list) -> float:
    """
    Bonus/penalty based on trading with or against news narrative.
    
    Args:
        direction: "BUY" or "SELL"
        sentiment_score: -1.0 to +1.0 (from VADER)
        news_events: Recent high-impact events
    
    Returns:
        Quality adjustment (-0.20 to +0.10)
    """
    # Strong bullish sentiment + buying = good
    if direction == "BUY" and sentiment_score > 0.3:
        logger.debug(f"Bonus: Trading with bullish sentiment ({sentiment_score:+.2f})")
        return 0.10
    
    # Strong bearish sentiment + selling = good
    elif direction == "SELL" and sentiment_score < -0.3:
        logger.debug(f"Bonus: Trading with bearish sentiment ({sentiment_score:+.2f})")
        return 0.10
    
    # Fighting strong sentiment = penalty
    elif (direction == "BUY" and sentiment_score < -0.5) or \
         (direction == "SELL" and sentiment_score > 0.5):
        logger.debug(f"Penalty: Fighting strong sentiment ({sentiment_score:+.2f})")
        return -0.20
    
    return 0.0

# ============================================================================
# 5. MULTI-TIMEFRAME CONFLUENCE (MTF Weighting)
# ============================================================================

def calculate_mtf_confluence(bias_df: pd.DataFrame, struct_df: pd.DataFrame, 
                             entry_df: pd.DataFrame, direction: str) -> float:
    """
    Score how well all 3 timeframes align.
    
    Returns:
        0.0 to 1.0+ (can exceed 1.0 with bonuses)
    """
    from core.market_structure import latest_structure_state, detect_structure, find_swings
    from core.fair_value_gap import detect_fvgs
    
    score = 0.0
    
    # Component 1: H1 bias alignment (40% weight)
    h1_bias = latest_structure_state(bias_df, lookback=5)
    expected_bias = "bullish" if direction == "BUY" else "bearish"
    if h1_bias['trend'] == expected_bias:
        score += 0.40
        logger.debug(f"MTF: H1 bias aligned ({h1_bias['trend']})")
    
    # Component 2: M15 structure confirmation (30% weight)
    m15_swings = find_swings(struct_df, lookback=3)
    m15_structure = detect_structure(m15_swings, buffer_pips=2.0)
    
    # Check for recent structure shift in correct direction
    if m15_structure:
        recent_events = [e for e in m15_structure 
                        if e.timestamp >= struct_df.index[-20]]
        matching_events = [e for e in recent_events 
                          if e.direction == expected_bias]
        if matching_events:
            score += 0.30
            logger.debug(f"MTF: M15 structure confirmed")
    
    # Component 3: M5 entry clean (30% weight)
    # Check if M5 has clean FVG and no conflicting signals
    m5_fvgs = detect_fvgs(entry_df, min_size=0, lookback=20)
    recent_fvgs = [fvg for fvg in m5_fvgs 
                   if fvg.start_time >= entry_df.index[-15]]
    
    # Count FVGs in correct direction
    matching_fvgs = [fvg for fvg in recent_fvgs 
                     if fvg.direction == expected_bias]
    if len(matching_fvgs) >= 1:
        score += 0.30
        logger.debug(f"MTF: M5 has {len(matching_fvgs)} aligned FVGs")
    
    # Bonus: All timeframes have FVGs in same direction
    h1_fvgs = detect_fvgs(bias_df.tail(50), min_size=0, lookback=20)
    h1_matching = [fvg for fvg in h1_fvgs 
                   if fvg.direction == expected_bias and not fvg.filled]
    
    if h1_matching and len(matching_fvgs) >= 1:
        score += 0.20
        logger.debug("MTF Bonus: FVGs aligned across all timeframes")
    
    return score

# ============================================================================
# 6. MASTER ENHANCEMENT FUNCTION
# ============================================================================

def apply_enhancements(setup, bias_df: pd.DataFrame, struct_df: pd.DataFrame, 
                      entry_df: pd.DataFrame, daily_df: pd.DataFrame,
                      news_events: list, sentiment_score: float, 
                      config: dict) -> Tuple[float, Dict]:
    """
    Apply all 6 enhancements to a valid ICT 2022 setup.
    
    Args:
        setup: ICT2022Setup object
        bias_df, struct_df, entry_df, daily_df: Multi-timeframe data
        news_events: Economic calendar events
        sentiment_score: Current market sentiment (-1 to +1)
        config: Bot configuration
    
    Returns:
        (final_quality_score, enhancement_details)
    """
    details = {
        "base_quality": setup.quality_score,
        "adjustments": {},
        "final_quality": setup.quality_score,
        "flags": [],
        "regime": "normal",
        "confluence": 0.0,
    }
    
    final_score = setup.quality_score
    
    # Enhancement 1: Support/Resistance Check
    try:
        key_levels = detect_key_levels(entry_df, lookback=100)
        near_level = is_near_key_level(setup.entry_price, key_levels)
        
        if near_level:
            if (setup.direction == "BUY" and near_level.level_type == "support") or \
               (setup.direction == "SELL" and near_level.level_type == "resistance"):
                bonus = 0.10
                final_score += bonus
                details["adjustments"]["key_level"] = f"+{bonus:.2f} (at {near_level.level_type})"
                logger.info(f"Enhancement: Entry near key {near_level.level_type} at {near_level.price:.2f}")
    except Exception as e:
        logger.warning(f"Support/resistance detection failed: {e}")
    
    # Enhancement 2: Red Flags
    try:
        red_flag_penalty, flags = detect_red_flags(entry_df, setup, news_events)
        if red_flag_penalty > 0:
            final_score -= red_flag_penalty
            details["adjustments"]["red_flags"] = f"-{red_flag_penalty:.2f}"
            details["flags"] = flags
            logger.warning(f"Red flags detected: {', '.join(flags)}")
    except Exception as e:
        logger.warning(f"Red flag detection failed: {e}")
    
    # Enhancement 3: Market Regime
    try:
        regime = detect_market_regime(bias_df, daily_df)
        details["regime"] = regime
        min_quality = get_regime_min_quality(regime)
        
        if regime == "ranging":
            final_score -= 0.10
            details["adjustments"]["regime"] = f"-0.10 (ranging market)"
        elif regime == "volatile":
            final_score -= 0.20
            details["adjustments"]["regime"] = f"-0.20 (volatile market)"
        elif regime == "trending":
            final_score += 0.05
            details["adjustments"]["regime"] = f"+0.05 (trending market)"
        
        logger.info(f"Market regime: {regime} (min quality: {min_quality:.2f})")
    except Exception as e:
        logger.warning(f"Regime detection failed: {e}")
    
    # Enhancement 4: News Sentiment Alignment
    try:
        sentiment_adj = check_news_bias_alignment(setup.direction, sentiment_score, news_events)
        if sentiment_adj != 0:
            final_score += sentiment_adj
            details["adjustments"]["sentiment"] = f"{sentiment_adj:+.2f}"
    except Exception as e:
        logger.warning(f"Sentiment alignment check failed: {e}")
    
    # Enhancement 5: MTF Confluence
    try:
        confluence = calculate_mtf_confluence(bias_df, struct_df, entry_df, setup.direction)
        details["confluence"] = confluence
        
        if confluence >= 0.90:
            bonus = 0.15
            final_score += bonus
            details["adjustments"]["mtf_confluence"] = f"+{bonus:.2f} (strong)"
        elif confluence >= 0.70:
            bonus = 0.05
            final_score += bonus
            details["adjustments"]["mtf_confluence"] = f"+{bonus:.2f} (good)"
        elif confluence < 0.50:
            penalty = 0.10
            final_score -= penalty
            details["adjustments"]["mtf_confluence"] = f"-{penalty:.2f} (weak)"
        
        logger.info(f"MTF confluence: {confluence:.2f}")
    except Exception as e:
        logger.warning(f"MTF confluence calculation failed: {e}")
    
    # Cap final score between 0 and 1
    final_score = max(0.0, min(1.0, final_score))
    details["final_quality"] = final_score
    
    logger.info(f"Final quality: {setup.quality_score:.2f} -> {final_score:.2f}")
    
    return final_score, details
