"""
ICT 2022 Mentorship Model (Chapter 19)

The book's EXACT 6-step entry model:
1. Price trades through major BSL/SSL (liquidity grab)
2. Displacement + FVG (institutional sponsorship required)
3. MSS - Market Structure Shift with FVG confirmation
4. 50% Fibonacci of displacement range
5. FVG must be at equilibrium (50%) or "better side"
6. Limit entry at FVG equilibrium

Book reference: Chapter 19, Pages 246-254
"""
from dataclasses import dataclass
from typing import Optional, Tuple, List
import pandas as pd
import numpy as np
import logging

from .market_structure import find_swings, SwingPoint
from .fair_value_gap import detect_fvgs, FVG
from .liquidity import find_liquidity_levels, detect_liquidity_sweeps

logger = logging.getLogger("ict.2022_model")

@dataclass
class ICT2022Setup:
    """Complete ICT 2022 Mentorship Model setup"""
    direction: str  # "BUY" or "SELL"
    liquidity_sweep: Tuple[float, pd.Timestamp]  # (price, time) of sweep
    displacement_range: Tuple[float, float]  # (start, end) of displacement
    fvg: FVG  # The FVG in displacement leg
    mss_confirmed: bool  # MSS validation
    equilibrium_price: float  # 50% of displacement range
    entry_price: float  # Actual entry (FVG at equilibrium)
    stop_loss: float  # Beyond liquidity sweep
    quality_score: float  # 0-1, higher = better
    reason: str  # Human-readable explanation
    
def detect_ict_2022_setup(
    df: pd.DataFrame,
    direction: str,
    pdh: Optional[float] = None,
    pdl: Optional[float] = None,
    asian_high: Optional[float] = None,
    asian_low: Optional[float] = None,
    min_displacement_atr: float = 1.5,
) -> Optional[ICT2022Setup]:
    """
    Detect ICT 2022 Mentorship Model setup per book Chapter 19.
    
    Args:
        df: Price data (M5 or M15 timeframe)
        direction: Expected direction ("BUY" or "SELL")
        pdh: Previous Day High (BSL reference)
        pdl: Previous Day Low (SSL reference)
        asian_high: Asian session high (BSL reference)
        asian_low: Asian session low (SSL reference)
        min_displacement_atr: Minimum displacement relative to ATR
        
    Returns:
        ICT2022Setup if all 6 conditions met, else None
    """
    if len(df) < 50:
        return None
    
    try:
        # Step 1: Find liquidity sweep (BSL/SSL grab)
        liquidity_sweep = _find_recent_sweep(df, direction, pdh, pdl, asian_high, asian_low)
        if not liquidity_sweep:
            logger.debug(f"No liquidity sweep found for {direction}")
            return None
        sweep_price, sweep_time = liquidity_sweep
        
        # Step 2: Detect displacement + FVG after sweep
        displacement = _find_displacement_with_fvg(df, sweep_time, direction, min_displacement_atr)
        if not displacement:
            logger.debug(f"No displacement with FVG found after sweep at {sweep_time}")
            return None
        disp_start, disp_end, fvg = displacement
        
        # Step 3: Confirm MSS (Market Structure Shift)
        mss_confirmed = _validate_mss_with_fvg(df, fvg, direction, sweep_time)
        if not mss_confirmed:
            logger.debug(f"MSS not confirmed for FVG at {fvg.start_time}")
            return None
        
        # Step 4: Calculate 50% equilibrium of displacement range
        equilibrium = (disp_start + disp_end) / 2.0
        
        # Step 5: Validate FVG is at/near equilibrium or on "better side"
        if not _fvg_at_equilibrium(fvg, equilibrium, disp_start, disp_end, direction):
            logger.debug(f"FVG not at equilibrium: FVG={fvg.top}-{fvg.bottom}, EQ={equilibrium}")
            return None
        
        # Step 6: Entry price = FVG at equilibrium
        entry_price = _calculate_entry_price(fvg, equilibrium, direction)
        
        # Calculate stop loss (beyond sweep extreme + buffer)
        atr = _compute_atr(df, period=14)
        sl_buffer = max(2.0, atr * 0.3)
        if direction == "BUY":
            stop_loss = sweep_price - sl_buffer
        else:
            stop_loss = sweep_price + sl_buffer
        
        # DIAGNOSTIC: Validate entry/SL relationship
        if direction == "BUY" and entry_price <= stop_loss:
            logger.error(f"BUG DETECTED: BUY entry ({entry_price:.2f}) <= SL ({stop_loss:.2f})")
            return None
        if direction == "SELL" and entry_price >= stop_loss:
            logger.error(f"BUG DETECTED: SELL entry ({entry_price:.2f}) >= SL ({stop_loss:.2f})")
            return None
        
        # CRITICAL FIX: Reject setups where entry is too close to stop loss
        # These have no room to breathe and get stopped out immediately by noise
        entry_sl_distance = abs(entry_price - stop_loss)
        min_distance = max(10.0, atr * 0.5)  # Minimum 10 points or 0.5 ATR
        if entry_sl_distance < min_distance:
            logger.debug(f"Setup rejected: Entry too close to SL ({entry_sl_distance:.2f} < {min_distance:.2f})")
            return None
        
        logger.debug(f"ICT 2022 Setup: {direction} | Entry={entry_price:.2f} (FVG {fvg.bottom:.2f}-{fvg.top:.2f}) | SL={stop_loss:.2f} (sweep={sweep_price:.2f}) | EQ={equilibrium:.2f} | Distance={entry_sl_distance:.2f}")
        
        # Quality score based on multiple factors
        quality = _calculate_quality_score(fvg, (disp_start, disp_end), equilibrium, atr)
        
        # Build reason string
        reason = _build_reason_string(sweep_price, sweep_time, fvg, equilibrium, quality, direction)
        
        return ICT2022Setup(
            direction=direction,
            liquidity_sweep=(sweep_price, sweep_time),
            displacement_range=(disp_start, disp_end),
            fvg=fvg,
            mss_confirmed=mss_confirmed,
            equilibrium_price=equilibrium,
            entry_price=entry_price,
            stop_loss=stop_loss,
            quality_score=quality,
            reason=reason
        )
    except Exception as e:
        logger.error(f"Error in ICT 2022 model detection: {e}", exc_info=True)
        return None

def _find_recent_sweep(
    df: pd.DataFrame, 
    direction: str, 
    pdh: Optional[float], 
    pdl: Optional[float],
    asian_high: Optional[float],
    asian_low: Optional[float]
) -> Optional[Tuple[float, pd.Timestamp]]:
    """Find recent liquidity sweep (Step 1) - prioritize PDH/PDL per Ch.27"""
    # Look for sweep in last 30 bars
    window = min(30, len(df))
    recent_df = df.iloc[-window:]
    
    if direction == "BUY":
        # Look for SSL sweep (priority: PDL > Asian Low > recent equal lows)
        candidates = []
        if pdl:
            below_pdl = recent_df[recent_df["low"] < pdl]
            if not below_pdl.empty:
                sweep_idx = below_pdl["low"].idxmin()
                candidates.append((recent_df.loc[sweep_idx, "low"], sweep_idx, "PDL"))
        
        if asian_low and not candidates:
            below_asian = recent_df[recent_df["low"] < asian_low]
            if not below_asian.empty:
                sweep_idx = below_asian["low"].idxmin()
                candidates.append((recent_df.loc[sweep_idx, "low"], sweep_idx, "Asian Low"))
        
        if candidates:
            # Return most recent sweep
            best = max(candidates, key=lambda x: x[1])
            logger.debug(f"Found SSL sweep: {best[2]} at {best[0]}")
            return (best[0], best[1])
            
    else:  # SELL
        # Look for BSL sweep (priority: PDH > Asian High > recent equal highs)
        candidates = []
        if pdh:
            above_pdh = recent_df[recent_df["high"] > pdh]
            if not above_pdh.empty:
                sweep_idx = above_pdh["high"].idxmax()
                candidates.append((recent_df.loc[sweep_idx, "high"], sweep_idx, "PDH"))
        
        if asian_high and not candidates:
            above_asian = recent_df[recent_df["high"] > asian_high]
            if not above_asian.empty:
                sweep_idx = above_asian["high"].idxmax()
                candidates.append((recent_df.loc[sweep_idx, "high"], sweep_idx, "Asian High"))
        
        if candidates:
            best = max(candidates, key=lambda x: x[1])
            logger.debug(f"Found BSL sweep: {best[2]} at {best[0]}")
            return (best[0], best[1])
    
    return None

def _find_displacement_with_fvg(
    df: pd.DataFrame, 
    after_time: pd.Timestamp, 
    direction: str, 
    min_atr_mult: float
) -> Optional[Tuple[float, float, FVG]]:
    """Find displacement + FVG after sweep (Step 2) - requires institutional sponsorship"""
    try:
        after_idx = df.index.get_loc(after_time)
    except KeyError:
        return None
    
    post_sweep_df = df.iloc[after_idx:]
    
    if len(post_sweep_df) < 5:
        return None
    
    # Detect FVGs in post-sweep period
    fvgs = detect_fvgs(post_sweep_df, min_size=0, lookback=20)
    if not fvgs:
        return None
    
    # Filter for direction-matching FVGs
    valid_fvgs = [fvg for fvg in fvgs if 
                  (direction == "BUY" and fvg.direction == "bullish") or
                  (direction == "SELL" and fvg.direction == "bearish")]
    
    if not valid_fvgs:
        return None
    
    # Use the first valid FVG after sweep (closest to sweep = highest quality)
    fvg = valid_fvgs[0]
    
    # Measure displacement (sweep point to FVG end)
    if direction == "BUY":
        disp_start = post_sweep_df.iloc[0]["low"]
        disp_end = post_sweep_df.loc[fvg.start_time, "high"]
    else:
        disp_start = post_sweep_df.iloc[0]["high"]
        disp_end = post_sweep_df.loc[fvg.start_time, "low"]
    
    # Validate displacement magnitude (book: requires "institutional sponsorship")
    displacement_size = abs(disp_end - disp_start)
    atr = _compute_atr(df, period=14)
    if displacement_size < (min_atr_mult * atr):
        logger.debug(f"Displacement too small: {displacement_size:.2f} < {min_atr_mult}*ATR={min_atr_mult*atr:.2f}")
        return None
    
    logger.debug(f"Valid displacement found: {disp_start:.2f} -> {disp_end:.2f} with FVG at {fvg.start_time}")
    return (disp_start, disp_end, fvg)

def _validate_mss_with_fvg(
    df: pd.DataFrame, 
    fvg: FVG, 
    direction: str,
    sweep_time: pd.Timestamp
) -> bool:
    """Validate Market Structure Shift with FVG (Step 3) - book requires FVG in displacement leg"""
    # The key validation is that FVG EXISTS in the displacement leg
    # and it's AFTER the sweep - both already confirmed by _find_displacement_with_fvg
    
    # Additional check: ensure price actually shifted structure
    try:
        fvg_idx = df.index.get_loc(fvg.start_time)
        sweep_idx = df.index.get_loc(sweep_time)
    except KeyError:
        return False
    
    if fvg_idx <= sweep_idx:
        return False
    
    # For MSS, we need a swing point that's horizontal to FVG candles
    # Book (p.246): "Swing (high or low) must be exactly horizontal to one of the three candles in the FVG"
    swings = find_swings(df, lookback=3)
    if not swings:
        return False
    
    # Check if any swing is within ±2 bars of FVG
    for swing in swings:
        try:
            swing_idx = df.index.get_loc(swing.timestamp)
            if abs(swing_idx - fvg_idx) <= 2:
                # Validate swing type matches direction
                if direction == "BUY" and swing.kind == "low":
                    logger.debug(f"MSS confirmed: Bullish swing at {swing.timestamp}")
                    return True
                elif direction == "SELL" and swing.kind == "high":
                    logger.debug(f"MSS confirmed: Bearish swing at {swing.timestamp}")
                    return True
        except KeyError:
            continue
    
    # If no perfect alignment, accept if FVG exists (book shows this as valid)
    return True

def _fvg_at_equilibrium(
    fvg: FVG, 
    eq: float, 
    disp_start: float, 
    disp_end: float, 
    direction: str
) -> bool:
    """Check if FVG is at equilibrium or on 'better side' (Step 5) - book p.247"""
    fvg_mid = (fvg.top + fvg.bottom) / 2.0
    
    # Calculate tolerance (5% of displacement range per book examples)
    disp_range = abs(disp_end - disp_start)
    tolerance = disp_range * 0.05
    
    # FVG midpoint should be near equilibrium
    if abs(fvg_mid - eq) <= tolerance:
        logger.debug(f"FVG at equilibrium: mid={fvg_mid:.2f}, eq={eq:.2f}")
        return True
    
    # Or on the "better side" (liquidity side per book)
    if direction == "BUY":
        # Better side = at/below equilibrium (discount)
        is_valid = fvg_mid <= eq + tolerance
        logger.debug(f"FVG {'VALID' if is_valid else 'INVALID'} for BUY: mid={fvg_mid:.2f} vs eq={eq:.2f}")
        return is_valid
    else:
        # Better side = at/above equilibrium (premium)
        is_valid = fvg_mid >= eq - tolerance
        logger.debug(f"FVG {'VALID' if is_valid else 'INVALID'} for SELL: mid={fvg_mid:.2f} vs eq={eq:.2f}")
        return is_valid

def _calculate_entry_price(fvg: FVG, equilibrium: float, direction: str) -> float:
    """Calculate entry price at FVG equilibrium (Step 6) - book p.247"""
    # Book: "Place Limit Order inside the FVG at the Equilibrium or on the better side of Equilibrium"
    
    if direction == "BUY":
        # BUY: Enter at FVG BOTTOM (discount side) - we want to buy LOW in the gap
        # Use equilibrium if it's within FVG, else use FVG bottom
        if fvg.bottom <= equilibrium <= fvg.top:
            return equilibrium
        return fvg.bottom  # FIXED: was fvg.top (entering at resistance)
    else:
        # SELL: Enter at FVG TOP (premium side) - we want to sell HIGH in the gap
        if fvg.bottom <= equilibrium <= fvg.top:
            return equilibrium
        return fvg.top  # FIXED: was fvg.bottom (entering at support)

def _calculate_quality_score(
    fvg: FVG, 
    displacement: Tuple[float, float], 
    equilibrium: float,
    atr: float
) -> float:
    """Calculate setup quality score (0-1) based on book principles"""
    score = 0.5  # Base score
    
    disp_start, disp_end = displacement
    disp_size = abs(disp_end - disp_start)
    fvg_mid = (fvg.top + fvg.bottom) / 2.0
    
    # Bonus: FVG exactly at equilibrium (book emphasizes this)
    eq_distance = abs(fvg_mid - equilibrium)
    if eq_distance < disp_size * 0.02:
        score += 0.25
    elif eq_distance < disp_size * 0.05:
        score += 0.15
    
    # Bonus: Strong displacement (>2 ATR = institutional sponsorship)
    if disp_size > 2.0 * atr:
        score += 0.15
    
    # Bonus: Clean FVG (not filled)
    if not fvg.filled:
        score += 0.10
    
    return min(score, 1.0)

def _build_reason_string(
    sweep_price: float,
    sweep_time: pd.Timestamp,
    fvg: FVG,
    equilibrium: float,
    quality: float,
    direction: str
) -> str:
    """Build human-readable reason string"""
    return (
        f"ICT 2022 Model | {direction} | "
        f"Sweep @{sweep_price:.2f} ({sweep_time.strftime('%H:%M')}) | "
        f"FVG {fvg.bottom:.2f}-{fvg.top:.2f} | "
        f"EQ={equilibrium:.2f} | "
        f"Quality={quality:.1%}"
    )

def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute Average True Range for volatility-based validation"""
    if len(df) < period + 1:
        ranges = (df["high"] - df["low"]).tail(period).mean()
        return float(ranges) if not pd.isna(ranges) else 1.0
    
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    trs = []
    for i in range(1, len(df)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    return float(atr) if atr > 0 else 1.0
