"""
Summary Generator - Comprehensive Report on Bot Stop

Generates unified report combining:
- Time travel backtest progress
- Live trading results
- Evolution timeline
- Current state

Shows when user presses Ctrl+C or bot stops.
"""

import logging
from datetime import datetime
from typing import Dict, List
from tabulate import tabulate

logger = logging.getLogger("ict.summary")

class SummaryGenerator:
    """Generates comprehensive summary on bot stop"""
    
    def __init__(self, time_traveler, learning_engine, ledger):
        """
        Args:
            time_traveler: TimeTraveler instance
            learning_engine: LearningEngine instance
            ledger: TradeLedger instance
        """
        self.time_traveler = time_traveler
        self.learning_engine = learning_engine
        self.ledger = ledger
        self.start_time = datetime.now()
    
    def generate_full_report(self) -> str:
        """Generate complete summary report"""
        
        lines = []
        lines.append("")
        lines.append("="*80)
        lines.append("  🤖 PERPETUAL LEARNING TRADER - SESSION SUMMARY")
        lines.append("="*80)
        lines.append("")
        
        # Runtime
        runtime = datetime.now() - self.start_time
        hours = runtime.total_seconds() / 3600
        lines.append(f"⏱️  Runtime: {hours:.1f} hours ({runtime.days} days, {runtime.seconds//3600} hours)")
        lines.append(f"📅 Session: {self.start_time.strftime('%Y-%m-%d %H:%M')} → {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")
        
        # === BACKTEST PROGRESS ===
        lines.append("━"*80)
        lines.append("  📚 HISTORICAL BACKTEST PROGRESS")
        lines.append("━"*80)
        
        travel_summary = self.time_traveler.get_progress_summary()
        
        direction_symbol = "⏪" if travel_summary['direction'] == "backward" else "⏩"
        lines.append(f"{direction_symbol} Current Position:")
        lines.append(f"   Week: {travel_summary['current_week']['monday']} to {travel_summary['current_week']['friday']}")
        lines.append(f"   Direction: {travel_summary['direction'].upper()}")
        lines.append("")
        
        lines.append(f"📊 Analysis Stats:")
        lines.append(f"   Weeks analyzed: {travel_summary['weeks_analyzed']}")
        lines.append(f"   Years covered: {travel_summary['years_covered']}")
        lines.append(f"   Time cycles: {travel_summary['cycles_completed']} complete loops through history")
        lines.append("")
        
        lines.append(f"📈 Historical Trades:")
        lines.append(f"   Total: {travel_summary['total_trades']}")
        lines.append(f"   Wins: {travel_summary['wins']} ({travel_summary['win_rate']:.1f}%)")
        lines.append(f"   Losses: {travel_summary['losses']}")
        lines.append(f"   Patterns discovered: {travel_summary['patterns_discovered']}")
        lines.append("")
        
        # === LIVE TRADING RESULTS ===
        lines.append("━"*80)
        lines.append("  💰 LIVE TRADING RESULTS (Real Killzone Trades)")
        lines.append("━"*80)
        
        try:
            live_trades = self.ledger.get_all_trades()
            
            if len(live_trades) == 0:
                lines.append("   No live trades executed yet")
                lines.append("   (Bot was in backtest mode or no setups met criteria)")
            else:
                live_wins = sum(1 for t in live_trades if t.get('won', False))
                live_losses = len(live_trades) - live_wins
                live_wr = (live_wins / len(live_trades) * 100) if live_trades else 0
                live_pnl = sum(t.get('pnl', 0) for t in live_trades)
                
                lines.append(f"   Total trades: {len(live_trades)}")
                lines.append(f"   Wins: {live_wins} ({live_wr:.1f}%)")
                lines.append(f"   Losses: {live_losses}")
                lines.append(f"   Total P&L: ${live_pnl:+,.2f}")
                lines.append("")
                
                if len(live_trades) >= 5:
                    # Show last 5 trades
                    lines.append("   Last 5 Trades:")
                    for trade in live_trades[-5:]:
                        result = "WIN" if trade.get('won') else "LOSS"
                        symbol = trade.get('symbol', 'N/A')
                        pnl = trade.get('pnl', 0)
                        timestamp = trade.get('timestamp', 'N/A')
                        lines.append(f"      {timestamp} | {symbol} | {result} | ${pnl:+.2f}")
        except Exception as e:
            lines.append(f"   Error loading live trades: {e}")
        
        lines.append("")
        
        # === EVOLUTION TIMELINE ===
        lines.append("━"*80)
        lines.append("  🧬 EVOLUTION TIMELINE")
        lines.append("━"*80)
        
        try:
            insights = self.learning_engine.analyze_patterns()
            
            # Quality threshold evolution
            current_threshold = self.learning_engine.current_quality_threshold if hasattr(self.learning_engine, 'current_quality_threshold') else 0.70
            lines.append(f"   Quality threshold: Started 0.70 → Now {current_threshold:.2f}")
            lines.append("")
            
            # Best performing patterns
            if 'by_symbol' in insights and insights['by_symbol']:
                lines.append("   Best Performing Symbols:")
                for sym, data in sorted(insights['by_symbol'].items(), 
                                       key=lambda x: x[1].get('win_rate', 0), reverse=True):
                    wr = data.get('win_rate', 0)
                    count = data.get('count', 0)
                    if count >= 3:
                        lines.append(f"      {sym}: {wr:.0f}% WR ({count} trades)")
            
            if 'by_killzone' in insights and insights['by_killzone']:
                lines.append("")
                lines.append("   Best Killzones:")
                for kz, data in sorted(insights['by_killzone'].items(),
                                      key=lambda x: x[1].get('win_rate', 0), reverse=True):
                    wr = data.get('win_rate', 0)
                    count = data.get('count', 0)
                    if count >= 3:
                        lines.append(f"      {kz}: {wr:.0f}% WR ({count} trades)")
        except Exception as e:
            lines.append(f"   Error analyzing patterns: {e}")
        
        lines.append("")
        
        # === CURRENT STATE ===
        lines.append("━"*80)
        lines.append("  📍 CURRENT STATE")
        lines.append("━"*80)
        lines.append(f"   ✅ Checkpoint saved automatically")
        lines.append(f"   ✅ When you restart, bot will resume from week: {travel_summary['current_week']['monday']}")
        lines.append(f"   ✅ All learning progress preserved in database")
        lines.append("")
        
        # === NEXT STEPS ===
        lines.append("━"*80)
        lines.append("  🚀 NEXT STEPS")
        lines.append("━"*80)
        lines.append("   To resume the bot:")
        lines.append("      python main.py")
        lines.append("")
        lines.append("   Bot will:")
        lines.append("      1. Load checkpoint and resume from saved week")
        lines.append("      2. Continue time travel backtest during idle")
        lines.append("      3. Trade live during killzones")
        lines.append("      4. Keep evolving strategy automatically")
        lines.append("")
        
        lines.append("="*80)
        lines.append("  Session Complete - All progress saved ✅")
        lines.append("="*80)
        lines.append("")
        
        return "\n".join(lines)
    
    def print_summary(self):
        """Print summary to console"""
        report = self.generate_full_report()
        print(report)
        logger.info("Summary report generated")
    
    def save_summary(self, path: str = "data/session_summary.txt"):
        """Save summary to file"""
        report = self.generate_full_report()
        with open(path, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"Summary saved to {path}")
