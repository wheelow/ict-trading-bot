"""
Strategy Lab — Evolutionary Strategy Optimization

This is the bot's 'imagination' — it generates variants of the base ICT strategy,
tests them on historical data during idle time, and keeps the winners.

How it works:
1. Take the base strategy parameters (OTE range, killzone times, RR min, etc.)
2. Generate mutated variants (small random changes)
3. Backtest each variant on 30+ days of real data
4. Score each variant (win rate × avg RR × profit factor)
5. Keep top performers, discard losers
6. Save winners to the ledger as 'evolved strategies'
7. Over time, the bot discovers parameter combinations that outperform the base

Additionally:
- Pattern Discovery: analyzes historical data for statistical edges
  (e.g. 'US30 tends to reverse 68% of the time after sweeping Asian high
   in the first 30 min of London killzone')
- The bot can 'discover' new rules that aren't in the original ICT framework

This is NOT AGI — it's evolutionary optimization. But over weeks, the bot's
strategy evolves beyond the original rules into something tuned to reality.
"""
import random
import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import copy

from .backtester import IdleBacktester
from .memory import TradeLedger

logger = logging.getLogger("ict.strategy_lab")


@dataclass
class StrategyVariant:
    """A mutated version of the base strategy."""
    id: str
    generation: int
    parent_id: str = ""
    # Strategy parameters (the DNA)
    ote_min: float = 0.62
    ote_max: float = 0.79
    ny_am_kz_start: str = "12:00"
    ny_am_kz_end: str = "15:00"
    london_kz_start: str = "07:00"
    london_kz_end: str = "10:00"
    min_rr: float = 3.0
    fvg_lookback: int = 50
    liquidity_lookback: int = 20
    sweep_buffer_pips: float = 2.0
    bos_buffer_pips: float = 2.0
    # Fitness score (computed after backtesting)
    score: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    total_pnl: float = 0.0
    avg_rr: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    notes: str = ""


class StrategyLab:
    """Evolves the strategy through mutation and selection."""

    def __init__(self, ledger: TradeLedger, backtester: IdleBacktester, cfg: Dict):
        self.ledger = ledger
        self.backtester = backtester
        self.cfg = cfg
        self.generation = 0
        self.population: List[StrategyVariant] = []
        self.best_variant: Optional[StrategyVariant] = None
        self._load_state()
        logger.info(f"Strategy Lab initialized (generation {self.generation}, population {len(self.population)})")

    def _load_state(self):
        """Load saved strategy variants from the ledger.
        Re-validates each variant against current scoring — rejects ones that
        no longer pass (e.g. old losing strategies saved before the profitability gate)."""
        try:
            import sqlite3
            conn = sqlite3.connect(self.ledger.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM lessons WHERE category = 'strategy_variant' AND active = 1
                ORDER BY confidence DESC LIMIT 50
            """).fetchall()
            conn.close()
            rejected = 0
            for row in rows:
                try:
                    data = json.loads(row['rule'])
                    variant = StrategyVariant(**data)
                    # RE-VALIDATE: re-score and reject if it no longer passes
                    revalidated_score = self._score_variant(variant)
                    if revalidated_score <= 0:
                        rejected += 1
                        logger.info(f"[EVOLUTION] Rejecting stale variant {variant.id} (score {variant.score} -> {revalidated_score})")
                        continue
                    variant.score = revalidated_score  # update to current score
                    self.population.append(variant)
                    if self.best_variant is None or variant.score > self.best_variant.score:
                        self.best_variant = variant
                    gen = data.get('generation', 0)
                    if gen > self.generation:
                        self.generation = gen
                except Exception:
                    continue
            if rejected > 0:
                logger.info(f"[EVOLUTION] Rejected {rejected} stale variant(s) on load")
        except Exception as e:
            logger.debug(f"Could not load strategy state: {e}")

    def _save_variant(self, variant: StrategyVariant):
        """Save a strategy variant to the ledger."""
        try:
            self.ledger.save_lesson(
                category="strategy_variant",
                rule=json.dumps(asdict(variant)),
                confidence=variant.score,
                sample_size=variant.total_trades
            )
        except Exception as e:
            logger.debug(f"Could not save variant: {e}")

    def _generate_base_variant(self) -> StrategyVariant:
        """Generate the base strategy variant from current config."""
        strat = self.cfg["strategy"]
        risk = self.cfg["risk"]
        return StrategyVariant(
            id="base",
            generation=0,
            ote_min=strat.get("ote_min", 0.62),
            ote_max=strat.get("ote_max", 0.79),
            ny_am_kz_start=strat.get("ny_am_kz_start", "12:00"),
            ny_am_kz_end=strat.get("ny_am_kz_end", "15:00"),
            london_kz_start=strat.get("london_kz_start", "07:00"),
            london_kz_end=strat.get("london_kz_end", "10:00"),
            min_rr=risk.get("reward_to_risk", 3.0),
            fvg_lookback=strat.get("fvg_lookback", 50),
            liquidity_lookback=strat.get("liquidity_lookback", 20),
            sweep_buffer_pips=strat.get("sweep_buffer_pips", 2.0),
            bos_buffer_pips=strat.get("bos_buffer_pips", 2.0),
        )

    def _mutate(self, parent: StrategyVariant, mutation_rate: float = 0.3) -> StrategyVariant:
        """Create a mutated child of a parent variant."""
        child = StrategyVariant(
            id=f"gen{self.generation+1}_{random.randint(1000, 9999)}",
            generation=self.generation + 1,
            parent_id=parent.id,
            ote_min=parent.ote_min,
            ote_max=parent.ote_max,
            ny_am_kz_start=parent.ny_am_kz_start,
            ny_am_kz_end=parent.ny_am_kz_end,
            london_kz_start=parent.london_kz_start,
            london_kz_end=parent.london_kz_end,
            min_rr=parent.min_rr,
            fvg_lookback=parent.fvg_lookback,
            liquidity_lookback=parent.liquidity_lookback,
            sweep_buffer_pips=parent.sweep_buffer_pips,
            bos_buffer_pips=parent.bos_buffer_pips,
        )

        # Apply random mutations
        if random.random() < mutation_rate:
            child.ote_min = max(0.38, min(0.78, parent.ote_min + random.uniform(-0.05, 0.05)))
        if random.random() < mutation_rate:
            child.ote_max = max(0.62, min(0.90, parent.ote_max + random.uniform(-0.05, 0.05)))
        if random.random() < mutation_rate:
            child.min_rr = max(1.5, min(5.0, parent.min_rr + random.uniform(-0.5, 0.5)))
        if random.random() < mutation_rate:
            child.fvg_lookback = max(20, min(100, parent.fvg_lookback + random.randint(-10, 10)))
        if random.random() < mutation_rate:
            child.liquidity_lookback = max(10, min(40, parent.liquidity_lookback + random.randint(-5, 5)))
        if random.random() < mutation_rate:
            child.sweep_buffer_pips = max(0.5, min(5.0, parent.sweep_buffer_pips + random.uniform(-0.5, 0.5)))
        if random.random() < mutation_rate:
            child.bos_buffer_pips = max(0.5, min(5.0, parent.bos_buffer_pips + random.uniform(-0.5, 0.5)))

        # Mutate killzone times (±15 minutes)
        if random.random() < mutation_rate * 0.5:  # less frequent — killzones are important
            child.ny_am_kz_start = self._mutate_time(parent.ny_am_kz_start, 15)
        if random.random() < mutation_rate * 0.5:
            child.ny_am_kz_end = self._mutate_time(parent.ny_am_kz_end, 15)
        if random.random() < mutation_rate * 0.5:
            child.london_kz_start = self._mutate_time(parent.london_kz_start, 15)
        if random.random() < mutation_rate * 0.5:
            child.london_kz_end = self._mutate_time(parent.london_kz_end, 15)

        return child

    def _mutate_time(self, time_str: str, max_delta_min: int) -> str:
        """Mutate a time string by ±max_delta_min minutes."""
        try:
            h, m = map(int, time_str.split(":"))
            total_min = h * 60 + m + random.randint(-max_delta_min, max_delta_min)
            total_min = max(0, min(23 * 60 + 59, total_min))
            return f"{total_min // 60:02d}:{total_min % 60:02d}"
        except Exception:
            return time_str

    def _build_config_for_variant(self, variant: StrategyVariant) -> Dict:
        """Create a modified config for testing a variant."""
        cfg = copy.deepcopy(self.cfg)
        cfg["strategy"]["ote_min"] = variant.ote_min
        cfg["strategy"]["ote_max"] = variant.ote_max
        cfg["strategy"]["ny_am_kz_start"] = variant.ny_am_kz_start
        cfg["strategy"]["ny_am_kz_end"] = variant.ny_am_kz_end
        cfg["strategy"]["london_kz_start"] = variant.london_kz_start
        cfg["strategy"]["london_kz_end"] = variant.london_kz_end
        cfg["strategy"]["fvg_lookback"] = variant.fvg_lookback
        cfg["strategy"]["liquidity_lookback"] = variant.liquidity_lookback
        cfg["strategy"]["sweep_buffer_pips"] = variant.sweep_buffer_pips
        cfg["strategy"]["bos_buffer_pips"] = variant.bos_buffer_pips
        cfg["risk"]["reward_to_risk"] = variant.min_rr
        return cfg

    def _score_variant(self, variant: StrategyVariant) -> float:
        """Compute a fitness score for a variant.
        Score = win_rate × avg_rr × profit_factor × trade_count_factor

        HARD GATE: variant must be actually profitable (positive P&L + profit factor > 1).
        Losing strategies always return 0 — they can never be promoted as "best".
        """
        if variant.total_trades < 3:
            return 0.0  # not enough data

        # HARD GATE: reject losing strategies
        if variant.total_pnl <= 0:
            return 0.0
        if variant.profit_factor <= 1.0:
            return 0.0

        # Profit factor component (penalize large drawdowns)
        if variant.max_drawdown > 0:
            pf_component = variant.profit_factor / (1 + variant.max_drawdown / 1000)
        else:
            pf_component = variant.profit_factor

        # Trade count factor — prefer strategies that trade more (more reliable)
        trade_factor = min(1.0, variant.total_trades / 20)

        score = (variant.win_rate / 100) * max(0.1, variant.avg_rr) * max(0.1, pf_component) * trade_factor
        return round(score, 4)

    def run_evolution_cycle(self, days_back: int = 30, population_size: int = 6) -> Dict:
        """Run one evolution cycle: generate mutants, test them, keep winners.

        Args:
            days_back: how many days of history to test on
            population_size: how many variants to test per generation

        Returns:
            Summary dict with generation results
        """
        logger.info("=" * 70)
        logger.info(f"[EVOLUTION] Starting generation {self.generation + 1}")
        logger.info(f"[EVOLUTION] Testing {population_size} variants on {days_back} days of history")
        logger.info("=" * 70)

        # Start from base or best known variant
        if self.best_variant and self.best_variant.score > 0:
            parent = self.best_variant
            logger.info(f"[EVOLUTION] Parent: {parent.id} (score={parent.score:.4f})")
        else:
            parent = self._generate_base_variant()
            logger.info(f"[EVOLUTION] Parent: base strategy (no evolved parent yet)")

        # Generate population
        population = [parent]  # always include parent
        for _ in range(population_size - 1):
            child = self._mutate(parent, mutation_rate=0.3)
            population.append(child)

        # Test each variant
        results = []
        for i, variant in enumerate(population):
            logger.info(f"[EVOLUTION] Testing variant {i+1}/{len(population)}: {variant.id}")
            logger.info(f"  OTE=[{variant.ote_min:.2f}-{variant.ote_max:.2f}] min_RR={variant.min_rr:.1f} "
                       f"KZ_NY={variant.ny_am_kz_start}-{variant.ny_am_kz_end} "
                       f"FVG_lb={variant.fvg_lookback} liq_lb={variant.liquidity_lookback}")

            # Build a modified backtester with this variant's config
            variant_cfg = self._build_config_for_variant(variant)
            variant_backtester = IdleBacktester(self.ledger, self.backtester.broker, variant_cfg)

            # Run deep backtest on US30 only (fast — one symbol for evolution)
            result = variant_backtester.run_deep_backtest("US30", days_back=days_back)

            if result and result.total_trades > 0:
                variant.win_rate = result.win_rate
                variant.total_trades = result.total_trades
                variant.total_pnl = result.total_pnl
                variant.avg_rr = result.avg_rr
                variant.max_drawdown = result.max_drawdown
                variant.profit_factor = result.profit_factor

                variant.score = self._score_variant(variant)
                logger.info(f"  Result: {result.total_trades} trades | win={result.win_rate:.0f}% | "
                           f"PnL={result.total_pnl:+.2f} | RR={result.avg_rr:.2f} | score={variant.score:.4f}")
            else:
                variant.score = 0.0
                logger.info(f"  Result: 0 trades (score=0)")

            results.append(variant)

        # Sort by score, keep top performers
        results.sort(key=lambda v: v.score, reverse=True)
        winners = results[:3]  # keep top 3

        logger.info("=" * 70)
        logger.info(f"[EVOLUTION] Generation {self.generation + 1} RESULTS")
        logger.info("=" * 70)
        for i, v in enumerate(results):
            marker = "[BEST]" if i == 0 else ("[+]" if i < 3 else "[-]")
            logger.info(f"  {marker} {v.id}: score={v.score:.4f} | win={v.win_rate:.0f}% | "
                       f"trades={v.total_trades} | PnL={v.total_pnl:+.2f}")

        # Save winners
        for winner in winners:
            if winner.score > 0:
                self._save_variant(winner)

        # Update best variant
        if winners and winners[0].score > 0:
            if self.best_variant is None or winners[0].score > self.best_variant.score:
                self.best_variant = winners[0]
                logger.info(f"[EVOLUTION] [TARGET] NEW BEST STRATEGY: {winners[0].id} (score={winners[0].score:.4f})")
                logger.info(f"  Parameters: OTE=[{winners[0].ote_min:.2f}-{winners[0].ote_max:.2f}] "
                           f"min_RR={winners[0].min_rr:.1f} NY_KZ={winners[0].ny_am_kz_start}-{winners[0].ny_am_kz_end}")

        self.generation += 1
        self.population = winners

        return {
            "generation": self.generation,
            "variants_tested": len(results),
            "best_score": winners[0].score if winners else 0,
            "best_variant_id": winners[0].id if winners else None,
            "best_win_rate": winners[0].win_rate if winners else 0,
            "best_pnl": winners[0].total_pnl if winners else 0,
        }

    def _get_backtest_trades(self, result):
        """Helper to extract trades from a backtest result (if available)."""
        return []

    def discover_patterns(self, days_back: int = 30) -> List[Dict]:
        """Analyze historical data for statistical edges NOT in the original strategy.

        This is the bot 'discovering' new patterns through brute-force statistics.
        """
        logger.info("=" * 70)
        logger.info(f"[DISCOVERY] Scanning {days_back} days for new patterns...")
        logger.info("=" * 70)

        patterns = []

        try:
            from .strategy import generate_signal
            from .killzones import in_killzone, current_killzone, parse_kz

            # Fetch US30 history
            symbol = "US30"
            entry_df = self.backtester.broker.get_history(symbol, self.backtester.tf_entry, count=5000)
            if entry_df.empty or len(entry_df) < 500:
                logger.info(f"[DISCOVERY] Not enough data for pattern discovery")
                return patterns

            # Pattern 1: Reversal probability after large wick
            logger.info("[DISCOVERY] Testing pattern: Large wick reversal")
            wick_reversal_stats = self._test_wick_reversal_pattern(entry_df)
            if wick_reversal_stats["sample_size"] >= 10:
                patterns.append({
                    "name": "Large Wick Reversal",
                    "description": f"After a candle with wick > 2x body, price reverses {wick_reversal_stats['reversal_rate']:.0f}% of the time",
                    "win_rate": wick_reversal_stats["reversal_rate"],
                    "sample_size": wick_reversal_stats["sample_size"],
                    "rules": "If candle wick > 2x body, expect reversal in next 3 candles",
                })
                logger.info(f"  -> {wick_reversal_stats['reversal_rate']:.0f}% reversal rate (n={wick_reversal_stats['sample_size']})")

            # Pattern 2: Time-of-day bias
            logger.info("[DISCOVERY] Testing pattern: Time-of-day directional bias")
            time_bias = self._test_time_of_day_bias(entry_df)
            for hour, stats in time_bias.items():
                if stats["sample_size"] >= 20 and abs(stats["bull_rate"] - 50) > 10:
                    direction = "bullish" if stats["bull_rate"] > 50 else "bearish"
                    patterns.append({
                        "name": f"Hour {hour}:00 UTC Bias",
                        "description": f"At {hour}:00 UTC, price moves {direction} {max(stats['bull_rate'], 100-stats['bull_rate']):.0f}% of the time",
                        "win_rate": max(stats["bull_rate"], 100 - stats["bull_rate"]),
                        "sample_size": stats["sample_size"],
                        "rules": f"Prefer {direction} setups at hour {hour}:00 UTC",
                    })
                    logger.info(f"  -> Hour {hour}:00 UTC: {direction} {max(stats['bull_rate'], 100-stats['bull_rate']):.0f}% (n={stats['sample_size']})")

            # Pattern 3: Consecutive candle streak reversal
            logger.info("[DISCOVERY] Testing pattern: Consecutive candle streak reversal")
            streak_stats = self._test_streak_reversal(entry_df)
            if streak_stats["sample_size"] >= 10:
                patterns.append({
                    "name": "Streak Reversal",
                    "description": f"After {streak_stats['streak_length']} consecutive {streak_stats['direction']} candles, reversal occurs {streak_stats['reversal_rate']:.0f}% of the time",
                    "win_rate": streak_stats["reversal_rate"],
                    "sample_size": streak_stats["sample_size"],
                    "rules": f"After {streak_stats['streak_length']} {streak_stats['direction']} candles, expect reversal",
                })
                logger.info(f"  -> {streak_stats['streak_length']} {streak_stats['direction']} candles -> {streak_stats['reversal_rate']:.0f}% reversal (n={streak_stats['sample_size']})")

        except Exception as e:
            logger.error(f"[DISCOVERY] Error: {e}")

        # Save discovered patterns as lessons
        for p in patterns:
            self.ledger.save_lesson(
                category="discovered_pattern",
                rule=f"{p['name']}: {p['description']} (win_rate={p['win_rate']:.0f}%, n={p['sample_size']})",
                confidence=p["win_rate"] / 100,
                sample_size=p["sample_size"]
            )

        logger.info("=" * 70)
        logger.info(f"[DISCOVERY] Found {len(patterns)} new patterns")
        for p in patterns:
            logger.info(f"  [STAT] {p['name']}: {p['win_rate']:.0f}% win rate (n={p['sample_size']})")
        logger.info("=" * 70)

        return patterns

    def _test_wick_reversal_pattern(self, df) -> Dict:
        """Test if large wicks predict reversals."""
        reversals = 0
        total = 0
        for i in range(20, len(df) - 3):
            open_p = df["open"].iloc[i]
            close = df["close"].iloc[i]
            high = df["high"].iloc[i]
            low = df["low"].iloc[i]
            body = abs(close - open_p)
            upper_wick = high - max(open_p, close)
            lower_wick = min(open_p, close) - low

            if body > 0:
                # Large upper wick (bearish reversal signal)
                if upper_wick > 2 * body:
                    total += 1
                    # Check if next 3 candles moved down
                    future_close = df["close"].iloc[i + 3]
                    if future_close < close:
                        reversals += 1
                # Large lower wick (bullish reversal signal)
                elif lower_wick > 2 * body:
                    total += 1
                    future_close = df["close"].iloc[i + 3]
                    if future_close > close:
                        reversals += 1

        return {
            "reversal_rate": (reversals / total * 100) if total > 0 else 0,
            "sample_size": total,
        }

    def _test_time_of_day_bias(self, df) -> Dict:
        """Test if certain hours have directional bias."""
        hourly = defaultdict(lambda: {"bull": 0, "bear": 0, "total": 0})
        for i in range(1, len(df)):
            ts = df.index[i]
            hour = ts.hour if hasattr(ts, 'hour') else 0
            direction = "bull" if df["close"].iloc[i] > df["open"].iloc[i] else "bear"
            hourly[hour][direction] += 1
            hourly[hour]["total"] += 1

        result = {}
        for hour, stats in hourly.items():
            result[hour] = {
                "bull_rate": (stats["bull"] / stats["total"] * 100) if stats["total"] > 0 else 0,
                "sample_size": stats["total"],
            }
        return result

    def _test_streak_reversal(self, df) -> Dict:
        """Test if consecutive same-direction candles predict reversal."""
        for streak_len in [4, 5, 6, 7]:
            reversals = 0
            total = 0
            current_streak = 0
            last_dir = None

            for i in range(1, len(df) - 1):
                direction = "up" if df["close"].iloc[i] > df["open"].iloc[i] else "down"
                if direction == last_dir:
                    current_streak += 1
                else:
                    if current_streak >= streak_len and last_dir is not None:
                        total += 1
                        # Check if next candle reversed
                        next_dir = "up" if df["close"].iloc[i] > df["open"].iloc[i] else "down"
                        if next_dir != last_dir:
                            reversals += 1
                    current_streak = 1
                    last_dir = direction

            if total >= 10:
                return {
                    "streak_length": streak_len,
                    "direction": last_dir or "up",
                    "reversal_rate": (reversals / total * 100) if total > 0 else 0,
                    "sample_size": total,
                }
        return {"streak_length": 0, "direction": "none", "reversal_rate": 0, "sample_size": 0}

    def get_best_strategy_params(self) -> Optional[StrategyVariant]:
        """Return the best evolved strategy, or None if no evolution has happened."""
        return self.best_variant

    def print_status(self):
        """Print current evolution status."""
        logger.info("=" * 70)
        logger.info("[STRATEGY LAB] EVOLUTION STATUS")
        logger.info("=" * 70)
        logger.info(f"Generation: {self.generation}")
        logger.info(f"Population: {len(self.population)} variants")
        if self.best_variant:
            logger.info(f"Best strategy: {self.best_variant.id}")
            logger.info(f"  Score: {self.best_variant.score:.4f}")
            logger.info(f"  Win rate: {self.best_variant.win_rate:.1f}%")
            logger.info(f"  Total trades: {self.best_variant.total_trades}")
            logger.info(f"  P&L: {self.best_variant.total_pnl:+.2f}")
            logger.info(f"  Avg RR: {self.best_variant.avg_rr:.2f}")
            logger.info(f"  Parameters:")
            logger.info(f"    OTE range: [{self.best_variant.ote_min:.2f} - {self.best_variant.ote_max:.2f}]")
            logger.info(f"    Min RR: {self.best_variant.min_rr:.1f}")
            logger.info(f"    NY AM KZ: {self.best_variant.ny_am_kz_start} - {self.best_variant.ny_am_kz_end}")
            logger.info(f"    London KZ: {self.best_variant.london_kz_start} - {self.best_variant.london_kz_end}")
            logger.info(f"    FVG lookback: {self.best_variant.fvg_lookback}")
            logger.info(f"    Liquidity lookback: {self.best_variant.liquidity_lookback}")
        else:
            logger.info("No evolved strategies yet — run --evolve to start")
        logger.info("=" * 70)
