"""
Risk Manager — Position sizing, daily drawdown limits, trade counter.

Aggressive profile (default):
- 2% risk per trade
- 5% max daily drawdown -> stop trading for the day
- Max 5 trades per day
- Move stop to breakeven at 1R
- Partial close (50%) at 2R
- 1:3 minimum reward-to-risk
"""
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime, date
import logging

logger = logging.getLogger("ict.risk")


@dataclass
class TradeRecord:
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    volume: float
    open_time: datetime
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl: float = 0.0
    status: str = "open"     # "open", "won", "lost", "be"
    rr_achieved: float = 0.0
    ticket: int = 0          # MT5 ticket for sync
    # >>> FIX: persistent BE/partial state. Previously these flags lived only on
    # the transient Position objects returned by broker.get_open_positions(),
    # which reset to False every loop — causing the bot to re-issue breakeven
    # and partial-close modify requests every 5 seconds. Storing them on the
    # long-lived TradeRecord means they survive across main-loop iterations.
    be_applied: bool = False
    partial_closed: bool = False


@dataclass
class RiskConfig:
    account_balance: float = 10000.0
    risk_per_trade_pct: float = 2.0
    max_daily_drawdown_pct: float = 5.0
    max_trades_per_day: int = 5
    reward_to_risk: float = 3.0
    breakeven_at_rr: float = 1.0
    partial_close_at_rr: float = 2.0
    trailing_stop_pips: float = 10.0


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.start_of_day_balance = cfg.account_balance
        self.current_balance = cfg.account_balance
        self.trades: List[TradeRecord] = []
        self.daily_trade_count = 0
        self.daily_loss = 0.0
        self.current_day: Optional[date] = None
        self.trading_halted = False
        self.halt_reason = ""
        # Real $ risk of the most recent position sizing call, computed from
        # the broker's tick_value/tick_size (set in calculate_position_size).
        # main.py reads this to log accurate per-trade risk instead of the
        # config pip_value fallback, which under-reports US100/US500 by ~10x.
        self.last_actual_risk = 0.0

    def _check_day_rollover(self, now: datetime):
        today = now.date()
        if self.current_day != today:
            self.current_day = today
            self.daily_trade_count = 0
            self.daily_loss = 0.0
            self.trading_halted = False
            self.halt_reason = ""
            self.start_of_day_balance = self.current_balance
            logger.info(f"--- New trading day: {today} | Start balance: {self.current_balance:.2f} ---")

    def can_trade(self, now: datetime) -> tuple:
        """Check whether the bot is allowed to open new trades right now."""
        self._check_day_rollover(now)

        if self.trading_halted:
            return False, f"Trading halted: {self.halt_reason}"

        if self.daily_trade_count >= self.cfg.max_trades_per_day:
            self.trading_halted = True
            self.halt_reason = "Max trades per day reached"
            return False, self.halt_reason

        max_loss = self.start_of_day_balance * (self.cfg.max_daily_drawdown_pct / 100.0)
        if self.daily_loss <= -max_loss:
            self.trading_halted = True
            self.halt_reason = f"Daily drawdown limit hit ({self.cfg.max_daily_drawdown_pct}%)"
            logger.warning(self.halt_reason)
            return False, self.halt_reason

        return True, "OK"

    def calculate_position_size(self, entry: float, stop_loss: float, symbol_pip_value: float = 1.0, min_volume: float = 0.01,
                                 max_volume: float = 5.0, symbol_name: str = "", broker_symbol_info=None) -> float:
        """Calculate position volume based on risk percentage.

        Args:
            entry: entry price
            stop_loss: stop loss price
            symbol_pip_value: $ value of 1 point per 1.0 lot (from config)
            min_volume: minimum volume allowed by broker
            max_volume: safety cap to prevent oversized positions (default 5 lots)
            symbol_name: symbol name for logging
            broker_symbol_info: optional MT5 symbol_info object with real contract size

        Returns:
            Volume in lots, properly sized to risk exactly risk_per_trade_pct of balance.
        """
        risk_amount = self.current_balance * (self.cfg.risk_per_trade_pct / 100.0)

        # If we have real broker symbol info, use it for accurate sizing
        actual_pip_value = symbol_pip_value
        if broker_symbol_info is not None:
            try:
                # MT5's trade_tick_value = $ value of 1 tick (point) per 1.0 lot
                # trade_tick_size = the tick size in price units
                tick_value = getattr(broker_symbol_info, 'trade_tick_value', 0) or 0
                tick_size = getattr(broker_symbol_info, 'trade_tick_size', 0) or 0
                if tick_value > 0 and tick_size > 0:
                    # $ per 1.0 point = tick_value / tick_size
                    actual_pip_value = tick_value / tick_size
                    logger.info(f"[RISK] {symbol_name}: using MT5 tick_value={tick_value}, tick_size={tick_size}, "
                                f"pip_value={actual_pip_value:.4f}")
            except Exception as e:
                logger.warning(f"[RISK] Could not read broker symbol info: {e}")

        # Calculate risk per 1.0 lot
        sl_distance = abs(entry - stop_loss)
        risk_per_lot = sl_distance * actual_pip_value

        if risk_per_lot <= 0:
            return 0.0

        # Volume = risk_amount / risk_per_lot
        volume = risk_amount / risk_per_lot

        # SAFETY CAP: never exceed max_volume lots per trade
        if volume > max_volume:
            logger.warning(f"[RISK] {symbol_name}: calculated volume {volume:.2f} capped to {max_volume:.2f} (safety limit)")
            volume = max_volume

        # Round down to nearest 0.01
        volume = max(min_volume, round(volume * 100) / 100)

        # Final validation: verify actual risk doesn't exceed 2x configured (safety net)
        actual_risk = volume * risk_per_lot
        max_allowed_risk = risk_amount * 2  # allow 2x as safety margin for rounding
        if actual_risk > max_allowed_risk:
            logger.warning(f"[RISK] {symbol_name}: actual risk ${actual_risk:.2f} exceeds limit ${max_allowed_risk:.2f}, "
                          f"reducing volume")
            volume = max(min_volume, round((risk_amount / risk_per_lot) * 100) / 100)
            actual_risk = volume * risk_per_lot  # recompute after the cap

        # Expose the final dollar risk so callers (main.py trade-logging) can
        # report the true figure instead of the pip_value fallback. Set after
        # any safety-cap reduction so it reflects what was actually traded.
        self.last_actual_risk = actual_risk

        logger.info(f"[RISK] {symbol_name}: entry={entry}, SL={stop_loss}, sl_dist={sl_distance:.2f}, "
                    f"pip_value=${actual_pip_value:.4f}/point, risk_per_lot=${risk_per_lot:.2f}, "
                    f"risk_amount=${risk_amount:.2f}, volume={volume:.2f} lots, actual_risk=${actual_risk:.2f}")

        return volume

    def register_trade(self, trade: TradeRecord, now: datetime):
        self._check_day_rollover(now)
        self.trades.append(trade)
        self.daily_trade_count += 1
        logger.info(f"Trade opened: {trade.symbol} {trade.direction} vol={trade.volume} entry={trade.entry:.2f} SL={trade.stop_loss:.2f} TP={trade.take_profit:.2f}")

    def close_trade(self, trade: TradeRecord, close_price: float, now: datetime, status: str = "closed"):
        trade.close_time = now
        trade.close_price = close_price
        trade.status = status

        # Compute P&L
        if trade.direction == "BUY":
            diff = close_price - trade.entry
        else:
            diff = trade.entry - close_price
        trade.pnl = diff * trade.volume
        trade.rr_achieved = diff / abs(trade.entry - trade.stop_loss) if abs(trade.entry - trade.stop_loss) > 0 else 0

        self.current_balance += trade.pnl
        if trade.pnl < 0:
            self.daily_loss += trade.pnl

        logger.info(
            f"Trade closed: {trade.symbol} {trade.direction} | "
            f"PnL={trade.pnl:+.2f} | Balance={self.current_balance:.2f} | "
            f"RR={trade.rr_achieved:.2f} | Status={status}"
        )

    def should_move_to_breakeven(self, trade: TradeRecord, current_price: float) -> bool:
        """Move stop to breakeven when price reaches breakeven_at_rr."""
        risk = abs(trade.entry - trade.stop_loss)
        if risk <= 0:
            return False
        if trade.direction == "BUY":
            r = (current_price - trade.entry) / risk
            return r >= self.cfg.breakeven_at_rr
        else:
            r = (trade.entry - current_price) / risk
            return r >= self.cfg.breakeven_at_rr

    def should_partial_close(self, trade: TradeRecord, current_price: float) -> bool:
        """Partial close at partial_close_at_rr."""
        risk = abs(trade.entry - trade.stop_loss)
        if risk <= 0:
            return False
        if trade.direction == "BUY":
            r = (current_price - trade.entry) / risk
        else:
            r = (trade.entry - current_price) / risk
        return r >= self.cfg.partial_close_at_rr

    def stats(self) -> dict:
        closed = [t for t in self.trades if t.status in ("won", "lost", "be")]
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl < 0]
        total_pnl = sum(t.pnl for t in closed)
        win_rate = (len(wins) / len(closed) * 100) if closed else 0
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
        return {
            "total_trades": len(closed),
            "open_trades": len([t for t in self.trades if t.status == "open"]),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "current_balance": self.current_balance,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "daily_trade_count": self.daily_trade_count,
            "trading_halted": self.trading_halted,
        }

    def sync_balance(self, broker_balance: float):
        """Sync the risk manager's balance with the broker's actual balance.
        The broker is the source of truth — the config's ``account_balance`` is
        only a fallback used before the first sync (e.g. before connecting, or
        in pure simulator mode).

        On the first sync of a session this also seeds ``start_of_day_balance``
        so the daily drawdown limit is computed against reality, not the stale
        config value. (Otherwise ``_check_day_rollover`` would inherit the config
        balance on the first day, under/over-stating drawdown headroom.)
        """
        if self.current_day is None:
            # First sync of the session — treat the real balance as the day's
            # baseline too. After this, _check_day_rollover manages it normally.
            self.start_of_day_balance = broker_balance
        self.current_balance = broker_balance


# === ADDED FOR ICT BOOK IMPLEMENTATION (Ch.32) ===

def calculate_stop_loss_by_book(
    entry_type: str,
    entry_price: float,
    direction: str,
    atr: float,
    asian_range_low: Optional[float] = None,
    asian_range_high: Optional[float] = None,
    lod: Optional[float] = None,  # Low of day
    hod: Optional[float] = None,  # High of day
    sweep_extreme: Optional[float] = None,
) -> float:
    """
    Calculate stop-loss per book's table (Ch.32 p.382-385)
    
    Entry types and their stop rules:
    - "cbdr_overlap": 15 points under/above
    - "asian_range_run": 20 points under/above
    - "sellstop_raid": 15 points under entry
    - "buystop_raid": 15 points above entry
    - "first_ob_retrace": 5 points under LOD (buy) or above HOD (sell)
    - "second_return": 15 points under LOD (buy) or above HOD (sell)
    - "other": 50% ADR from Asian range boundary
    
    Book reference: Ch.32 "Placing Stop Losses In Buy/Short Daytrades"
    """
    # Default buffer (for ES/SP500 - scale for other instruments)
    points_15 = 15.0
    points_20 = 20.0
    points_5 = 5.0
    
    if direction == "BUY":
        if entry_type == "cbdr_overlap":
            # "If trading CBDR overlap with PDA - Stop is 15 points under"
            return entry_price - points_15
        
        elif entry_type == "asian_range_run":
            # "If trading run under Asian Range - Stop is 20 points under"
            if asian_range_low:
                return asian_range_low - points_20
            return entry_price - points_20
        
        elif entry_type == "sellstop_raid" and sweep_extreme:
            # "If trading ANY Sell Stop Raid - 15 points under the low/entry"
            return sweep_extreme - points_15
        
        elif entry_type == "first_ob_retrace" and lod:
            # "If trading 1st retracement into +OB - 5 points under LOD"
            return lod - points_5
        
        elif entry_type == "second_return" and lod:
            # "If trading 2nd return for Sell Stops - 15 points under LOD"
            return lod - points_15
        
        else:
            # "Use 50% ADR of last 5 days subtracted from Asian Range Low"
            # Fallback: use 25 points as conservative default
            if asian_range_low:
                return asian_range_low - 25.0
            return entry_price - 25.0
    
    else:  # SELL
        if entry_type == "cbdr_overlap":
            return entry_price + points_15
        
        elif entry_type == "asian_range_run":
            if asian_range_high:
                return asian_range_high + points_20
            return entry_price + points_20
        
        elif entry_type == "buystop_raid" and sweep_extreme:
            return sweep_extreme + points_15
        
        elif entry_type == "first_ob_retrace" and hod:
            return hod + points_5
        
        elif entry_type == "second_return" and hod:
            return hod + points_15
        
        else:
            if asian_range_high:
                return asian_range_high + 25.0
            return entry_price + 25.0

def calculate_take_profit_levels_by_book(
    entry_price: float,
    direction: str,
    asian_range: Optional[tuple] = None,  # (low, high)
    cbdr_range: Optional[tuple] = None,  # (low, high)
    pdh: Optional[float] = None,
    pdl: Optional[float] = None,
    five_day_adr: Optional[float] = None,
) -> List[tuple]:
    """
    Calculate take-profit levels per book's table (Ch.32 p.383, 386)
    
    Book guidance for scaling out:
    1. Take something off at 10-15 points
    2. Scale off every 2 STD of Asian Range or CBDR
    3. PDH/PDL ±2-7 points
    4. 50% of 1h price range
    5. 60-80% off at 5-day ADR projection
    
    Returns:
        List of (price, percentage, reason) tuples
    """
    levels = []
    
    # Level 1: Quick 10-15 points
    if direction == "BUY":
        levels.append((entry_price + 12.5, 0.20, "Quick 10-15pts (20% off)"))
    else:
        levels.append((entry_price - 12.5, 0.20, "Quick 10-15pts (20% off)"))
    
    # Level 2: 2 STD of Asian or CBDR
    if asian_range:
        ar_size = asian_range[1] - asian_range[0]
        two_std = ar_size * 2
        if direction == "BUY":
            levels.append((entry_price + two_std, 0.30, "2 STD Asian Range (30% off)"))
        else:
            levels.append((entry_price - two_std, 0.30, "2 STD Asian Range (30% off)"))
    
    # Level 3: PDH/PDL ±2-7 points
    if direction == "BUY" and pdh:
        levels.append((pdh + 5, 0.25, "PDH +5pts (25% off)"))
    elif direction == "SELL" and pdl:
        levels.append((pdl - 5, 0.25, "PDL -5pts (25% off)"))
    
    # Level 4: 5-day ADR projection (60-80% off)
    if five_day_adr:
        if direction == "BUY":
            levels.append((entry_price + five_day_adr, 0.25, "5-day ADR (final 25%)"))
        else:
            levels.append((entry_price - five_day_adr, 0.25, "5-day ADR (final 25%)"))
    
    return levels
