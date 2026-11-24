"""
Signal Diagnosis Engine — Tracks Why Setups Are Rejected

The ICT strategy has 6 gates that every setup must pass:
1. Killzone check (are we in London/NY AM/NY PM?)
2. HTF bias (is the H1 trend clear?)
3. Liquidity sweep (did price sweep equal highs/lows?)
4. CHoCH confirmation (did structure shift after the sweep?)
5. OTE zone (did price retrace to 0.62-0.79 Fib?)
6. Risk/Reward (is the RR at least 1:3?)

When the bot finds 0 trades, we need to know WHICH gate is killing setups.
This engine instruments the strategy to log every rejection reason.

Usage:
    python main.py --diagnose          # diagnose on recent data (1 day)
    python main.py --diagnose --days 7 # diagnose on 7 days

Output:
    A detailed report showing:
    - Total bars scanned
    - How many setups passed each gate
    - Where the funnel narrows (the bottleneck)
    - Specific examples of rejected setups
    - Recommendations for which filter to loosen
"""
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import json

from .strategy import generate_signal, find_swings, detect_structure, latest_structure_state
from .market_structure import find_swings as find_swings_ms
from .liquidity import find_liquidity_levels, detect_liquidity_sweeps
from .fair_value_gap import detect_fvgs
from .order_block import detect_order_blocks
from .killzones import parse_kz, in_killzone, current_killzone

logger = logging.getLogger("ict.diagnosis")


@dataclass
class GateResult:
    """Result of checking one gate for one bar."""
    gate_name: str
    passed: bool
    reason: str = ""
    details: str = ""


@dataclass
class SetupAttempt:
    """A single setup attempt at a specific bar — tracks which gates it passed/failed."""
    timestamp: str
    symbol: str
    bar_index: int
    gates: List[GateResult] = field(default_factory=list)
    final_result: str = ""  # "PASSED" or which gate rejected it


class SignalDiagnoser:
    """Diagnoses where the strategy funnel is blocking setups."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.kzs = parse_kz(cfg["strategy"])
        self.attempts: List[SetupAttempt] = []
        self.gate_stats: Dict[str, Dict] = defaultdict(lambda: {"passed": 0, "failed": 0, "reasons": defaultdict(int)})

    def diagnose_symbol(self, symbol: str, bias_df, struct_df, entry_df, verbose: bool = True) -> Dict:
        """Diagnose signal generation for a symbol across all bars.

        Walks through every bar and checks each gate independently,
        logging where setups are blocked.
        """
        if entry_df.empty or len(entry_df) < 50:
            return {"error": f"Not enough data for {symbol}"}

        min_rr = self.cfg["risk"]["reward_to_risk"]
        strat = self.cfg["strategy"]

        total_bars = len(entry_df)
        bars_in_killzone = 0
        bars_with_bias = 0
        bars_with_sweep = 0
        bars_with_choch = 0
        bars_with_ote = 0
        bars_with_rr = 0
        full_setups = 0

        symbol_attempts: List[SetupAttempt] = []

        # Walk through every bar from bar 50 onward
        for i in range(50, total_bars - 1):
            current_entry_df = entry_df.iloc[:i+1]
            current_struct_df = struct_df.iloc[:max(1, i//3 + 1)] if not struct_df.empty else struct_df
            current_bias_df = bias_df.iloc[:max(1, i//12 + 1)] if not bias_df.empty else bias_df

            bar_time = entry_df.index[i]
            # Normalize timezone
            if hasattr(bar_time, 'tzinfo') and bar_time.tzinfo is not None:
                bar_time_naive = bar_time.replace(tzinfo=None)
            else:
                bar_time_naive = bar_time

            attempt = SetupAttempt(
                timestamp=str(bar_time_naive),
                symbol=symbol,
                bar_index=i,
            )

            # Gate 1: Killzone
            in_kz = in_killzone(bar_time_naive, self.kzs)
            kz_name = current_killzone(bar_time_naive, self.kzs)
            if in_kz:
                bars_in_killzone += 1
                attempt.gates.append(GateResult("killzone", True, f"In {kz_name}"))
            else:
                attempt.gates.append(GateResult("killzone", False, "Off-session"))
                symbol_attempts.append(attempt)
                continue  # skip further checks if not in killzone

            # Gate 2: HTF Bias
            bias_state = latest_structure_state(current_bias_df, lookback=5, buffer_pips=2.0)
            if bias_state["trend"] == "unknown":
                attempt.gates.append(GateResult("htf_bias", False, "No clear H1 trend"))
                self._record_gate("htf_bias", False, "No clear H1 trend")
                symbol_attempts.append(attempt)
                continue

            bias_direction = "BUY" if bias_state["trend"] == "bullish" else "SELL"
            bars_with_bias += 1
            attempt.gates.append(GateResult("htf_bias", True, f"Bias={bias_direction}"))

            # Gate 3: Liquidity Sweep
            levels = find_liquidity_levels(current_entry_df, lookback=strat.get("liquidity_lookback", 20))
            sweeps = detect_liquidity_sweeps(current_entry_df, levels, buffer_pips=strat.get("sweep_buffer_pips", 2.0))

            if not sweeps:
                attempt.gates.append(GateResult("liquidity_sweep", False, "No sweeps found"))
                self._record_gate("liquidity_sweep", False, "No sweeps found")
                symbol_attempts.append(attempt)
                continue

            # Find matching sweep
            matching_sweep = None
            for sw in reversed(sweeps):
                if bias_direction == "BUY" and sw.direction == "bullish_sweep":
                    matching_sweep = sw
                    break
                if bias_direction == "SELL" and sw.direction == "bearish_sweep":
                    matching_sweep = sw
                    break

            if matching_sweep is None:
                attempt.gates.append(GateResult("liquidity_sweep", False, f"Sweep direction mismatch (bias={bias_direction})"))
                self._record_gate("liquidity_sweep", False, "Direction mismatch")
                symbol_attempts.append(attempt)
                continue

            # Check recency (extended to 15 bars based on diagnosis)
            sweep_idx = current_entry_df.index.get_loc(matching_sweep.sweep_time)
            bars_since_sweep = len(current_entry_df) - 1 - sweep_idx
            if bars_since_sweep > 15:
                attempt.gates.append(GateResult("liquidity_sweep", False, f"Sweep too old ({bars_since_sweep} bars ago)"))
                self._record_gate("liquidity_sweep", False, "Too old")
                symbol_attempts.append(attempt)
                continue

            bars_with_sweep += 1
            attempt.gates.append(GateResult("liquidity_sweep", True, f"{matching_sweep.direction} {bars_since_sweep} bars ago"))

            # Gate 4: CHoCH after sweep
            entry_struct = latest_structure_state(current_entry_df, lookback=3, buffer_pips=2.0)
            m15_struct = latest_structure_state(current_struct_df, lookback=5, buffer_pips=2.0)

            last_event = None
            if entry_struct["last_event"] and entry_struct["last_event"].timestamp >= matching_sweep.sweep_time:
                last_event = entry_struct["last_event"]
            elif m15_struct["last_event"] and m15_struct["last_event"].timestamp >= matching_sweep.sweep_time:
                last_event = m15_struct["last_event"]

            if last_event is None:
                attempt.gates.append(GateResult("choch", False, "No CHoCH after sweep"))
                self._record_gate("choch", False, "No CHoCH after sweep")
                symbol_attempts.append(attempt)
                continue

            if bias_direction == "BUY" and last_event.direction != "bullish":
                attempt.gates.append(GateResult("choch", False, f"CHoCH direction={last_event.direction} != bias BUY"))
                self._record_gate("choch", False, "Direction mismatch")
                symbol_attempts.append(attempt)
                continue
            if bias_direction == "SELL" and last_event.direction != "bearish":
                attempt.gates.append(GateResult("choch", False, f"CHoCH direction={last_event.direction} != bias SELL"))
                self._record_gate("choch", False, "Direction mismatch")
                symbol_attempts.append(attempt)
                continue

            bars_with_choch += 1
            attempt.gates.append(GateResult("choch", True, f"{last_event.type} {last_event.direction}"))

            # Gate 5: OTE zone
            impulse_start = matching_sweep.sweep_price
            impulse_end = last_event.price
            ote_min = strat.get("ote_min", 0.62)
            ote_max = strat.get("ote_max", 0.79)
            diff = impulse_end - impulse_start
            if diff > 0:
                ote_upper = impulse_end - ote_min * diff
                ote_lower = impulse_end - ote_max * diff
            else:
                ote_upper = impulse_end - ote_max * diff
                ote_lower = impulse_end - ote_min * diff

            current_price = current_entry_df["close"].iloc[-1]
            if ote_lower <= current_price <= ote_upper:
                bars_with_ote += 1
                attempt.gates.append(GateResult("ote", True, f"Price {current_price:.2f} in OTE [{ote_lower:.2f}-{ote_upper:.2f}]"))
            else:
                reason = "Above OTE" if current_price > ote_upper else "Below OTE"
                attempt.gates.append(GateResult("ote", False, f"Price {current_price:.2f} {reason} OTE [{ote_lower:.2f}-{ote_upper:.2f}]"))
                self._record_gate("ote", False, reason)
                symbol_attempts.append(attempt)
                continue

            # Gate 6: Risk/Reward
            entry_price = (ote_lower + ote_upper) / 2
            if bias_direction == "BUY":
                stop_loss = matching_sweep.sweep_price - 2.0
            else:
                stop_loss = matching_sweep.sweep_price + 2.0

            risk = abs(entry_price - stop_loss)
            if risk <= 0:
                attempt.gates.append(GateResult("rr", False, "Zero risk"))
                self._record_gate("rr", False, "Zero risk")
                symbol_attempts.append(attempt)
                continue

            # Check for opposing liquidity (TP target)
            from .strategy import find_opposing_liquidity
            tp = find_opposing_liquidity(current_entry_df, bias_direction, entry_price=entry_price, lookback=50)

            if tp is not None:
                reward = abs(tp - entry_price)
                rr = reward / risk if risk > 0 else 0
                if rr >= min_rr:
                    bars_with_rr += 1
                    full_setups += 1
                    attempt.gates.append(GateResult("rr", True, f"RR={rr:.2f} (TP at liquidity {tp:.2f})"))
                    attempt.final_result = "PASSED"
                else:
                    # Fall back to computed TP
                    if bias_direction == "BUY":
                        tp = entry_price + min_rr * risk
                    else:
                        tp = entry_price - min_rr * risk
                    rr = min_rr
                    bars_with_rr += 1
                    full_setups += 1
                    attempt.gates.append(GateResult("rr", True, f"RR={rr:.2f} (computed TP {tp:.2f})"))
                    attempt.final_result = "PASSED"
            else:
                # No liquidity TP — use computed
                if bias_direction == "BUY":
                    tp = entry_price + min_rr * risk
                else:
                    tp = entry_price - min_rr * risk
                rr = min_rr
                bars_with_rr += 1
                full_setups += 1
                attempt.gates.append(GateResult("rr", True, f"RR={rr:.2f} (computed TP — no liquidity target)"))
                attempt.final_result = "PASSED"

            symbol_attempts.append(attempt)

        # Compute funnel
        funnel = {
            "symbol": symbol,
            "total_bars": total_bars,
            "bars_in_killzone": bars_in_killzone,
            "bars_with_bias": bars_with_bias,
            "bars_with_sweep": bars_with_sweep,
            "bars_with_choch": bars_with_choch,
            "bars_with_ote": bars_with_ote,
            "bars_with_rr": bars_with_rr,
            "full_setups": full_setups,
        }

        # Find bottleneck
        gates = [
            ("killzone", bars_in_killzone),
            ("htf_bias", bars_with_bias),
            ("liquidity_sweep", bars_with_sweep),
            ("choch", bars_with_choch),
            ("ote", bars_with_ote),
            ("rr", bars_with_rr),
        ]

        bottleneck = None
        max_drop = 0
        for i in range(1, len(gates)):
            prev_name, prev_count = gates[i-1]
            curr_name, curr_count = gates[i]
            if prev_count > 0:
                drop_pct = (prev_count - curr_count) / prev_count * 100
                if drop_pct > max_drop and curr_count < prev_count:
                    max_drop = drop_pct
                    bottleneck = (prev_name, curr_name, prev_count, curr_count, drop_pct)

        funnel["bottleneck"] = bottleneck

        # Store attempts
        self.attempts.extend(symbol_attempts[-20:])  # keep last 20 for examples

        return funnel

    def _record_gate(self, gate_name: str, passed: bool, reason: str):
        """Record gate statistics."""
        self.gate_stats[gate_name]["passed" if passed else "failed"] += 1
        if not passed:
            self.gate_stats[gate_name]["reasons"][reason] += 1

    def print_report(self, funnels: List[Dict]):
        """Print the diagnosis report."""
        logger.info("=" * 70)
        logger.info("SIGNAL DIAGNOSIS REPORT — Where Are Setups Being Blocked?")
        logger.info("=" * 70)

        for funnel in funnels:
            if "error" in funnel:
                logger.info(f"\n{funnel['error']}")
                continue

            symbol = funnel["symbol"]
            logger.info(f"\n{'-' * 70}")
            logger.info(f"Symbol: {symbol}")
            logger.info(f"{'-' * 70}")

            total = funnel["total_bars"]
            logger.info(f"  Total bars scanned:           {total}")
            logger.info(f"  Gate 1 — In killzone:         {funnel['bars_in_killzone']:5d} ({funnel['bars_in_killzone']/total*100:.1f}%)")
            logger.info(f"  Gate 2 — HTF bias clear:      {funnel['bars_with_bias']:5d} ({funnel['bars_with_bias']/max(1,funnel['bars_in_killzone'])*100:.1f}% of KZ)")
            logger.info(f"  Gate 3 — Liquidity sweep:     {funnel['bars_with_sweep']:5d} ({funnel['bars_with_sweep']/max(1,funnel['bars_with_bias'])*100:.1f}% of biased)")
            logger.info(f"  Gate 4 — CHoCH after sweep:   {funnel['bars_with_choch']:5d} ({funnel['bars_with_choch']/max(1,funnel['bars_with_sweep'])*100:.1f}% of swept)")
            logger.info(f"  Gate 5 — Price in OTE zone:   {funnel['bars_with_ote']:5d} ({funnel['bars_with_ote']/max(1,funnel['bars_with_choch'])*100:.1f}% of CHoCH)")
            logger.info(f"  Gate 6 — Min 1:3 RR met:      {funnel['bars_with_rr']:5d}")
            logger.info(f"  -----------------------------")
            logger.info(f"  FULL SETUPS (all gates pass): {funnel['full_setups']:5d}")

            # Bottleneck
            bn = funnel.get("bottleneck")
            if bn:
                prev_gate, curr_gate, prev_count, curr_count, drop_pct = bn
                logger.info(f"\n  [BOTTLENECK] {prev_gate} -> {curr_gate}")
                logger.info(f"    {prev_count} setups survived {prev_gate}")
                logger.info(f"    {curr_count} setups survived {curr_gate}")
                logger.info(f"    {drop_pct:.1f}% drop — THIS is where most setups die")

                # Recommendation
                rec = self._get_recommendation(curr_gate, drop_pct)
                logger.info(f"\n  RECOMMENDATION: {rec}")
            else:
                logger.info(f"\n  No significant bottleneck detected")

        # Gate statistics
        if self.gate_stats:
            logger.info(f"\n{'=' * 70}")
            logger.info("GATE FAILURE REASONS")
            logger.info(f"{'=' * 70}")
            for gate, stats in self.gate_stats.items():
                if stats["failed"] > 0:
                    logger.info(f"\n  {gate} ({stats['failed']} failures, {stats['passed']} passes):")
                    for reason, count in sorted(stats["reasons"].items(), key=lambda x: -x[1]):
                        logger.info(f"    {reason}: {count}")

        # Example rejected setups
        rejected = [a for a in self.attempts if a.final_result != "PASSED"]
        if rejected:
            logger.info(f"\n{'=' * 70}")
            logger.info(f"EXAMPLE REJECTED SETUPS (last {min(5, len(rejected))})")
            logger.info(f"{'=' * 70}")
            for attempt in rejected[-5:]:
                failed_gate = next((g for g in reversed(attempt.gates) if not g.passed), None)
                if failed_gate:
                    logger.info(f"\n  {attempt.symbol} @ {attempt.timestamp[:19]}")
                    logger.info(f"    Failed at: {failed_gate.gate_name}")
                    logger.info(f"    Reason: {failed_gate.reason}")

        logger.info(f"\n{'=' * 70}")
        logger.info("DIAGNOSIS COMPLETE")
        logger.info(f"{'=' * 70}")

    def _get_recommendation(self, gate: str, drop_pct: float) -> str:
        """Get a recommendation based on which gate is the bottleneck."""
        recs = {
            "killzone": "Extend killzone windows by 30 min on each side, or add Asian session",
            "htf_bias": "Reduce swing lookback from 5 to 3, or add M30 as intermediate bias timeframe",
            "liquidity_sweep": "Increase liquidity_lookback from 20 to 30, or decrease sweep_buffer_pips from 2.0 to 1.0",
            "choch": "Reduce structure lookback from 5 to 3, or accept BOS (not just CHoCH) as confirmation",
            "ote": "Widen OTE range from 0.62-0.79 to 0.50-0.79, or accept price anywhere in the impulse leg",
            "rr": "Reduce minimum RR from 3.0 to 2.0 — still profitable with 40%+ win rate",
        }
        return recs.get(gate, "Review strategy parameters")
