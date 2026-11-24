"""
Simulator Broker — generates synthetic OHLCV data for indices (US30, NAS100, SP500)
and simulates trade execution. Lets the bot run end-to-end in any environment
without needing MT5/Windows.

The synthetic data is designed to mimic real index behavior:
- Realistic volatility per symbol
- Trending + ranging regimes
- Occasional news spikes
- Session-based volume (higher in NY/London killzones)
- Liquidity pools (equal highs/lows) that get swept — so the ICT strategy has setups
"""
from typing import Optional, List, Dict
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import logging

from .base import BaseBroker, Position

logger = logging.getLogger("ict.broker.simulator")


# Base prices and daily volatility (in price units)
SYMBOL_PARAMS = {
    "US30":  {"base_price": 38500.0, "volatility": 80.0,  "trend_bias": 0.00005},
    "NAS100": {"base_price": 17800.0, "volatility": 60.0,  "trend_bias": 0.00008},
    "SP500":  {"base_price": 5200.0,  "volatility": 12.0,  "trend_bias": 0.00006},
}


def _generate_synthetic_bars(symbol: str, n_bars: int = 500, timeframe_minutes: int = 5) -> pd.DataFrame:
    """Generate n_bars of synthetic OHLCV data ending at the current time.
    Uses geometric brownian motion with regime switching to create trends + ranges.
    """
    params = SYMBOL_PARAMS.get(symbol, SYMBOL_PARAMS["US30"])
    base = params["base_price"]
    vol = params["volatility"]
    trend = params["trend_bias"]

    np.random.seed(hash(symbol) % 2**31)

    # Time index — every `timeframe_minutes` minutes (integer spacing, no fractional seconds)
    end = datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    start = end - timedelta(minutes=timeframe_minutes * n_bars)
    timestamps = pd.date_range(start=start, periods=n_bars, freq=f"{timeframe_minutes}min")

    # Regime switching: 0 = ranging, 1 = up-trend, 2 = down-trend
    regime = np.zeros(n_bars, dtype=int)
    current_regime = 0
    regime_length = 0
    for i in range(n_bars):
        if regime_length <= 0:
            current_regime = np.random.choice([0, 1, 2], p=[0.5, 0.3, 0.2])
            regime_length = np.random.randint(20, 60)
        regime[i] = current_regime
        regime_length -= 1

    # Generate returns with regime-dependent drift
    returns = np.zeros(n_bars)
    for i in range(n_bars):
        noise = np.random.normal(0, vol / base)
        if regime[i] == 1:
            drift = trend * 5
        elif regime[i] == 2:
            drift = -trend * 5
        else:
            drift = 0
        # Add occasional news spikes (~1% chance per bar)
        if np.random.random() < 0.01:
            noise += np.random.normal(0, vol / base * 5)
        returns[i] = drift + noise

    # Build price series
    closes = base * np.exp(np.cumsum(returns))

    # Build OHLC around each close
    opens = np.roll(closes, 1)
    opens[0] = closes[0] * (1 - returns[0] / 2)
    intrabar_vol = vol * 0.4
    highs = np.maximum(opens, closes) + np.abs(np.random.normal(0, intrabar_vol, n_bars))
    lows = np.minimum(opens, closes) - np.abs(np.random.normal(0, intrabar_vol, n_bars))

    # Volume — higher during NY session (12-17 UTC)
    hours = np.array([t.hour for t in timestamps])
    base_vol = 1000
    session_multiplier = np.where((hours >= 12) & (hours <= 17), 2.5,
                          np.where((hours >= 7) & (hours <= 10), 1.8, 1.0))
    volume = (base_vol * session_multiplier * np.random.uniform(0.5, 1.5, n_bars)).astype(int)

    # Inject equal highs/lows (liquidity pools) followed by sweeps
    # ~ every 25 bars — this creates ICT-style liquidity sweep setups
    # that the strategy can detect and trade.
    i = 30
    while i < n_bars - 15:
        # Create equal lows (SSL) for a bullish sweep setup
        ref_low = lows[i]
        for j in range(i + 1, min(i + 4, n_bars - 5)):
            lows[j] = ref_low + np.random.uniform(-1, 1)
        # Then a sweep candle (5-8 bars later) pierces below and closes back up
        sweep_idx = min(i + 6, n_bars - 5)
        lows[sweep_idx] = ref_low - abs(np.random.normal(0, vol * 0.6))
        closes[sweep_idx] = ref_low + abs(np.random.normal(0, vol * 0.3))
        opens[sweep_idx] = ref_low - abs(np.random.normal(0, vol * 0.2))
        highs[sweep_idx] = max(closes[sweep_idx], opens[sweep_idx]) + abs(np.random.normal(0, vol * 0.2))
        # Then a strong up-move (BOS) over the next 2-3 bars
        for k in range(sweep_idx + 1, min(sweep_idx + 4, n_bars)):
            closes[k] = closes[k - 1] * (1 + abs(np.random.normal(0, vol / base * 2)))
            opens[k] = closes[k - 1]
            highs[k] = closes[k] + abs(np.random.normal(0, vol * 0.3))
            lows[k] = opens[k] - abs(np.random.normal(0, vol * 0.2))
        i += 25

    # Also inject equal highs (BSL) followed by bearish sweeps
    i = 17
    while i < n_bars - 15:
        ref_high = highs[i]
        for j in range(i + 1, min(i + 4, n_bars - 5)):
            highs[j] = ref_high + np.random.uniform(-1, 1)
        sweep_idx = min(i + 6, n_bars - 5)
        highs[sweep_idx] = ref_high + abs(np.random.normal(0, vol * 0.6))
        closes[sweep_idx] = ref_high - abs(np.random.normal(0, vol * 0.3))
        opens[sweep_idx] = ref_high + abs(np.random.normal(0, vol * 0.2))
        lows[sweep_idx] = min(closes[sweep_idx], opens[sweep_idx]) - abs(np.random.normal(0, vol * 0.2))
        for k in range(sweep_idx + 1, min(sweep_idx + 4, n_bars)):
            closes[k] = closes[k - 1] * (1 - abs(np.random.normal(0, vol / base * 2)))
            opens[k] = closes[k - 1]
            lows[k] = closes[k] - abs(np.random.normal(0, vol * 0.3))
            highs[k] = opens[k] + abs(np.random.normal(0, vol * 0.2))
        i += 25

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volume,
    }, index=timestamps)
    df.index.name = "time"
    return df


class SimulatorBroker(BaseBroker):
    """In-memory broker that simulates price feed and trade execution."""

    def __init__(self, symbols: List[str], starting_balance: float = 10000.0, timeframes: List[str] = None):
        self.symbols = symbols
        self.balance = starting_balance
        self.equity = starting_balance
        self.positions: List[Position] = []
        self.next_ticket = 100001

        # Pre-generate data for each symbol/timeframe
        self.timeframes = timeframes or ["M1", "M5", "M15", "H1"]
        self.data: Dict[str, Dict[str, pd.DataFrame]] = {}
        for sym in symbols:
            self.data[sym] = {}
            for tf in self.timeframes:
                minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}[tf]
                self.data[sym][tf] = _generate_synthetic_bars(sym, n_bars=500, timeframe_minutes=minutes)

        # Pointer to current bar (we step through time)
        self.current_bar_idx = 400  # start near the end of pre-generated data
        self.max_bar_idx = 499

        # Anchor simulated time so current bar sits inside the NY AM killzone
        # (07:00-10:00 NY local). Phase 1: anchor in NY time so the demo lands
        # inside a killzone regardless of DST. Previously fixed at 13:00 UTC,
        # which only matched NY AM during winter/EST — drifted an hour in summer.
        self._shift_data_into_killzone()

    def _shift_data_into_killzone(self):
        """Shift pre-generated bar timestamps so the current bar sits at ~08:30 NY local
        (algorithm liquidity-hunt start, Ch.20 p.308 — inside the NY AM killzone).
        DST-aware via zoneinfo."""
        from datetime import datetime as _dt
        try:
            from zoneinfo import ZoneInfo
            ny = ZoneInfo("America/New_York")
            utc = ZoneInfo("UTC")
            ref_sym = self.symbols[0]
            ref_tf = "M5"
            current_time = self.data[ref_sym][ref_tf].index[self.current_bar_idx]
            current_utc = current_time.to_pydatetime().replace(tzinfo=utc)
            today_ny = current_utc.astimezone(ny).date()
            target_ny = _dt.combine(today_ny, _dt.min.time()).replace(hour=8, minute=30, tzinfo=ny)
            target_utc_naive = target_ny.astimezone(utc).replace(tzinfo=None)
            delta = target_utc_naive - current_time.to_pydatetime()
        except Exception:
            # Fallback: simple hour shift to 12:30 UTC (approx 08:30 EST / 07:30 EDT)
            ref_sym = self.symbols[0]
            ref_tf = "M5"
            current_time = self.data[ref_sym][ref_tf].index[self.current_bar_idx]
            target_time = current_time.replace(hour=12, minute=30, second=0, microsecond=0)
            delta = target_time - current_time

        for sym in self.symbols:
            for tf in self.timeframes:
                df = self.data[sym][tf]
                df.index = df.index + delta
                self.data[sym][tf] = df

    def current_simulated_time(self) -> datetime:
        """Return the timestamp of the current bar (for killzone/news checks)."""
        ref_sym = self.symbols[0]
        ref_tf = "M5"
        if ref_sym in self.data and ref_tf in self.data[ref_sym]:
            idx = min(self.current_bar_idx, len(self.data[ref_sym][ref_tf]) - 1)
            return self.data[ref_sym][ref_tf].index[idx].to_pydatetime()
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def connect(self) -> bool:
        logger.info(f"Simulator connected | symbols={self.symbols} | balance={self.balance}")
        logger.info(f"Pre-generated {len(self.symbols)} symbols x {len(self.timeframes)} timeframes of synthetic data")
        logger.info(f"Simulated time anchored to NY AM killzone for demo")
        return True

    def disconnect(self):
        logger.info("Simulator disconnected")

    def step_time(self):
        """Advance simulated time by one bar (used by main loop in backtest mode)."""
        if self.current_bar_idx < self.max_bar_idx:
            self.current_bar_idx += 1
        else:
            # Extend the data by appending new bars
            for sym in self.symbols:
                for tf in self.timeframes:
                    df = self.data[sym][tf]
                    last_close = df["close"].iloc[-1]
                    last_time = df.index[-1]
                    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}[tf]
                    new_time = last_time + timedelta(minutes=minutes)
                    # Generate one new bar
                    params = SYMBOL_PARAMS.get(sym, SYMBOL_PARAMS["US30"])
                    vol = params["volatility"]
                    noise = np.random.normal(0, vol / params["base_price"])
                    new_close = last_close * (1 + noise)
                    new_open = last_close
                    new_high = max(new_open, new_close) + abs(np.random.normal(0, vol * 0.4))
                    new_low = min(new_open, new_close) - abs(np.random.normal(0, vol * 0.4))
                    new_vol = int(1000 * np.random.uniform(0.5, 2.0))
                    new_row = pd.DataFrame({
                        "open": [new_open], "high": [new_high], "low": [new_low],
                        "close": [new_close], "volume": [new_vol],
                    }, index=[new_time])
                    self.data[sym][tf] = pd.concat([df, new_row])
            self.current_bar_idx += 1
            self.max_bar_idx = self.current_bar_idx

    def get_history(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame:
        df = self.data[symbol][timeframe]
        end_idx = self.current_bar_idx + 1
        start_idx = max(0, end_idx - count)
        return df.iloc[start_idx:end_idx].copy()

    def get_tick(self, symbol: str) -> float:
        df = self.data[symbol]["M5"]
        return float(df["close"].iloc[self.current_bar_idx])

    def open_position(self, symbol: str, direction: str, volume: float, stop_loss: float, take_profit: float, entry_price: float = None) -> Optional[Position]:
        # If entry_price is provided (limit order at OTE zone), use it.
        # Otherwise use current market price (market order).
        price = entry_price if entry_price is not None else self.get_tick(symbol)
        pos = Position(
            symbol=symbol,
            direction=direction,
            volume=volume,
            entry=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            open_time=datetime.now(timezone.utc).replace(tzinfo=None),
            ticket=self.next_ticket,
        )
        self.next_ticket += 1
        self.positions.append(pos)
        logger.info(f"[SIM] Opened {direction} {symbol} vol={volume} @ {price:.2f} SL={stop_loss:.2f} TP={take_profit:.2f}")
        return pos

    def close_position(self, position: Position, price: Optional[float] = None, partial_volume: Optional[float] = None):
        if price is None:
            price = self.get_tick(position.symbol)
        close_vol = partial_volume if partial_volume is not None else position.volume
        if position.direction == "BUY":
            pnl = (price - position.entry) * close_vol
        else:
            pnl = (position.entry - price) * close_vol
        self.balance += pnl
        if partial_volume is not None:
            position.volume -= close_vol
            logger.info(f"[SIM] Partial-closed {position.direction} {position.symbol} @ {price:.2f} | "
                        f"closed_vol={close_vol:.2f} remaining={position.volume:.2f} | PnL={pnl:+.2f} | Balance={self.balance:.2f}")
        else:
            if position in self.positions:
                self.positions.remove(position)
            logger.info(f"[SIM] Closed {position.direction} {position.symbol} @ {price:.2f} | PnL={pnl:+.2f} | Balance={self.balance:.2f}")
        return True

    def modify_position(self, position: Position, new_stop_loss: Optional[float] = None, new_take_profit: Optional[float] = None):
        if new_stop_loss is not None:
            position.stop_loss = new_stop_loss
        if new_take_profit is not None:
            position.take_profit = new_take_profit
        return True

    def get_open_positions(self) -> List[Position]:
        return list(self.positions)

    def account_balance(self) -> float:
        # Update equity with floating P&L
        floating = 0.0
        for pos in self.positions:
            price = self.get_tick(pos.symbol)
            if pos.direction == "BUY":
                floating += (price - pos.entry) * pos.volume
            else:
                floating += (pos.entry - price) * pos.volume
        self.equity = self.balance + floating
        return self.balance

    def is_market_open(self, symbol: str) -> bool:
        return True  # simulator is always open

    def check_position_hits(self):
        """Check whether any open position has hit its SL or TP — called after every bar.
        Returns a list of (position, close_price, status) tuples for closed positions.
        """
        closed = []
        for pos in list(self.positions):
            df = self.data[pos.symbol]["M5"]
            bar = df.iloc[self.current_bar_idx]
            hit_sl = False
            hit_tp = False
            if pos.direction == "BUY":
                if bar["low"] <= pos.stop_loss:
                    hit_sl = True
                    self.close_position(pos, price=pos.stop_loss)
                elif bar["high"] >= pos.take_profit:
                    hit_tp = True
                    self.close_position(pos, price=pos.take_profit)
            else:  # SELL
                if bar["high"] >= pos.stop_loss:
                    hit_sl = True
                    self.close_position(pos, price=pos.stop_loss)
                elif bar["low"] <= pos.take_profit:
                    hit_tp = True
                    self.close_position(pos, price=pos.take_profit)
            if hit_sl:
                logger.info(f"[SIM] SL hit on {pos.symbol} {pos.direction}")
                closed.append((pos, pos.stop_loss, "lost"))
            elif hit_tp:
                logger.info(f"[SIM] TP hit on {pos.symbol} {pos.direction}")
                closed.append((pos, pos.take_profit, "won"))
        return closed
