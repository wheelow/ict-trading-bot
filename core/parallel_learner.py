"""
Parallel Learner — Runs learning tasks DURING killzones while bot is trading

The bot scans for trades every 5 seconds. Most scans find nothing (no setup).
Instead of wasting those cycles, the Parallel Learner uses them to:

1. Quick Pattern Validation — re-test discovered patterns against latest data
2. Background Micro-Evolution — test 1 strategy variant quietly per killzone
3. Real-Time Lesson Extraction — when a trade closes, immediately analyze why
4. Market Regime Detection — track current volatility/trend strength
5. Setup Logging — log every potential setup found (even if filtered) for later analysis

This means the bot is ALWAYS learning, even while actively trading.
"""
import logging
import random
from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from .memory import TradeLedger
from .learning import LearningEngine
from .strategy_lab import StrategyLab, StrategyVariant

logger = logging.getLogger("ict.parallel_learner")


@dataclass
class SetupObservation:
    """A potential setup the bot observed (even if it didn't trade)."""
    timestamp: str
    symbol: str
    direction: str
    would_entry: float
    would_sl: float
    would_tp: float
    would_rr: float
    killzone: str
    htf_bias: str
    confluence: str
    traded: bool  # did the bot actually take this trade?
    skip_reason: str = ""  # why it was skipped (if not traded)


class ParallelLearner:
    """Learns in parallel with live trading — never stops learning."""

    def __init__(self, ledger: TradeLedger, learning: LearningEngine, strategy_lab: StrategyLab):
        self.ledger = ledger
        self.learning = learning
        self.strategy_lab = strategy_lab
        self.setup_observations: List[SetupObservation] = []
        self.market_regime = {"volatility": "normal", "trend_strength": "moderate"}
        self.micro_evolution_done_this_kz = False
        self.current_killzone = ""
        self.last_lesson_extraction = None
        logger.info("Parallel learner initialized — bot will learn during killzones too")

    def on_killzone_enter(self, killzone_name: str):
        """Called when bot enters a killzone — reset per-killzone state."""
        self.current_killzone = killzone_name
        self.micro_evolution_done_this_kz = False
        logger.info(f"[PARALLEL] Entered {killzone_name} killzone — parallel learning active")

    def on_killzone_exit(self, killzone_name: str):
        """Called when bot exits a killzone — extract final lessons."""
        logger.info(f"[PARALLEL] Exited {killzone_name} killzone — analyzing session observations")
        self._analyze_setup_observations()
        self._save_observations_to_ledger()

    def observe_setup(self, observation: SetupObservation):
        """Log a setup the bot observed (whether traded or not).
        This builds a dataset of 'what the bot saw' for later analysis.
        """
        self.setup_observations.append(observation)
        if not observation.traded:
            logger.debug(f"[PARALLEL] Observed setup (skipped): {observation.symbol} {observation.direction} "
                        f"— reason: {observation.skip_reason}")

    def on_trade_closed(self, trade_data: Dict):
        """Called immediately when a trade closes — extract real-time lessons.
        
        Args:
            trade_data: dict with symbol, direction, entry, sl, tp, pnl, rr, status,
                       killzone, htf_bias, confluence, hour_utc, day_of_week
        """
        logger.info(f"[PARALLEL] Analyzing closed trade for real-time lessons...")
        
        won = trade_data.get("status") == "won"
        symbol = trade_data.get("symbol", "")
        killzone = trade_data.get("killzone", "")
        confluence = trade_data.get("confluence", "")
        hour = trade_data.get("hour_utc", 0)
        direction = trade_data.get("direction", "")

        # Update learning engine (adaptive risk)
        self.learning.record_trade_outcome(won=won)

        # Generate immediate lesson based on outcome
        lessons = []

        if won:
            # What went right?
            if trade_data.get("rr_achieved", 0) >= 3.0:
                lessons.append({
                    "category": "winning_setup",
                    "rule": f"{symbol} {direction} in {killzone} with {confluence} confluence hit full 3R — this is a high-quality setup type",
                    "confidence": 0.7,
                })
            if confluence and confluence != "OTE_only":
                lessons.append({
                    "category": "winning_confluence",
                    "rule": f"{confluence} confluence produced a win on {symbol} in {killzone}",
                    "confidence": 0.6,
                })
        else:
            # What went wrong?
            lessons.append({
                "category": "losing_setup",
                "rule": f"{symbol} {direction} in {killzone} lost — be cautious with similar setups",
                "confidence": 0.5,
            })
            if hour and (hour < 7 or hour > 17):
                lessons.append({
                    "category": "bad_time",
                    "rule": f"Trade at hour {hour}:00 UTC lost — outside main liquidity hours",
                    "confidence": 0.4,
                })

        # Save lessons immediately
        for lesson in lessons:
            try:
                self.ledger.save_lesson(
                    category=lesson["category"],
                    rule=lesson["rule"],
                    confidence=lesson["confidence"],
                    sample_size=1
                )
            except Exception:
                pass

        logger.info(f"[PARALLEL] Extracted {len(lessons)} real-time lessons from closed trade")

        # Re-run pattern analysis (quick)
        try:
            self.learning.analyze_patterns()
        except Exception:
            pass

    def run_micro_evolution(self, broker, cfg: Dict):
        """Test ONE strategy variant quietly during the current killzone.
        This runs in the background without affecting live trading.
        """
        if self.micro_evolution_done_this_kz:
            return  # already done for this killzone

        if not hasattr(broker, 'get_history'):
            return

        try:
            logger.info(f"[PARALLEL] Running micro-evolution (1 variant) during {self.current_killzone}...")
            
            # Generate 1 mutated variant
            if self.strategy_lab.best_variant and self.strategy_lab.best_variant.score > 0:
                parent = self.strategy_lab.best_variant
            else:
                parent = self.strategy_lab._generate_base_variant()

            child = self.strategy_lab._mutate(parent, mutation_rate=0.2)
            
            # Test it on recent data (7 days — quick)
            from .backtester import IdleBacktester
            variant_cfg = self.strategy_lab._build_config_for_variant(child)
            variant_backtester = IdleBacktester(self.ledger, broker, variant_cfg)
            
            result = variant_backtester.run_deep_backtest("US30", days_back=7)
            
            if result and result.total_trades > 0:
                child.win_rate = result.win_rate
                child.total_trades = result.total_trades
                child.total_pnl = result.total_pnl
                child.avg_rr = result.avg_rr
                child.max_drawdown = result.max_drawdown
                child.profit_factor = result.profit_factor
                child.score = self.strategy_lab._score_variant(child)
                
                logger.info(f"[PARALLEL] Micro-evolution: variant {child.id} scored {child.score:.4f} "
                           f"(win={child.win_rate:.0f}%, trades={child.total_trades})")
                
                # Save if it's good
                if child.score > 0:
                    self.strategy_lab._save_variant(child)
                    if self.strategy_lab.best_variant is None or child.score > self.strategy_lab.best_variant.score:
                        self.strategy_lab.best_variant = child
                        logger.info(f"[PARALLEL] [TARGET] New best strategy discovered during killzone! {child.id}")
            else:
                logger.info(f"[PARALLEL] Micro-evolution: variant {child.id} produced 0 trades")
            
            self.micro_evolution_done_this_kz = True

        except Exception as e:
            logger.debug(f"[PARALLEL] Micro-evolution error (non-fatal): {e}")
            self.micro_evolution_done_this_kz = True

    def detect_market_regime(self, broker, symbol: str = "US30"):
        """Detect current market regime (volatility, trend strength).
        Helps the bot understand context — is this a trending or ranging market?
        """
        try:
            df = broker.get_history(symbol, "M5", count=50)
            if df.empty or len(df) < 20:
                return

            # Volatility: average candle range
            ranges = [(h - l) for h, l in zip(df["high"].values, df["low"].values)]
            avg_range = sum(ranges) / len(ranges) if ranges else 0
            recent_range = sum(ranges[-10:]) / 10 if len(ranges) >= 10 else avg_range

            if recent_range > avg_range * 1.5:
                regime_vol = "high_volatility"
            elif recent_range < avg_range * 0.6:
                regime_vol = "low_volatility"
            else:
                regime_vol = "normal"

            # Trend strength: directional bias of last 20 candles
            closes = df["close"].values[-20:]
            direction_changes = sum(1 for i in range(1, len(closes)) if (closes[i] - closes[i-1]) * (closes[max(0,i-1)] - closes[max(0,i-2)] if i >= 2 else 1) < 0)
            
            if direction_changes < 5:
                trend = "strong_trend"
            elif direction_changes > 12:
                trend = "ranging"
            else:
                trend = "moderate"

            old_regime = f"{self.market_regime['volatility']}/{self.market_regime['trend_strength']}"
            self.market_regime = {"volatility": regime_vol, "trend_strength": trend}
            new_regime = f"{regime_vol}/{trend}"

            if old_regime != new_regime:
                logger.info(f"[PARALLEL] Market regime: {old_regime} -> {new_regime}")

        except Exception as e:
            logger.debug(f"[PARALLEL] Regime detection error: {e}")

    def _analyze_setup_observations(self):
        """Analyze all setups observed during the killzone — find patterns."""
        if not self.setup_observations:
            return

        traded = [s for s in self.setup_observations if s.traded]
        skipped = [s for s in self.setup_observations if not s.traded]

        logger.info(f"[PARALLEL] Killzone analysis: {len(self.setup_observations)} setups observed "
                    f"({len(traded)} traded, {len(skipped)} skipped)")

        # Analyze skip reasons
        skip_reasons = defaultdict(int)
        for s in skipped:
            skip_reasons[s.skip_reason] += 1

        if skip_reasons:
            logger.info("[PARALLEL] Skip reasons:")
            for reason, count in skip_reasons.items():
                logger.info(f"  {reason}: {count} setups skipped")

        # Analyze by symbol
        by_symbol = defaultdict(lambda: {"total": 0, "traded": 0})
        for s in self.setup_observations:
            by_symbol[s.symbol]["total"] += 1
            if s.traded:
                by_symbol[s.symbol]["traded"] += 1

        logger.info("[PARALLEL] Setup frequency by symbol:")
        for symbol, counts in by_symbol.items():
            logger.info(f"  {symbol}: {counts['total']} setups, {counts['traded']} traded")

    def _save_observations_to_ledger(self):
        """Save setup observations to the ledger for long-term analysis."""
        if not self.setup_observations:
            return

        try:
            import json
            conn = self.ledger._get_conn() if hasattr(self.ledger, '_get_conn') else None
            if conn is None:
                import sqlite3
                conn = sqlite3.connect(self.ledger.db_path)

            # Create table if not exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS setup_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    direction TEXT,
                    would_entry REAL,
                    would_sl REAL,
                    would_tp REAL,
                    would_rr REAL,
                    killzone TEXT,
                    htf_bias TEXT,
                    confluence TEXT,
                    traded INTEGER,
                    skip_reason TEXT
                )
            """)

            for obs in self.setup_observations[-50:]:  # keep last 50
                conn.execute("""
                    INSERT INTO setup_observations (
                        timestamp, symbol, direction, would_entry, would_sl,
                        would_tp, would_rr, killzone, htf_bias, confluence,
                        traded, skip_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    obs.timestamp, obs.symbol, obs.direction, obs.would_entry,
                    obs.would_sl, obs.would_tp, obs.would_rr, obs.killzone,
                    obs.htf_bias, obs.confluence, int(obs.traded), obs.skip_reason
                ))

            conn.commit()
            if hasattr(self.ledger, '_get_conn'):
                pass  # connection managed by ledger
            else:
                conn.close()

        except Exception as e:
            logger.debug(f"[PARALLEL] Could not save observations: {e}")

        # Clear observations for next killzone
        self.setup_observations = []

    def should_reject_pattern(self, symbol: str, direction: str, killzone: str, confluence: str) -> tuple:
        """Check whether historical trade data suggests rejecting this setup.

        Closes the feedback loop between the trade ledger and signal generation:
        if this exact pattern (symbol + direction + killzone + confluence) has
        a consistently losing record, reject the signal before it becomes a trade.

        Returns:
            (reject: bool, reason_or_boost: str)
            reason_or_boost is a human-readable reason if rejected, or
            "boost" if the pattern has a strong winning history (caller should
            increase quality_score), or empty string if neutral.
        """
        patterns = self.ledger.get_pattern_win_rates(min_samples=2)

        # 1. Exact pattern check (symbol + direction + killzone + confluence)
        specific_key = f"{symbol}_{direction}_{killzone}_{confluence}"
        if specific_key in patterns:
            p = patterns[specific_key]
            if p["total"] >= 5 and p["win_rate"] < 30.0:
                return (True, f"Pattern '{specific_key}' has {p['win_rate']:.0f}% win rate "
                        f"({p['wins']}W/{p['losses']}L, n={p['total']}) — rejecting (< 30%)")
            if p["total"] >= 3 and p["win_rate"] < 20.0:
                return (True, f"Pattern '{specific_key}' has {p['win_rate']:.0f}% win rate "
                        f"({p['wins']}W/{p['losses']}L, n={p['total']}) — rejecting (< 20% with fewer samples)")
            if p["total"] >= 5 and p["win_rate"] > 60.0:
                return (False, "boost")

        # 2. Broad pattern check (symbol + direction + killzone, any confluence)
        broad = self.ledger.get_broad_pattern_win_rates(
            ["symbol", "direction", "killzone"], min_samples=4
        )
        broad_key = f"{symbol}_{direction}_{killzone}"
        if broad_key in broad:
            bp = broad[broad_key]
            if bp["win_rate"] < 25.0:
                return (True, f"Broad pattern '{broad_key}' has {bp['win_rate']:.0f}% win rate "
                        f"({bp['wins']}W/{bp['losses']}L, n={bp['total']}) — rejecting")

        return (False, "")

    def get_day_confidence_map(self) -> Dict[int, Dict]:
        """Delegate to ledger for day-of-week confidence data."""
        return self.ledger.get_day_confidence()

    def get_status(self) -> Dict:
        """Get current parallel learning status."""
        return {
            "current_killzone": self.current_killzone,
            "observations_this_kz": len(self.setup_observations),
            "market_regime": f"{self.market_regime['volatility']}/{self.market_regime['trend_strength']}",
            "micro_evolution_done": self.micro_evolution_done_this_kz,
            "best_strategy_score": self.strategy_lab.best_variant.score if self.strategy_lab.best_variant else 0,
        }
