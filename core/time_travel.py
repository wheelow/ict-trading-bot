"""
Time Travel System - Week-by-Week Historical Analysis

The bot travels through time analyzing complete trading weeks (Mon-Fri).
Starts from present, goes backward to 2020, then forward to present, repeating forever.

This enables the bot to learn from EVERY market condition in history.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Tuple, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger("ict.time_travel")

@dataclass
class TravelCheckpoint:
    """Saves the bot's position in time"""
    current_date: str  # ISO format: "2026-07-14"
    direction: str  # "backward" or "forward"
    weeks_analyzed: int
    total_trades: int
    total_wins: int
    total_losses: int
    patterns_discovered: int
    cycles_completed: int
    last_updated: str

class TimeTraveler:
    """
    Manages week-by-week time travel through historical data.
    
    Features:
    - Analyzes complete trading weeks (Monday-Friday)
    - Goes backward from present to earliest data (2020)
    - Reverses and goes forward back to present
    - Repeats infinitely (oscillating through time)
    - Saves checkpoint after each week
    - Resumes from exact position on restart
    """
    
    def __init__(self, checkpoint_path: str = "data/time_travel_checkpoint.json",
                 earliest_year: int = 2020):
        """
        Initialize time traveler.
        
        Args:
            checkpoint_path: Where to save/load progress
            earliest_year: How far back in time to go (default 2020)
        """
        self.checkpoint_path = Path(checkpoint_path)
        self.earliest_date = datetime(earliest_year, 1, 1)
        self.latest_date = datetime.now()
        
        # State
        self.current_date: datetime = None
        self.direction: str = "backward"  # or "forward"
        self.weeks_analyzed: int = 0
        self.total_trades: int = 0
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.patterns_discovered: int = 0
        self.cycles_completed: int = 0
        
        # Load checkpoint if exists, otherwise start fresh
        self._load_or_initialize()
        
        logger.info(f"Time Traveler initialized")
        logger.info(f"  Current position: {self.current_date.date()}")
        logger.info(f"  Direction: {self.direction}")
        logger.info(f"  Weeks analyzed: {self.weeks_analyzed}")
        logger.info(f"  Range: {self.earliest_date.date()} to {self.latest_date.date()}")
    
    def _load_or_initialize(self):
        """Load checkpoint or start from yesterday"""
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, 'r') as f:
                    data = json.load(f)
                
                self.current_date = datetime.fromisoformat(data['current_date'])
                self.direction = data['direction']
                self.weeks_analyzed = data['weeks_analyzed']
                self.total_trades = data['total_trades']
                self.total_wins = data['total_wins']
                self.total_losses = data['total_losses']
                self.patterns_discovered = data['patterns_discovered']
                self.cycles_completed = data['cycles_completed']
                
                logger.info(f"✅ Checkpoint loaded - resuming from {self.current_date.date()}")
            except Exception as e:
                logger.warning(f"Failed to load checkpoint: {e}")
                self._initialize_fresh()
        else:
            self._initialize_fresh()
    
    def _initialize_fresh(self):
        """Start from yesterday, going backward"""
        # Start from most recent complete week
        today = datetime.now()
        self.current_date = self._get_week_start(today - timedelta(days=7))
        self.direction = "backward"
        self.weeks_analyzed = 0
        self.total_trades = 0
        self.total_wins = 0
        self.total_losses = 0
        self.patterns_discovered = 0
        self.cycles_completed = 0
        
        logger.info(f"🆕 Starting fresh from {self.current_date.date()}")
    
    def _get_week_start(self, date: datetime) -> datetime:
        """Get Monday of the week containing this date"""
        # Monday = 0, Sunday = 6
        days_since_monday = date.weekday()
        monday = date - timedelta(days=days_since_monday)
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)
    
    def get_current_week(self) -> Tuple[datetime, datetime]:
        """
        Get current week to analyze (Monday-Friday).
        
        Returns:
            (monday_datetime, friday_datetime)
        """
        monday = self._get_week_start(self.current_date)
        friday = monday + timedelta(days=4)  # Monday + 4 = Friday
        
        return monday, friday
    
    def advance(self):
        """Move to next week (backward or forward in time)"""
        if self.direction == "backward":
            # Go back one week
            self.current_date -= timedelta(weeks=1)
            
            # Check if hit earliest boundary
            if self.current_date <= self.earliest_date:
                logger.info(f"🔄 Reached earliest date ({self.earliest_date.date()}) - REVERSING to go FORWARD")
                self.direction = "forward"
                self.cycles_completed += 1
                self.current_date = self.earliest_date
        
        else:  # forward
            # Go forward one week
            self.current_date += timedelta(weeks=1)
            
            # Check if hit latest boundary
            if self.current_date >= self.latest_date:
                logger.info(f"🔄 Reached present date ({self.latest_date.date()}) - REVERSING to go BACKWARD")
                self.direction = "backward"
                self.cycles_completed += 1
                # Start from last complete week
                self.current_date = self._get_week_start(datetime.now() - timedelta(days=7))
        
        self.weeks_analyzed += 1
    
    def record_week_results(self, trades: int, wins: int, losses: int, patterns: int = 0):
        """
        Record results from analyzing a week.
        
        Args:
            trades: Number of trades found that week
            wins: Number of winning trades
            losses: Number of losing trades
            patterns: New patterns discovered
        """
        self.total_trades += trades
        self.total_wins += wins
        self.total_losses += losses
        self.patterns_discovered += patterns
    
    def save_checkpoint(self):
        """Save current position to disk"""
        checkpoint = TravelCheckpoint(
            current_date=self.current_date.isoformat(),
            direction=self.direction,
            weeks_analyzed=self.weeks_analyzed,
            total_trades=self.total_trades,
            total_wins=self.total_wins,
            total_losses=self.total_losses,
            patterns_discovered=self.patterns_discovered,
            cycles_completed=self.cycles_completed,
            last_updated=datetime.now().isoformat()
        )
        
        # Ensure directory exists
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save to JSON
        with open(self.checkpoint_path, 'w') as f:
            json.dump(asdict(checkpoint), f, indent=2)
        
        logger.debug(f"💾 Checkpoint saved: {self.current_date.date()} ({self.direction})")
    
    def get_progress_summary(self) -> Dict:
        """Get summary of time travel progress"""
        monday, friday = self.get_current_week()
        
        # Calculate time period covered
        if self.weeks_analyzed > 0:
            years_covered = self.weeks_analyzed / 52.0
        else:
            years_covered = 0
        
        # Calculate win rate
        if self.total_trades > 0:
            win_rate = (self.total_wins / self.total_trades) * 100
        else:
            win_rate = 0
        
        return {
            "current_week": {
                "monday": monday.strftime("%Y-%m-%d"),
                "friday": friday.strftime("%Y-%m-%d"),
            },
            "direction": self.direction,
            "weeks_analyzed": self.weeks_analyzed,
            "years_covered": round(years_covered, 1),
            "cycles_completed": self.cycles_completed,
            "total_trades": self.total_trades,
            "wins": self.total_wins,
            "losses": self.total_losses,
            "win_rate": round(win_rate, 1),
            "patterns_discovered": self.patterns_discovered,
            "time_range": {
                "earliest": self.earliest_date.strftime("%Y-%m-%d"),
                "latest": self.latest_date.strftime("%Y-%m-%d"),
            }
        }
    
    def print_progress(self):
        """Print current progress in readable format"""
        summary = self.get_progress_summary()
        
        print()
        print("="*80)
        print("  🕐 TIME TRAVEL PROGRESS")
        print("="*80)
        
        monday = summary['current_week']['monday']
        friday = summary['current_week']['friday']
        direction_symbol = "⏪" if self.direction == "backward" else "⏩"
        
        print(f"{direction_symbol} Current Week: {monday} to {friday} (going {self.direction})")
        print(f"📊 Analysis Progress:")
        print(f"   Weeks analyzed: {summary['weeks_analyzed']}")
        print(f"   Years covered: {summary['years_covered']}")
        print(f"   Cycles completed: {summary['cycles_completed']}")
        print()
        print(f"📈 Historical Backtest Results:")
        print(f"   Total trades: {summary['total_trades']}")
        print(f"   Wins: {summary['wins']} | Losses: {summary['losses']}")
        print(f"   Win rate: {summary['win_rate']}%")
        print(f"   Patterns discovered: {summary['patterns_discovered']}")
        print()
        print(f"🗓️  Time Range: {summary['time_range']['earliest']} to {summary['time_range']['latest']}")
        print("="*80)
        print()
    
    def is_analyzing_recent_data(self, days_threshold: int = 30) -> bool:
        """
        Check if currently analyzing recent data (within last N days).
        Useful for weighting recent learnings higher.
        """
        days_ago = (datetime.now() - self.current_date).days
        return days_ago <= days_threshold
