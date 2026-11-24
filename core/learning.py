"""
Learning Engine — Pattern Recognition & Adaptive Rules

Reads from the trade ledger, finds patterns, and generates adaptive rules.

Capabilities:
1. Pattern Analysis
   - Win rate by symbol, killzone, day of week, hour, setup type, confluence
   - Identify winning and losing patterns
   - Generate human-readable insights

2. Adaptive Risk Management
   - Reduce risk after consecutive losses
   - Increase risk (capped) after consecutive wins
   - Skip trades in low-win-rate conditions

3. Trade Filtering
   - Block trades in conditions with <30% win rate (min sample size: 10 trades)
   - Block trades on symbols with poor recent performance
   - Require extra confluence in marginal conditions

4. Lesson Generation
   - Auto-generate lessons from patterns
   - Store in ledger for future reference
   - Display to user in logs
"""
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
import logging
from collections import defaultdict

from .memory import TradeLedger

logger = logging.getLogger("ict.learning")


@dataclass
class AdaptiveRiskConfig:
    """Risk settings that adapt based on performance."""
    base_risk_pct: float = 2.0
    min_risk_pct: float = 0.5       # never go below this
    max_risk_pct: float = 3.0       # never go above this
    loss_streak_reduction: float = 0.5  # reduce by this much per consecutive loss
    win_streak_increase: float = 0.25   # increase by this much per consecutive win
    min_trades_for_patterns: int = 10   # need this many trades before filtering
    min_win_rate_to_trade: float = 30.0 # skip if pattern win rate below this
    max_loss_streak: int = 5            # halt if this many losses in a row


class LearningEngine:
    """Analyzes trade history and generates adaptive rules."""

    def __init__(self, ledger: TradeLedger, cfg: AdaptiveRiskConfig = None):
        self.ledger = ledger
        self.cfg = cfg or AdaptiveRiskConfig()
        self._current_risk_pct = cfg.base_risk_pct if cfg else 2.0
        self._loss_streak = 0
        self._win_streak = 0
        self._last_analysis = None
        logger.info("Learning engine initialized")

    def record_trade_outcome(self, won: bool):
        """Update streaks and adjust risk based on outcome."""
        if won:
            self._win_streak += 1
            self._loss_streak = 0
            # Increase risk slightly after wins (capped)
            if self._win_streak >= 2:
                new_risk = self._current_risk_pct + self.cfg.win_streak_increase * (self._win_streak - 1)
                self._current_risk_pct = min(self.cfg.max_risk_pct, new_risk)
                logger.info(f"[LEARN] Win streak={self._win_streak} → risk increased to {self._current_risk_pct:.2f}%")
        else:
            self._loss_streak += 1
            self._win_streak = 0
            # Reduce risk after losses
            new_risk = self._current_risk_pct - self.cfg.loss_streak_reduction * self._loss_streak
            self._current_risk_pct = max(self.cfg.min_risk_pct, new_risk)
            logger.info(f"[LEARN] Loss streak={self._loss_streak} → risk reduced to {self._current_risk_pct:.2f}%")

            if self._loss_streak >= self.cfg.max_loss_streak:
                logger.warning(f"[LEARN] Max loss streak ({self.cfg.max_loss_streak}) reached — recommend halting")

    def current_risk_pct(self) -> float:
        """Get the current adaptive risk percentage."""
        return self._current_risk_pct

    def should_trade(self, symbol: str, killzone: str, htf_bias: str,
                     confluence: str, hour_utc: int) -> Tuple[bool, str]:
        """Check if the bot should trade based on learned patterns.

        Returns:
            (allowed: bool, reason: str)
        """
        trades = self.ledger.get_recent_trades(days=90)

        if len(trades) < self.cfg.min_trades_for_patterns:
            return True, f"Learning (only {len(trades)} trades, need {self.cfg.min_trades_for_patterns})"

        # Check 1: Symbol win rate
        symbol_trades = [t for t in trades if t["symbol"] == symbol and t["status"] != "open"]
        if len(symbol_trades) >= 5:
            wins = sum(1 for t in symbol_trades if t["status"] == "won")
            win_rate = wins / len(symbol_trades) * 100
            if win_rate < self.cfg.min_win_rate_to_trade:
                return False, f"Symbol {symbol} win rate {win_rate:.0f}% < {self.cfg.min_win_rate_to_trade}%"

        # Check 2: Killzone win rate
        kz_trades = [t for t in trades if t["killzone"] == killzone and t["status"] != "open"]
        if len(kz_trades) >= 5:
            wins = sum(1 for t in kz_trades if t["status"] == "won")
            win_rate = wins / len(kz_trades) * 100
            if win_rate < self.cfg.min_win_rate_to_trade:
                return False, f"Killzone {killzone} win rate {win_rate:.0f}% < {self.cfg.min_win_rate_to_trade}%"

        # Check 3: Symbol + Killzone combination
        combo_trades = [t for t in trades
                       if t["symbol"] == symbol and t["killzone"] == killzone and t["status"] != "open"]
        if len(combo_trades) >= 5:
            wins = sum(1 for t in combo_trades if t["status"] == "won")
            win_rate = wins / len(combo_trades) * 100
            if win_rate < self.cfg.min_win_rate_to_trade:
                return False, f"{symbol}+{killzone} win rate {win_rate:.0f}% (n={len(combo_trades)})"

        # Check 4: Hour of day
        hour_trades = [t for t in trades if t["hour_utc"] == hour_utc and t["status"] != "open"]
        if len(hour_trades) >= 8:
            wins = sum(1 for t in hour_trades if t["status"] == "won")
            win_rate = wins / len(hour_trades) * 100
            if win_rate < 25.0:
                return False, f"Hour {hour_utc}:00 UTC win rate {win_rate:.0f}% (n={len(hour_trades)})"

        return True, "OK"

    def analyze_patterns(self) -> Dict:
        """Analyze all trades and return pattern insights.
        Also generates lessons and saves them to the ledger.
        """
        trades = self.ledger.get_recent_trades(days=90)
        closed = [t for t in trades if t["status"] != "open"]

        if len(closed) < 5:
            return {"message": f"Not enough trades to analyze (only {len(closed)})"}

        insights = {
            "total_trades": len(closed),
            "overall_win_rate": 0,
            "by_symbol": {},
            "by_killzone": {},
            "by_confluence": {},
            "by_day_of_week": {},
            "by_hour": {},
            "best_setup": "",
            "worst_setup": "",
            "lessons": [],
        }

        # Overall win rate
        wins = sum(1 for t in closed if t["status"] == "won")
        insights["overall_win_rate"] = wins / len(closed) * 100

        # By symbol
        symbol_stats = self._group_stats(closed, "symbol")
        insights["by_symbol"] = symbol_stats

        # By killzone
        kz_stats = self._group_stats(closed, "killzone")
        insights["by_killzone"] = kz_stats

        # By confluence
        conf_stats = self._group_stats(closed, "confluence")
        insights["by_confluence"] = conf_stats

        # By day of week
        dow_stats = self._group_stats(closed, "day_of_week")
        insights["by_day_of_week"] = dow_stats

        # By hour
        hour_stats = self._group_stats(closed, "hour_utc")
        insights["by_hour"] = hour_stats

        # Find best and worst setups
        setups_with_data = {k: v for k, v in symbol_stats.items() if v["count"] >= 3}
        if setups_with_data:
            best = max(setups_with_data.items(), key=lambda x: x[1]["win_rate"])
            worst = min(setups_with_data.items(), key=lambda x: x[1]["win_rate"])
            insights["best_setup"] = f"{best[0]} ({best[1]['win_rate']:.0f}% win, n={best[1]['count']})"
            insights["worst_setup"] = f"{worst[0]} ({worst[1]['win_rate']:.0f}% win, n={worst[1]['count']})"

        # Generate lessons
        lessons = self._generate_lessons(insights)
        insights["lessons"] = lessons

        # Save lessons to ledger
        for lesson in lessons:
            self.ledger.save_lesson(
                category=lesson["category"],
                rule=lesson["rule"],
                confidence=lesson["confidence"],
                sample_size=lesson["sample_size"]
            )

        self._last_analysis = insights
        return insights

    def _group_stats(self, trades: List[Dict], key: str) -> Dict:
        """Group trades by a key and compute win rate."""
        groups = defaultdict(list)
        for t in trades:
            groups[t[key]].append(t)

        result = {}
        for group, group_trades in groups.items():
            wins = sum(1 for t in group_trades if t["status"] == "won")
            total_pnl = sum(t["pnl"] for t in group_trades)
            avg_rr = sum(t["rr_achieved"] for t in group_trades) / len(group_trades)
            result[str(group)] = {
                "count": len(group_trades),
                "wins": wins,
                "win_rate": wins / len(group_trades) * 100,
                "total_pnl": total_pnl,
                "avg_rr": avg_rr,
            }
        return result

    def _generate_lessons(self, insights: Dict) -> List[Dict]:
        """Generate human-readable lessons from pattern analysis."""
        lessons = []

        # Lesson: best performing symbol
        for symbol, stats in insights["by_symbol"].items():
            if stats["count"] >= 5:
                if stats["win_rate"] >= 60:
                    lessons.append({
                        "category": "strong_setup",
                        "rule": f"{symbol} performs well: {stats['win_rate']:.0f}% win rate over {stats['count']} trades. Consider prioritizing.",
                        "confidence": stats["win_rate"] / 100,
                        "sample_size": stats["count"],
                    })
                elif stats["win_rate"] < 30:
                    lessons.append({
                        "category": "weak_setup",
                        "rule": f"{symbol} underperforming: only {stats['win_rate']:.0f}% win rate over {stats['count']} trades. Consider skipping.",
                        "confidence": 1 - stats["win_rate"] / 100,
                        "sample_size": stats["count"],
                    })

        # Lesson: best killzone
        for kz, stats in insights["by_killzone"].items():
            if stats["count"] >= 5:
                if stats["win_rate"] >= 60:
                    lessons.append({
                        "category": "strong_killzone",
                        "rule": f"{kz} killzone is profitable: {stats['win_rate']:.0f}% win rate. Best time to trade.",
                        "confidence": stats["win_rate"] / 100,
                        "sample_size": stats["count"],
                    })
                elif stats["win_rate"] < 30:
                    lessons.append({
                        "category": "weak_killzone",
                        "rule": f"{kz} killzone underperforming: {stats['win_rate']:.0f}% win rate. Consider avoiding.",
                        "confidence": 1 - stats["win_rate"] / 100,
                        "sample_size": stats["count"],
                    })

        # Lesson: confluence value
        for conf, stats in insights["by_confluence"].items():
            if stats["count"] >= 5:
                if stats["win_rate"] >= 55:
                    lessons.append({
                        "category": "strong_confluence",
                        "rule": f"{conf} confluence boosts win rate to {stats['win_rate']:.0f}%.",
                        "confidence": stats["win_rate"] / 100,
                        "sample_size": stats["count"],
                    })

        return lessons

    def print_insights(self):
        """Print current insights to the log."""
        if not self._last_analysis:
            self.analyze_patterns()

        if not self._last_analysis or "message" in self._last_analysis:
            logger.info("[LEARN] Not enough data for analysis yet")
            return

        insights = self._last_analysis
        logger.info("=" * 70)
        logger.info("[LEARN] PATTERN ANALYSIS REPORT")
        logger.info("=" * 70)
        logger.info(f"Total trades analyzed: {insights['total_trades']}")
        logger.info(f"Overall win rate: {insights['overall_win_rate']:.1f}%")

        logger.info("\nBy Symbol:")
        for sym, stats in insights["by_symbol"].items():
            logger.info(f"  {sym}: {stats['win_rate']:.0f}% win ({stats['wins']}/{stats['count']}) | PnL={stats['total_pnl']:+.2f}")

        logger.info("\nBy Killzone:")
        for kz, stats in insights["by_killzone"].items():
            logger.info(f"  {kz}: {stats['win_rate']:.0f}% win ({stats['wins']}/{stats['count']}) | PnL={stats['total_pnl']:+.2f}")

        logger.info("\nBy Confluence:")
        for conf, stats in insights["by_confluence"].items():
            logger.info(f"  {conf}: {stats['win_rate']:.0f}% win ({stats['wins']}/{stats['count']}) | PnL={stats['total_pnl']:+.2f}")

        if insights["best_setup"]:
            logger.info(f"\nBest setup: {insights['best_setup']}")
        if insights["worst_setup"]:
            logger.info(f"Worst setup: {insights['worst_setup']}")

        if insights["lessons"]:
            logger.info(f"\nLessons Learned ({len(insights['lessons'])}):")
            for lesson in insights["lessons"]:
                logger.info(f"  [{lesson['category']}] {lesson['rule']}")

        logger.info("=" * 70)
