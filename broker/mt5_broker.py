"""
MT5 Broker Adapter — connects to MetaTrader5 terminal for live/paper trading.

REQUIREMENTS:
- Windows OS (or Wine on Linux, advanced setup)
- MetaTrader 5 terminal installed and logged in
- `pip install MetaTrader5`
- Demo/paper account credentials from your broker (ICMarkets, Pepperstone, FTMO, etc.)

The adapter wraps MT5's Python API in our BaseBroker interface so the bot
doesn't care whether it's running against MT5 or the simulator.
"""
from typing import Optional, List
from datetime import datetime, timedelta, timezone
import logging
import pandas as pd

from .base import BaseBroker, Position

logger = logging.getLogger("ict.broker.mt5")

# Lazy import — MetaTrader5 only available on Windows
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


# Map our timeframe strings to MT5 constants
TF_MAP = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class MT5Broker(BaseBroker):
    def __init__(self, login: int, password: str, server: str, path: str = ""):
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self.connected = False

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            logger.error("MetaTrader5 Python package is not installed. Run: pip install MetaTrader5")
            return False

        kwargs = {
            "login": self.login,
            "password": self.password,
            "server": self.server,
        }
        if self.path:
            kwargs["path"] = self.path

        if not mt5.initialize(**kwargs):
            logger.error(f"MT5 initialize() failed: {mt5.last_error()}")
            return False

        info = mt5.account_info()
        if info is None:
            logger.error("MT5 account_info() returned None — check credentials")
            return False

        self.connected = True
        logger.info(f"Connected to MT5: {info.server} | account={info.login} | balance={info.balance} {info.currency}")
        return True

    def disconnect(self):
        if MT5_AVAILABLE and self.connected:
            mt5.shutdown()
            self.connected = False

    def get_history(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame:
        tf_const = getattr(mt5, TF_MAP.get(timeframe, "TIMEFRAME_M5"))
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]]

    def get_history_by_date(self, symbol: str, timeframe: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
        """Fetch historical bars between two dates — for deep backtesting.
        MT5 allows up to 65536 bars per request.
        """
        tf_const = getattr(mt5, TF_MAP.get(timeframe, "TIMEFRAME_M5"))
        rates = mt5.copy_rates_range(symbol, tf_const, date_from, date_to)
        if rates is None or len(rates) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]]

    def get_tick(self, symbol: str) -> float:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return 0.0
        return tick.bid if tick.bid else tick.last

    def _normalize_price(self, symbol: str, price: float) -> float:
        """Normalize price to the symbol's digits and point size."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return float(price)
        digits = info.digits
        point = info.point
        # Round to the correct number of digits
        normalized = round(float(price) / point) * point
        return round(normalized, digits)

    def _normalize_volume(self, symbol: str, volume: float) -> float:
        """Normalize volume to the symbol's volume step."""
        info = mt5.symbol_info(symbol)
        if info is None:
            return float(volume)
        step = info.volume_step
        min_vol = info.volume_min
        max_vol = info.volume_max
        # Round down to nearest step
        vol = max(min_vol, round(float(volume) / step) * step)
        vol = min(max_vol, vol)
        # Round to step's decimals
        decimals = len(str(step).split('.')[-1]) if '.' in str(step) else 0
        return round(vol, decimals)

    def _get_filling_mode(self, symbol: str) -> int:
        """Auto-detect the correct filling mode for a symbol.
        Checks symbol_info.filling_mode bitmask and returns the best supported mode.
        Bitmask: 1=FOK, 2=IOC, 4=RETURN
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_RETURN
        # filling_mode is the bitmask attribute (renamed from 'filling' in newer MT5 lib versions)
        filling = getattr(info, 'filling_mode', None) or getattr(info, 'filling', 0)
        # Prefer RETURN (4) > IOC (2) > FOK (1)
        if filling & 4:
            return mt5.ORDER_FILLING_RETURN
        elif filling & 2:
            return mt5.ORDER_FILLING_IOC
        elif filling & 1:
            return mt5.ORDER_FILLING_FOK
        else:
            return mt5.ORDER_FILLING_RETURN

    def open_position(self, symbol: str, direction: str, volume: float, stop_loss: float, take_profit: float, entry_price: float = None) -> Optional[Position]:
        """Open a position. If entry_price is provided AND on the correct side of market,
        send a LIMIT order. Otherwise send a MARKET order at current price.
        """
        # Make sure symbol is visible in Market Watch
        info = mt5.symbol_info(symbol)
        if info is None:
            logger.error(f"Symbol {symbol} not found in MT5")
            return None
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Cannot select symbol {symbol}")
                return None

        # Normalize all prices
        volume = self._normalize_volume(symbol, volume)
        stop_loss = self._normalize_price(symbol, stop_loss)
        take_profit = self._normalize_price(symbol, take_profit)
        if entry_price is not None:
            entry_price = self._normalize_price(symbol, entry_price)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Cannot get tick for {symbol}")
            return None

        current_bid = tick.bid
        current_ask = tick.ask

        # Decide: limit order or market order?
        # SELL LIMIT: entry must be ABOVE current ask
        # BUY LIMIT: entry must be BELOW current bid
        use_limit = False
        if entry_price is not None:
            if direction == "SELL" and entry_price > current_ask:
                use_limit = True
            elif direction == "BUY" and entry_price < current_bid:
                use_limit = True

        if use_limit:
            trade_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
            price = entry_price
            action = mt5.TRADE_ACTION_PENDING
            filling = self._get_filling_mode(symbol)
            logger.info(f"[MT5] Limit order: {direction} {symbol} @ {price} (bid={current_bid}, ask={current_ask})")
        else:
            # Market order at current price
            trade_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
            price = current_ask if direction == "BUY" else current_bid
            action = mt5.TRADE_ACTION_DEAL
            filling = self._get_filling_mode(symbol)
            logger.info(f"[MT5] Market order: {direction} {symbol} @ {price} (bid={current_bid}, ask={current_ask})")

        request = {
            "action": action,
            "symbol": symbol,
            "volume": float(volume),
            "type": trade_type,
            "price": price,
            "sl": float(stop_loss),
            "tp": float(take_profit),
            "deviation": 50,
            "magic": 20240714,
            "comment": "ICT-BOT",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"MT5 order failed: {result}")
            logger.error(f"  Request: symbol={symbol}, vol={volume}, price={price}, sl={stop_loss}, tp={take_profit}")
            logger.error(f"  Current: bid={current_bid}, ask={current_ask}, digits={info.digits}, point={info.point}")
            logger.error(f"  Volume constraints: min={info.volume_min}, max={info.volume_max}, step={info.volume_step}")
            return None

        logger.info(f"[MT5] Order filled: ticket={result.order}, price={result.price}, vol={result.volume}")

        return Position(
            symbol=symbol,
            direction=direction,
            volume=volume,
            entry=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            open_time=datetime.now(timezone.utc).replace(tzinfo=None),
            ticket=result.order,
        )

    def close_position(self, position: Position, price: Optional[float] = None, partial_volume: Optional[float] = None):
        trade_type = mt5.ORDER_TYPE_SELL if position.direction == "BUY" else mt5.ORDER_TYPE_BUY
        if price is None:
            tick = mt5.symbol_info_tick(position.symbol)
            price = tick.bid if position.direction == "BUY" else tick.ask

        # Support partial close: if partial_volume given, close only that portion.
        # MT5 natively supports this — just send a smaller volume in the close request.
        close_vol = partial_volume if partial_volume is not None else position.volume
        close_vol = self._normalize_volume(position.symbol, close_vol)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": close_vol,
            "type": trade_type,
            "position": position.ticket,
            "price": price,
            "deviation": 20,
            "magic": 20240714,
            "comment": "ICT-BOT-PARTIAL" if partial_volume else "ICT-BOT-CLOSE",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._get_filling_mode(position.symbol),
        }

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error(f"MT5 close failed: {result}")
            return False

        # Update position volume after partial close
        if partial_volume is not None:
            position.volume -= close_vol
        return True

    def modify_position(self, position: Position, new_stop_loss: Optional[float] = None, new_take_profit: Optional[float] = None):
        sl = new_stop_loss if new_stop_loss is not None else position.stop_loss
        tp = new_take_profit if new_take_profit is not None else position.take_profit
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": position.symbol,
            "position": position.ticket,
            "sl": float(sl),
            "tp": float(tp),
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            position.stop_loss = sl
            position.take_profit = tp
            return True
        return False

    def get_open_positions(self) -> List[Position]:
        positions = mt5.positions_get(magic=20240714)
        if positions is None:
            return []
        result = []
        for p in positions:
            direction = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
            result.append(Position(
                symbol=p.symbol,
                direction=direction,
                volume=p.volume,
                entry=p.price_open,
                stop_loss=p.sl,
                take_profit=p.tp,
                open_time=datetime.utcfromtimestamp(p.time),
                ticket=p.ticket,
            ))
        return result

    def account_balance(self) -> float:
        info = mt5.account_info()
        return info.balance if info else 0.0

    def is_market_open(self, symbol: str) -> bool:
        """Real market-open check for a symbol.

        Uses MT5's trade mode + a sanity check on the last tick. This catches:
        - Weekends (US30/US100/US500 index CFDs are closed Sat/Sun)
        - Broker-specific holidays
        - Symbols that are temporarily disabled

        MT5 TRADE_DISALLOWED=0, TRADE_LONGONLY=2 etc. — we only treat
        full/disabled modes as "closed". LONGONLY/SHORTONLY still count as open
        (rare for indices, but we don't want to over-block).
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            return False

        # 1. Weekend hard guard — index CFDs never trade Sat/Sun regardless of broker
        #    (covers the case where MT5 still reports stale quotes on weekends)
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        # 4 = Friday, 5 = Saturday, 6 = Sunday — closed Sat full day + Sun until
        # most brokers reopen Sunday evening UTC (typically 22:00-23:00 UTC).
        if now_utc.weekday() == 5:  # Saturday
            return False
        if now_utc.weekday() == 6 and now_utc.hour < 22:  # Sunday before 22:00 UTC
            return False

        # 2. MT5 trade mode: 0=disabled, 1=longonly/shortonly, 2=closeonly, 3=full
        #    TRADE_MODE_DISABLED = 0 -> definitely closed
        try:
            if info.trade_mode == 0:
                return False
        except AttributeError:
            pass

        # 3. Sanity check: if the last tick is more than 10 minutes stale, the
        #    feed is effectively dead (holiday / session break). Real sessions
        #    push ticks at least every few seconds.
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None and tick.time > 0:
                tick_dt = datetime.utcfromtimestamp(tick.time).replace(tzinfo=timezone.utc)
                age_seconds = (now_utc - tick_dt).total_seconds()
                if age_seconds > 600:  # >10 min stale
                    return False
        except Exception:
            pass

        return True

    def get_symbol_info(self, symbol: str):
        """Return MT5 symbol info object (real contract size, tick value, etc.)"""
        return mt5.symbol_info(symbol)

    def get_recent_closed_deals(self, magic: int = 20240714, days: int = 1):
        """Get closed deals from the last N days (for trade tracking sync).

        NOTE: deals are returned UNFILTERED by magic. Some brokers (e.g. FBS) do
        not propagate `magic` onto the closing-side deal of a position, so a
        magic filter here would drop exactly the deals we need to reconcile
        closed trades. The caller (main.py manage_open_positions) re-filters
        authoritatively by ``position_id == trade.ticket``, which is preserved
        on both legs of every deal regardless of broker magic quirks — so
        returning extra magicless deals here cannot cause false matches.
        The ``magic`` arg is kept for API compatibility but no longer used.

        IMPORTANT (2026-07-22 incident): the upper bound must extend PAST the
        current trading day. On at least FBS, ``history_deals_get(from, to)``
        omits every deal from "today" unless ``to`` is strictly beyond the end
        of the current server trading day — extending by a few minutes or an
        hour is NOT enough; it must cross into the next calendar day. Querying
        with ``to = now`` therefore made today's closed trades permanently
        invisible: two real +$3,055.50 US30 wins (tickets 1918501799,
        1918519080) looped as "waiting for exit deal to index" for 4+ hours and
        were never recorded. Verified empirically against FBS-Demo:
        to=now -> 6 deals (today missing); to=now+1day -> 10 deals (today
        present). The fix adds 2 days of headroom so it survives any timezone
        offset between the local UTC clock and the broker's server day.
        """
        from datetime import timedelta
        utc_to = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)
        utc_from = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
        deals = mt5.history_deals_get(utc_from, utc_to)
        if deals is None:
            return []
        return list(deals)
