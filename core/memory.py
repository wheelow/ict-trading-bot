"""
Trade Ledger — Persistent Memory System

Stores every trade in a SQLite database with full context:
- Setup details (symbol, direction, entry, SL, TP, RR)
- Market context (HTF bias, killzone, time of day, day of week)
- Confluences present (FVG, OB, OTE zone)
- Outcome (win/loss, P&L, R-multiple)
- News context (was there news nearby?)
- Sentiment at entry
- Lessons learned (auto-generated tags)

This is the bot's LONG-TERM MEMORY. It survives restarts.
The learning engine reads from this to find patterns.
"""
import sqlite3
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
import os

logger = logging.getLogger("ict.memory")


@dataclass
class TradeRecord:
    """Complete record of a single trade — stored in the ledger."""
    # Identity
    id: Optional[int] = None
    ticket: int = 0                    # MT5 ticket

    # Setup
    symbol: str = ""
    direction: str = ""                # BUY or SELL
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    volume: float = 0.0
    planned_rr: float = 0.0

    # Market context at entry
    htf_bias: str = ""                 # bullish or bearish
    killzone: str = ""                 # London, NY AM, NY PM, Off-session
    day_of_week: int = 0               # 0=Monday, 6=Sunday
    hour_utc: int = 0                  # 0-23
    setup_type: str = ""               # OTE, OTE+FVG, OTE+OB
    sweep_direction: str = ""          # bullish_sweep or bearish_sweep
    confluence: str = ""               # FVG, OB, or OTE_only

    # News/sentiment context
    news_blocked: bool = False
    sentiment_score: float = 0.0

    # Outcome
    status: str = "open"               # open, won, lost, be
    close_price: float = 0.0
    pnl: float = 0.0
    rr_achieved: float = 0.0
    close_reason: str = ""             # SL, TP, breakeven, manual

    # Timestamps
    open_time: str = ""                # ISO format
    close_time: str = ""

    # Auto-generated lesson tags (filled by learning engine)
    lessons: str = "[]"                # JSON array of lesson strings


class TradeLedger:
    """SQLite-backed persistent trade storage."""

    def __init__(self, db_path: str = "data/trade_ledger.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        logger.info(f"Trade ledger initialized: {db_path}")

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER DEFAULT 0,
                symbol TEXT,
                direction TEXT,
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                volume REAL,
                planned_rr REAL,
                htf_bias TEXT,
                killzone TEXT,
                day_of_week INTEGER,
                hour_utc INTEGER,
                setup_type TEXT,
                sweep_direction TEXT,
                confluence TEXT,
                news_blocked INTEGER,
                sentiment_score REAL,
                status TEXT DEFAULT 'open',
                close_price REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                rr_achieved REAL DEFAULT 0,
                close_reason TEXT DEFAULT '',
                open_time TEXT,
                close_time TEXT,
                lessons TEXT DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                symbol TEXT,
                period_start TEXT,
                period_end TEXT,
                total_trades INTEGER,
                wins INTEGER,
                losses INTEGER,
                win_rate REAL,
                total_pnl REAL,
                avg_rr REAL,
                max_drawdown REAL,
                parameters TEXT,
                notes TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_date TEXT,
                category TEXT,
                rule TEXT,
                confidence REAL,
                sample_size INTEGER,
                active INTEGER DEFAULT 1
            )
        """)
        conn.commit()
        conn.close()

    def save_trade(self, trade: TradeRecord) -> int:
        """Save a trade to the ledger. Returns the trade ID."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                ticket, symbol, direction, entry, stop_loss, take_profit,
                volume, planned_rr, htf_bias, killzone, day_of_week, hour_utc,
                setup_type, sweep_direction, confluence, news_blocked,
                sentiment_score, status, close_price, pnl, rr_achieved,
                close_reason, open_time, close_time, lessons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.ticket, trade.symbol, trade.direction, trade.entry,
            trade.stop_loss, trade.take_profit, trade.volume, trade.planned_rr,
            trade.htf_bias, trade.killzone, trade.day_of_week, trade.hour_utc,
            trade.setup_type, trade.sweep_direction, trade.confluence,
            int(trade.news_blocked), trade.sentiment_score, trade.status,
            trade.close_price, trade.pnl, trade.rr_achieved, trade.close_reason,
            trade.open_time, trade.close_time, trade.lessons
        ))
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logger.debug(f"Saved trade #{trade_id}: {trade.symbol} {trade.direction}")
        return trade_id

    def update_trade_outcome(self, trade_id: int, status: str, close_price: float,
                              pnl: float, rr_achieved: float, close_reason: str,
                              close_time: str):
        """Update a trade's outcome after it closes."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            UPDATE trades
            SET status = ?, close_price = ?, pnl = ?, rr_achieved = ?,
                close_reason = ?, close_time = ?
            WHERE id = ?
        """, (status, close_price, pnl, rr_achieved, close_reason, close_time, trade_id))
        conn.commit()
        conn.close()
        logger.info(f"[LEDGER] Trade #{trade_id} closed: {status} | PnL={pnl:+.2f} | RR={rr_achieved:.2f}")

    def get_open_trades(self) -> List[Dict]:
        """Get all trades with status='open'."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades WHERE status = 'open'").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_recent_trades(self, days: int = 30) -> List[Dict]:
        """Get all trades from the last N days."""
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE open_time >= ? ORDER BY open_time DESC",
            (cutoff,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_trades(self) -> List[Dict]:
        """Get all trades ever recorded."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM trades ORDER BY open_time DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_lesson(self, category: str, rule: str, confidence: float, sample_size: int):
        """Save a learned rule/lesson."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO lessons (created_date, category, rule, confidence, sample_size, active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
              category, rule, confidence, sample_size))
        conn.commit()
        conn.close()
        logger.info(f"[LEDGER] New lesson learned: [{category}] {rule} (confidence={confidence:.2f}, n={sample_size})")

    def get_active_lessons(self) -> List[Dict]:
        """Get all active lessons/rules."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM lessons WHERE active = 1 ORDER BY confidence DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_backtest_result(self, symbol: str, period_start: str, period_end: str,
                              total_trades: int, wins: int, losses: int, win_rate: float,
                              total_pnl: float, avg_rr: float, max_drawdown: float,
                              parameters: Dict, notes: str = ""):
        """Save a backtest run result."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO backtest_results (
                run_date, symbol, period_start, period_end, total_trades,
                wins, losses, win_rate, total_pnl, avg_rr, max_drawdown,
                parameters, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            symbol, period_start, period_end, total_trades, wins, losses,
            win_rate, total_pnl, avg_rr, max_drawdown, json.dumps(parameters), notes
        ))
        conn.commit()
        conn.close()
        logger.info(f"[LEDGER] Backtest saved: {symbol} | {total_trades} trades | win_rate={win_rate:.1f}% | PnL={total_pnl:+.2f}")

    def get_pattern_win_rates(self, min_samples: int = 2) -> Dict[str, Dict]:
        """Query historical win rates grouped by symbol+direction+killzone+confluence.

        This is the core feedback loop for pattern-based signal rejection:
        if a specific setup pattern has a losing track record, the bot can
        reject similar setups in the future.

        Args:
            min_samples: Minimum number of closed trades to include a pattern.

        Returns:
            Dict like:
            {
                "US30_BUY_NY AM_FVG": {"wins": 3, "losses": 1, "total": 4, "win_rate": 75.0, ...},
                ...
            }
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                symbol, direction, killzone, confluence,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as losses,
                COUNT(*) as total
            FROM trades
            WHERE status IN ('won', 'lost')
            GROUP BY symbol, direction, killzone, confluence
            HAVING total >= ?
        """, (min_samples,)).fetchall()
        conn.close()

        result = {}
        for r in rows:
            key = f"{r['symbol']}_{r['direction']}_{r['killzone']}_{r['confluence']}"
            total = r['total']
            wins = r['wins']
            losses = r['losses']
            result[key] = {
                "wins": wins,
                "losses": losses,
                "total": total,
                "win_rate": (wins / total * 100) if total > 0 else 0,
                "symbol": r['symbol'],
                "direction": r['direction'],
                "killzone": r['killzone'],
                "confluence": r['confluence'],
            }
        return result

    def get_broad_pattern_win_rates(self, dimensions: list, min_samples: int = 3) -> Dict[str, Dict]:
        """Query win rates grouped by arbitrary dimension subset.

        Args:
            dimensions: Column names to group by, e.g. ['symbol', 'direction', 'killzone']
            min_samples: Minimum closed trades per group.

        Returns:
            Dict of joined-key -> stats.
        """
        valid = {'symbol', 'direction', 'killzone', 'confluence', 'day_of_week', 'hour_utc'}
        cols = [c for c in dimensions if c in valid]
        if not cols:
            return {}
        select = ', '.join(cols)
        group = ', '.join(cols)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        query = f"""
            SELECT {select},
                   SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as losses,
                   COUNT(*) as total
            FROM trades
            WHERE status IN ('won', 'lost')
            GROUP BY {group}
            HAVING total >= ?
        """
        rows = conn.execute(query, (min_samples,)).fetchall()
        conn.close()
        result = {}
        for r in rows:
            key = '_'.join(str(r[c]) for c in cols)
            total = r['total']
            wins = r['wins']
            losses = r['losses']
            result[key] = {
                "wins": wins, "losses": losses, "total": total,
                "win_rate": (wins / total * 100) if total > 0 else 0,
                **{c: r[c] for c in cols},
            }
        return result

    def get_day_confidence(self) -> Dict[int, Dict]:
        """Get win rates by day of week (0=Mon .. 4=Fri) from closed trades."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT day_of_week,
                   SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                   SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as losses,
                   COUNT(*) as total
            FROM trades
            WHERE status IN ('won', 'lost')
            GROUP BY day_of_week
        """).fetchall()
        conn.close()
        result = {}
        for r in rows:
            dow = r['day_of_week']
            total = r['total']; wins = r['wins']; losses = r['losses']
            result[dow] = {
                "wins": wins, "losses": losses, "total": total,
                "win_rate": (wins / total * 100) if total > 0 else 0,
            }
        return result

    def stats_summary(self) -> Dict:
        """Get high-level stats for the dashboard."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Overall stats
        overall = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN status = 'be' THEN 1 ELSE 0 END) as breakeven,
                SUM(pnl) as total_pnl,
                AVG(CASE WHEN status != 'open' THEN rr_achieved END) as avg_rr
            FROM trades
            WHERE status != 'open'
        """).fetchone()

        # By symbol
        by_symbol = conn.execute("""
            SELECT symbol,
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins,
                   SUM(pnl) as pnl
            FROM trades WHERE status != 'open'
            GROUP BY symbol
        """).fetchall()

        # By killzone
        by_killzone = conn.execute("""
            SELECT killzone,
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) as wins
            FROM trades WHERE status != 'open'
            GROUP BY killzone
        """).fetchall()

        conn.close()

        return {
            "total_trades": overall["total"] or 0,
            "wins": overall["wins"] or 0,
            "losses": overall["losses"] or 0,
            "breakeven": overall["breakeven"] or 0,
            "win_rate": ((overall["wins"] or 0) / overall["total"] * 100) if overall["total"] else 0,
            "total_pnl": overall["total_pnl"] or 0,
            "avg_rr": overall["avg_rr"] or 0,
            "by_symbol": [dict(r) for r in by_symbol],
            "by_killzone": [dict(r) for r in by_killzone],
        }
