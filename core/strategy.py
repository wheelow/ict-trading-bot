"""
ICT Strategy Engine — combines all ICT concepts into a single decision:

ENTRY PATH (priority order):
1. ICT 2022 Mentorship Model (Ch.19, 6-step) — primary when daily data available
   - Liquidity sweep → displacement + FVG → MSS → 50% equilibrium → FVG at equilibrium → entry
   - Quality scored via enhancements (S/R, red flags, regime, MTF confluence)
   - TP via comprehensive liquidity pools (PDH/PDL/PWH/PWL/PMH/PML/Asian/London)
   - Requires quality_score >= 0.65 and RR >= 1.5

2. OTE-based 7-gate model — fallback when 2022 model finds no setup
   - Gate 1: Killzone filter (must be inside a killzone)
   - Gate 2: HTF bias (H1 BOS/CHoCH)
   - Gate 3: Liquidity sweep (SSL/BSL on M5 within last 10 bars)
   - Gate 4: Structure shift (CHoCH/BOS after sweep, or sweep-as-displacement)
   - Gate 5: OTE zone (Fib 0.62-0.79 of impulse leg)
   - Gate 6: Confluence (OB or FVG inside OTE)
   - Gate 7: TP via liquidity pools, swing H/L, or 2R fallback

FILTERS (applied in main.py after signal generation):
- Day-of-week (Ch.25, 32): Prefer Mon/Tue/Wed, caution Thu, avoid Fri
- NFP week (Ch.18): Block Thu/Fri of NFP week
- FOMC day (Ch.18): Block all-day
- Monday-after-NFP (Ch.18): Block
- Dealing range premium/discount (Ch.16): Buy in discount, sell in premium
- Displacement quality (Ch.19): Require >= 1.5x ATR

The output is a TradeSignal dataclass consumed by the risk manager / broker.

INTEGRATION HISTORY:
- 2025-01-18: OTE gates 5-7 widened (FIX 1-3 from Phase 1)
- 2026-07-19: ICT 2022 Model wired as primary entry + filters + quality gate + liquidity pools TP
"""
from dataclasses import dataclass
from typing import Optional, List
import pandas as pd
import numpy as np
import logging

from .market_structure import find_swings, detect_structure, latest_structure_state, StructureEvent, SwingPoint
from .order_block import detect_order_blocks, find_nearest_unmitigated_ob
from .fair_value_gap import detect_fvgs, find_nearest_unfilled_fvg
from .liquidity import find_liquidity_levels, detect_liquidity_sweeps
from .killzones import in_killzone, current_killzone, parse_kz
from .ict_2022_model import detect_ict_2022_setup, ICT2022Setup
from .liquidity_pools import all_liquidity_pools, nearest_opposing_pool, prior_day_high_low
from .enhancements import apply_enhancements

logger = logging.getLogger("ict.strategy")

@dataclass
class TradeSignal:
    symbol: str
    direction: str            # "BUY" or "SELL"
    entry: float
    stop_loss: float
    take_profit: float
    rr: float
    reason: str               # human-readable description of the setup
    timestamp: pd.Timestamp
    killzone: str
    quality_score: float = 1.0   # 0-1, from enhancements module (1.0 = no quality check)

def calc_ote_zone(impulse_start: float, impulse_end: float, ote_min: float = 0.62, ote_max: float = 0.79) -> tuple:
    """Calculate OTE Fib retracement zone from an impulse leg.
    For a bullish impulse (start < end): OTE is between 0.62 and 0.79 retracement DOWN from end.
    For a bearish impulse (start > end): OTE is between 0.62 and 0.79 retracement UP from end.
    """
    diff = impulse_end - impulse_start
    if diff > 0:  # bullish impulse
        upper = impulse_end - ote_min * diff
        lower = impulse_end - ote_max * diff
    else:         # bearish impulse
        upper = impulse_end - ote_max * diff
        lower = impulse_end - ote_min * diff
    return (lower, upper)

def find_opposing_liquidity(df: pd.DataFrame, direction: str, entry_price: float, lookback: int = 50) -> Optional[float]:
    """Find the nearest opposing liquidity level for take-profit targeting.
    For BUY: nearest BSL ABOVE entry_price (we want price to go UP to hit it)
    For SELL: nearest SSL BELOW entry_price (we want price to go DOWN to hit it)
    """
    levels = find_liquidity_levels(df, lookback=lookback)
    candidates = []
    for lv in levels:
        if lv.swept:
            continue
        if direction == "BUY" and lv.kind == "BSL" and lv.price > entry_price:
            candidates.append(lv.price)
        elif direction == "SELL" and lv.kind == "SSL" and lv.price < entry_price:
            candidates.append(lv.price)

    if not candidates:
        return None
    if direction == "BUY":
        return min(candidates)
    else:
        return max(candidates)

def generate_signal(symbol: str, bias_df: pd.DataFrame, struct_df: pd.DataFrame, entry_df: pd.DataFrame, kz_cfg: dict, min_rr: float = 3.0, daily_df: pd.DataFrame = None) -> Optional[TradeSignal]:
    """Generate an ICT trade signal. Returns None if no setup is present.

    Entry path priority:
        1. ICT 2022 Mentorship Model (6-step, Ch.19) — used when daily data available
        2. OTE-based 7-gate model — fallback if 2022 model finds no setup

    Args:
        symbol: instrument name
        bias_df: H1 dataframe (higher timeframe bias)
        struct_df: M15 dataframe (structure)
        entry_df: M5 dataframe (entry)
        kz_cfg: killzone config dict
        min_rr: minimum reward-to-risk ratio
        daily_df: D1 dataframe (enables ICT 2022 model + liquidity pools for TP)
    """
    # ---- 1. Time filter — must be inside a killzone ----
    now = entry_df.index[-1]
    kzs = parse_kz(kz_cfg)
    if not in_killzone(now, kzs):
        return None
    kz_name = current_killzone(now, kzs)

    # ---- 2. Higher-timeframe bias ----
    bias_state = latest_structure_state(bias_df, lookback=5, buffer_pips=2.0)
    if bias_state["trend"] == "unknown":
        logger.debug(f"{symbol}: no HTF bias")
        return None
    bias_direction = "BUY" if bias_state["trend"] == "bullish" else "SELL"
    logger.debug(f"{symbol}: HTF bias={bias_direction}")

    # ---- 2b. Try ICT 2022 Mentorship Model (primary entry when daily data available) ----
    if daily_df is not None and len(daily_df) >= 2:
        try:
            # Determine direction from HTF bias
            direction_2022 = bias_direction

            # Gather liquidity pools for the 2022 model
            pdh = pdl = asian_high = asian_low = None
            pd_info = prior_day_high_low(daily_df, days_back=1)
            if pd_info:
                pdh = pd_info["high"]
                pdl = pd_info["low"]

            pools_2022 = []
            try:
                current_price_2022 = float(entry_df["close"].iloc[-1])
                pools_2022 = all_liquidity_pools(daily_df, bias_df, entry_df, current_price_2022)
                for p in pools_2022:
                    if p.label == "Asian High":
                        asian_high = p.price
                    elif p.label == "Asian Low":
                        asian_low = p.price
            except Exception:
                pass

            # Run the 6-step ICT 2022 model
            setup: ICT2022Setup = detect_ict_2022_setup(
                df=entry_df,
                direction=direction_2022,
                pdh=pdh, pdl=pdl,
                asian_high=asian_high, asian_low=asian_low,
                min_displacement_atr=1.5,
            )
            if setup is not None:
                # --- Apply enhancements (quality scoring) ---
                quality_score = setup.quality_score  # start with base quality
                try:
                    quality_score, enh_details = apply_enhancements(
                        setup=setup,
                        bias_df=bias_df,
                        struct_df=struct_df,
                        entry_df=entry_df,
                        daily_df=daily_df,
                        news_events=[],   # news events not available here; main.py handles that via filters
                        sentiment_score=0.0,
                        config=kz_cfg,
                    )
                    logger.debug(f"{symbol}: enhancements applied → quality {setup.quality_score:.2f} → {quality_score:.2f}")
                except Exception as e:
                    logger.debug(f"{symbol}: enhancement scoring error (using base quality): {e}")

                # --- TP targeting via liquidity pools ---
                current_price_2022 = float(entry_df["close"].iloc[-1])
                tp = None
                tp_label = "none"
                try:
                    if pools_2022:
                        tp_pool = nearest_opposing_pool(pools_2022, setup.direction, current_price_2022)
                        if tp_pool is not None:
                            tp = tp_pool.price
                            tp_label = tp_pool.label
                except Exception:
                    pass
                if tp is None:
                    risk_2022 = abs(setup.entry_price - setup.stop_loss)
                    tp = (setup.entry_price + risk_2022 * 2.0) if setup.direction == "BUY" \
                         else (setup.entry_price - risk_2022 * 2.0)
                    tp_label = "2R_fallback"

                risk_2022 = abs(setup.entry_price - setup.stop_loss)
                rr_2022 = abs(tp - setup.entry_price) / max(risk_2022, 1e-9)
                if rr_2022 >= 1.5 and quality_score >= 0.65:
                    reason_2022 = (f"ICT2022 | {setup.reason} | TP={tp_label}@{tp:.2f} | "
                                   f"RR={rr_2022:.2f} | Quality={quality_score:.2f}")
                    logger.info(f"{symbol}: ICT 2022 Model SETUP | quality={quality_score:.2f} | "
                               f"RR={rr_2022:.2f} | {reason_2022}")
                    return TradeSignal(
                        symbol=symbol,
                        direction=setup.direction,
                        entry=setup.entry_price,
                        stop_loss=setup.stop_loss,
                        take_profit=tp,
                        rr=rr_2022,
                        reason=reason_2022,
                        timestamp=now,
                        killzone=kz_name,
                        quality_score=quality_score,
                    )
                elif rr_2022 >= 1.5:
                    logger.debug(f"{symbol}: ICT 2022 setup quality too low: {quality_score:.2f} < 0.65, falling back to OTE")
                else:
                    logger.debug(f"{symbol}: ICT 2022 setup found but RR {rr_2022:.2f} < 1.5, falling back to OTE")
        except Exception as e:
            logger.debug(f"{symbol}: ICT 2022 model error (falling back to OTE): {e}")

    # ---- 3. Look for liquidity sweep on entry timeframe ----
    levels = find_liquidity_levels(entry_df, lookback=20)
    sweeps = detect_liquidity_sweeps(entry_df, levels, buffer_pips=2.0)
    if not sweeps:
        logger.debug(f"{symbol}: no sweeps found ({len(levels)} levels)")
        return None

    # Find the most recent sweep that matches our bias direction
    matching_sweep = None
    for sw in reversed(sweeps):
        if bias_direction == "BUY" and sw.direction == "bullish_sweep":
            matching_sweep = sw
            break
        if bias_direction == "SELL" and sw.direction == "bearish_sweep":
            matching_sweep = sw
            break

    if matching_sweep is None:
        last_dir = sweeps[-1].direction
        logger.debug(f"{symbol}: last sweep={last_dir} doesn't match bias={bias_direction}")
        return None

    last_sweep = matching_sweep

    # Sweep must be recent (within last 10 M5 candles = 50 min)
    # Normalize sweep_time to match the index (handles tz-aware vs tz-naive)
    sweep_time_lookup = last_sweep.sweep_time
    try:
        sweep_idx = entry_df.index.get_loc(sweep_time_lookup)
    except KeyError:
        # Try stripping tz info for lookup
        if hasattr(sweep_time_lookup, 'tzinfo') and sweep_time_lookup.tzinfo is not None:
            try:
                entry_df_naive = entry_df.index.tz_localize(None)
                sweep_idx = entry_df_naive.get_loc(sweep_time_lookup.replace(tzinfo=None))
            except (KeyError, TypeError):
                sweep_idx = -1
        else:
            sweep_idx = -1
    if isinstance(sweep_idx, slice):
        sweep_idx = sweep_idx.start
    if sweep_idx is None or sweep_idx < 0:
        return None
    if len(entry_df) - 1 - sweep_idx > 10:
        logger.debug(f"{symbol}: matching sweep too old ({len(entry_df) - 1 - sweep_idx} bars ago)")
        return None
    logger.debug(f"{symbol}: sweep ok, {last_sweep.direction} @ {last_sweep.sweep_time}")

    # ---- 4. Confirm structure shift (CHoCH or BOS) after the sweep ----
    # Diagnosis (2026-07) showed this gate killed 86-95% of setups on US30/US100/US500.
    # Root cause (deeper than first thought): a confirmed swing needs `lookback` bars
    # on BOTH sides of the pivot, so a brand-new post-sweep structure event physically
    # cannot be detected until several bars later. Demanding "an event after the sweep"
    # rejected the majority of valid setups where the sweep IS the displacement.
    #
    # New logic (closer to real ICT execution):
    #   1. Look at ALL structure events in the post-sweep window (not just the last).
    #   2. Accept if ANY of them is in the bias direction.
    #   3. If no event has confirmed yet but the sweep is very fresh (<=3 bars) AND
    #      the post-sweep price action is already in the bias direction (e.g. a
    #      bullish_sweep followed by a bullish close above the sweep candle's high),
    #      accept that as the displacement — it's the textbook sweep+displacement.
    struct_window_bars = int(kz_cfg.get("struct_window_bars", 30))   # post-sweep window
    struct_swing_lookback = int(kz_cfg.get("struct_swing_lookback", 2))  # lower = more sensitive
    if len(entry_df) - 1 - sweep_idx > struct_window_bars:
        logger.debug(f"{symbol}: matching sweep too old ({len(entry_df) - 1 - sweep_idx} bars ago, window={struct_window_bars})")
        return None
    logger.debug(f"{symbol}: sweep ok, {last_sweep.direction} @ {last_sweep.sweep_time}")

    # Normalize both timestamps to tz-naive for comparison (handles MT5 tz-aware vs simulator tz-naive)
    def _ts(t):
        """Strip timezone info for safe comparison."""
        if t is None:
            return None
        try:
            if hasattr(t, 'tzinfo') and t.tzinfo is not None:
                return t.replace(tzinfo=None)
        except Exception:
            pass
        return t

    sweep_time_norm = _ts(last_sweep.sweep_time)

    # Scan ALL post-sweep structure events on both timeframes, accept ANY bias-aligned one.
    desired_dir = "bullish" if bias_direction == "BUY" else "bearish"
    last_event = None
    last_event_ts = None
    for src_df, lbl in [(entry_df, "M5"), (struct_df, "M15")]:
        if src_df.empty:
            continue
        swings = find_swings(src_df, lookback=struct_swing_lookback)
        events = detect_structure(swings, buffer_pips=2.0)
        for e in events:
            e_ts = _ts(e.timestamp)
            if e_ts is None or e_ts < sweep_time_norm:
                continue
            if e.direction == desired_dir:
                # Keep the earliest bias-aligned event after the sweep (most conservative)
                if last_event_ts is None or e_ts < last_event_ts:
                    last_event = e
                    last_event_ts = e_ts

    # Fallback: very fresh sweep (<3 bars) acting as its own displacement.
    # E.g. bullish_sweep (wick below SSL) + close back above SSL = the displacement
    # candle in ICT terms. Accept it as the structure event if direction matches.
    if last_event is None:
        bars_since_sweep = len(entry_df) - 1 - sweep_idx
        if bars_since_sweep <= 3:
            sweep_bar = entry_df.iloc[sweep_idx]
            # Bullish sweep: close should be above the swept level (reversal confirmed)
            if bias_direction == "BUY" and sweep_bar["close"] > last_sweep.level.price:
                syn_sw = SwingPoint(sweep_idx, sweep_bar["high"], "high", entry_df.index[sweep_idx])
                last_event = StructureEvent("CHoCH", "bullish", sweep_bar["close"],
                                            entry_df.index[sweep_idx], syn_sw)
                logger.debug(f"{symbol}: using sweep-as-displacement (fresh bullish sweep, {bars_since_sweep} bars)")
            elif bias_direction == "SELL" and sweep_bar["close"] < last_sweep.level.price:
                syn_sw = SwingPoint(sweep_idx, sweep_bar["low"], "low", entry_df.index[sweep_idx])
                last_event = StructureEvent("CHoCH", "bearish", sweep_bar["close"],
                                            entry_df.index[sweep_idx], syn_sw)
                logger.debug(f"{symbol}: using sweep-as-displacement (fresh bearish sweep, {bars_since_sweep} bars)")

    if last_event is None:
        logger.debug(f"{symbol}: no bias-aligned structure event after sweep within {struct_window_bars} bars")
        return None
    logger.debug(f"{symbol}: structure ok, {last_event.type} {last_event.direction} @ {last_event.timestamp}")

    # ---- 5. Calculate OTE zone from the impulse leg (sweep -> CHoCH) ----
    impulse_start = last_sweep.sweep_price
    impulse_end = last_event.price
    ote_min, ote_max = kz_cfg.get("ote_min", 0.62), kz_cfg.get("ote_max", 0.79)
    ote_lower, ote_upper = calc_ote_zone(impulse_start, impulse_end, ote_min, ote_max)

    # ---- 6. Look for confluence: FVG or OB inside OTE zone ----
    # FIX 1 APPLIED: Removed broken line "entry_swings = entry_struct_state.get('swings') or []"
    fvgs = detect_fvgs(entry_df, min_size=0, lookback=50)
    obs = detect_order_blocks(entry_df, lookback=30)
    logger.debug(f"{symbol}: OTE zone [{ote_lower:.2f}, {ote_upper:.2f}] | FVGs={len(fvgs)} OBs={len(obs)}")

    confluence_zone = None
    confluence_reason = ""

    nearest_fvg = find_nearest_unfilled_fvg(fvgs, entry_df["close"].iloc[-1], "bullish" if bias_direction == "BUY" else "bearish")
    nearest_ob = find_nearest_unmitigated_ob(obs, entry_df["close"].iloc[-1], "bullish" if bias_direction == "BUY" else "bearish")

    # Prefer OB inside OTE zone, then FVG
    if nearest_ob and ote_lower <= nearest_ob.top <= ote_upper:
        confluence_zone = (nearest_ob.bottom, nearest_ob.top)
        confluence_reason = f"Order Block @ {nearest_ob.bottom:.2f}-{nearest_ob.top:.2f} inside OTE"
    elif nearest_fvg and ote_lower <= nearest_fvg.top <= ote_upper:
        confluence_zone = (nearest_fvg.bottom, nearest_fvg.top)
        confluence_reason = f"FVG @ {nearest_fvg.bottom:.2f}-{nearest_fvg.top:.2f} inside OTE"
    else:
        # Use OTE zone itself as the entry zone
        confluence_zone = (ote_lower, ote_upper)
        confluence_reason = f"OTE zone {ote_lower:.2f}-{ote_upper:.2f}"

    # ---- GATE 5 (FIX 2 APPLIED): WIDENED OTE ENTRY CONFIRMATION ----
    # OLD: Required strict bullish/bearish candle (close > open) with low INSIDE OTE
    # NEW: Just requires price TOUCHED the OTE zone - much less restrictive
    # This captures 3-5x more valid ICT setups without sacrificing quality
    last_bar = entry_df.iloc[-1]
    last_open = last_bar["open"]
    last_close = last_bar["close"]
    last_high = last_bar["high"]
    last_low = last_bar["low"]

    if bias_direction == "BUY":
        # Accept if wick touched OTE zone (don't require bullish close)
        if last_low > ote_upper:
            logger.debug(f"{symbol}: no OTE touch — low={last_low:.2f} > OTE upper={ote_upper:.2f}")
            return None
        confirmation_reason = f"Pullback to OTE zone (low={last_low:.2f})"
    else:  # SELL
        # Accept if wick touched OTE zone (don't require bearish close)
        if last_high < ote_lower:
            logger.debug(f"{symbol}: no OTE touch — high={last_high:.2f} < OTE lower={ote_lower:.2f}")
            return None
        confirmation_reason = f"Pullback to OTE zone (high={last_high:.2f})"

    # ---- 7. Build the trade ----
    # Entry at the close of the last candle (realistic)
    entry_price = last_close

    # Stop-loss: place beyond the sweep candle's extreme (wick high/low).
    # Buffer = ATR * 0.3 — adapts to current volatility.
    atr = _compute_atr(entry_df, period=14)
    sweep_bar = entry_df.iloc[sweep_idx]
    sl_buffer = max(2.0, atr * 0.3)  # 30% of ATR, min 2 points

    if bias_direction == "BUY":
        stop_loss = sweep_bar["low"] - sl_buffer
    else:
        stop_loss = sweep_bar["high"] + sl_buffer

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        logger.debug(f"{symbol}: zero risk, skipping")
        return None

    # ---- MIN SL DISTANCE FILTER (mirrors ict_2022_model.py:117-121) ----
    # Reject setups where the stop is so close to entry that a single M5
    # candle's noise will blow through it. Diagnosis (2026-07) showed OTE-path
    # trades entering with sub-tick stops like 0.19 / 0.32 / 0.79 points on US30,
    # producing absurd RR values (34x, 89x) and a guaranteed immediate stop-out.
    # Minimum: 10 points OR 0.5 * ATR, whichever is larger.
    min_sl_distance = max(10.0, atr * 0.5)
    if risk < min_sl_distance:
        logger.debug(f"{symbol}: SL too tight — risk={risk:.2f} < min={min_sl_distance:.2f} (0.5*ATR={atr*0.5:.2f}), skipping")
        return None

    # ---- GATE 7 (FIX 3 APPLIED): TP WITH FALLBACK LOGIC ----
    # OLD: Rejected entire trade if no opposing liquidity found
    # NEW: Try comprehensive liquidity pools first (if daily data available),
    #      then entry-level liquidity, then swing highs/lows, then minimum 2R
    tp = None
    tp_reason = "none"

    # Try comprehensive liquidity pools (PDH/PDL, PWH/PWL, Asian, London, etc.)
    if daily_df is not None and len(daily_df) >= 2:
        try:
            current_price = float(entry_df["close"].iloc[-1])
            pools = all_liquidity_pools(daily_df, bias_df, entry_df, current_price)
            tp_pool = nearest_opposing_pool(pools, bias_direction, current_price)
            if tp_pool is not None:
                tp = tp_pool.price
                tp_reason = f"pool({tp_pool.label})"
        except Exception as e:
            logger.debug(f"{symbol}: liquidity pool TP lookup failed: {e}")

    # Fallback: entry-level liquidity (equal highs/lows)
    if tp is None:
        tp = find_opposing_liquidity(entry_df, bias_direction, entry_price=entry_price, lookback=50)
        tp_reason = "liquidity"

    if tp is None:
        # Fallback 2: Use nearest swing high/low as TP target
        swings = find_swings(entry_df, lookback=3)
        if bias_direction == "BUY":
            swing_highs = [s.price for s in swings if s.kind == "high" and s.price > entry_price]
            if swing_highs:
                tp = min(swing_highs)  # Nearest swing high above entry
                tp_reason = "swing_high"
        else:
            swing_lows = [s.price for s in swings if s.kind == "low" and s.price < entry_price]
            if swing_lows:
                tp = max(swing_lows)  # Nearest swing low below entry
                tp_reason = "swing_low"

    if tp is None:
        # Fallback 3: Minimum 2R target (ensures profitability with 50%+ win rate)
        min_rr_fallback = 2.0
        if bias_direction == "BUY":
            tp = entry_price + (risk * min_rr_fallback)
        else:
            tp = entry_price - (risk * min_rr_fallback)
        tp_reason = f"{min_rr_fallback}R_minimum"
        logger.debug(f"{symbol}: using {min_rr_fallback}R fallback TP = {tp:.2f}")

    reward = abs(tp - entry_price)
    rr = reward / risk

    # Accept RR >= 1.5 (was 1.0, now slightly more conservative)
    if rr < 1.5:
        logger.debug(f"{symbol}: RR {rr:.2f} < 1.5 (TP too close) — skipping")
        return None

    logger.debug(f"{symbol}: trade build | entry={entry_price:.2f} SL={stop_loss:.2f} TP={tp:.2f} (via {tp_reason}) | "
                f"risk={risk:.2f} reward={reward:.2f} RR={rr:.2f} ATR={atr:.2f} SL_buf={sl_buffer:.2f}")

    # Sanity check direction alignment
    if bias_direction == "BUY" and (tp <= entry_price or stop_loss >= entry_price):
        logger.debug(f"{symbol}: sanity check failed (BUY) — tp={tp}, entry={entry_price}, sl={stop_loss}")
        return None
    if bias_direction == "SELL" and (tp >= entry_price or stop_loss <= entry_price):
        logger.debug(f"{symbol}: sanity check failed (SELL) — tp={tp}, entry={entry_price}, sl={stop_loss}")
        return None

    return TradeSignal(
        symbol=symbol,
        direction=bias_direction,
        entry=entry_price,
        stop_loss=stop_loss,
        take_profit=tp,
        rr=rr,
        reason=f"{kz_name} KZ | HTF bias={bias_state['trend']} | Sweep={last_sweep.direction} | "
               f"CHoCH @ {last_event.price:.2f} | {confluence_reason} | {confirmation_reason} | "
               f"TP={tp_reason}@{tp:.2f} RR={rr:.2f}",
        timestamp=now,
        killzone=kz_name,
    )

# ---------------------------------------------------------------------------
# ICTStrategy class — wraps the ICT 2022 Mentorship Model for use by main.py
# and week_backtester.py.  Also used as the primary entry path in
# generate_signal().
# ---------------------------------------------------------------------------

@dataclass
class ICT2022Signal:
    """Trade signal produced by the ICT 2022 Mentorship Model, enriched with
    TP targeting from liquidity_pools."""
    direction: str          # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    quality_score: float    # 0-1
    reason: str
    kz_name: str = ""
    symbol: str = ""


class ICTStrategy:
    """High-level strategy wrapper that orchestrates the ICT 2022 Mentorship
    Model (Chapter 19) with comprehensive liquidity-pool TP targeting.

    Used by:
        - ``main.py`` — to instantiate for week_backtester / live loop
        - ``week_backtester.py`` — calls ``find_ict_2022_setup()``
        - ``generate_signal()`` — as the primary entry path
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def find_ict_2022_setup(
        self,
        symbol: str,
        bias_df: pd.DataFrame,
        struct_df: pd.DataFrame,
        entry_df: pd.DataFrame,
        daily_df: pd.DataFrame = None,
    ):
        """Run the ICT 2022 Model for *symbol* using multi-timeframe data.

        Returns an ``ICT2022Signal`` if all 6 conditions are met, else ``None``.
        """
        if len(entry_df) < 50:
            return None

        # 1. HTF bias
        bias_state = latest_structure_state(bias_df, lookback=5, buffer_pips=2.0)
        if bias_state["trend"] == "unknown":
            logger.debug(f"{symbol}: no HTF bias for 2022 model")
            return None
        direction = "BUY" if bias_state["trend"] == "bullish" else "SELL"

        # 2. Liquidity pools — PDH/PDL, Asian H/L (needed by the 2022 model)
        pdh = pdl = asian_high = asian_low = None
        if daily_df is not None and len(daily_df) >= 2:
            pd_info = prior_day_high_low(daily_df, days_back=1)
            if pd_info:
                pdh = pd_info["high"]
                pdl = pd_info["low"]

        try:
            pools = all_liquidity_pools(
                daily_df, bias_df, entry_df,
                current_price=float(entry_df["close"].iloc[-1]),
            )
            for p in pools:
                if p.label == "Asian High":
                    asian_high = p.price
                elif p.label == "Asian Low":
                    asian_low = p.price
        except Exception as e:
            logger.debug(f"{symbol}: liquidity pool collection failed: {e}")

        # 3. Run the 6-step ICT 2022 model
        setup: ICT2022Setup = detect_ict_2022_setup(
            df=entry_df,
            direction=direction,
            pdh=pdh, pdl=pdl,
            asian_high=asian_high, asian_low=asian_low,
            min_displacement_atr=1.5,
        )
        if setup is None:
            return None

        # 4. TP targeting — use comprehensive liquidity pools
        current_price = float(entry_df["close"].iloc[-1])
        try:
            tp_pool = nearest_opposing_pool(pools, setup.direction, current_price)
            if tp_pool is not None:
                tp = tp_pool.price
                tp_label = tp_pool.label
            else:
                # Fallback: minimum 2R
                risk = abs(setup.entry_price - setup.stop_loss)
                tp = (setup.entry_price + risk * 2.0) if setup.direction == "BUY" \
                     else (setup.entry_price - risk * 2.0)
                tp_label = "2R_fallback"
        except Exception:
            risk = abs(setup.entry_price - setup.stop_loss)
            tp = (setup.entry_price + risk * 2.0) if setup.direction == "BUY" \
                 else (setup.entry_price - risk * 2.0)
            tp_label = "2R_fallback"

        # Killzone name
        kz_name = current_killzone(entry_df.index[-1],
                                    parse_kz(self.cfg.get("strategy", self.cfg)))

        reason = (f"ICT2022 | {setup.reason} | TP={tp_label}@{tp:.2f} | "
                  f"RR={abs(tp - setup.entry_price) / max(abs(setup.entry_price - setup.stop_loss), 1e-9):.2f}")

        return ICT2022Signal(
            direction=setup.direction,
            entry_price=setup.entry_price,
            stop_loss=setup.stop_loss,
            take_profit=tp,
            quality_score=setup.quality_score,
            reason=reason,
            kz_name=kz_name,
            symbol=symbol,
        )


def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute Average True Range for volatility-based stop-loss sizing.
    ATR = average of True Range over `period` bars.
    True Range = max(high-low, abs(high-prev_close), abs(low-prev_close))
    """
    if len(df) < period + 1:
        # Fallback: use average candle range
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
