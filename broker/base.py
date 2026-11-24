"""Abstract broker interface — same API for simulator and MT5."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime
import pandas as pd


@dataclass
class Position:
    symbol: str
    direction: str         # "BUY" or "SELL"
    volume: float
    entry: float
    stop_loss: float
    take_profit: float
    open_time: datetime
    ticket: int = 0
    partial_closed: bool = False
    be_applied: bool = False    # True once SL has been moved to breakeven


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BaseBroker(ABC):
    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def disconnect(self): ...

    @abstractmethod
    def get_history(self, symbol: str, timeframe: str, count: int = 200) -> pd.DataFrame: ...

    @abstractmethod
    def get_tick(self, symbol: str) -> float: ...

    @abstractmethod
    def open_position(self, symbol: str, direction: str, volume: float, stop_loss: float, take_profit: float, entry_price: float = None) -> Optional[Position]: ...

    @abstractmethod
    def close_position(self, position: Position, price: Optional[float] = None, partial_volume: Optional[float] = None): ...

    @abstractmethod
    def modify_position(self, position: Position, new_stop_loss: Optional[float] = None, new_take_profit: Optional[float] = None): ...

    @abstractmethod
    def get_open_positions(self) -> List[Position]: ...

    @abstractmethod
    def account_balance(self) -> float: ...

    @abstractmethod
    def is_market_open(self, symbol: str) -> bool: ...

    def get_symbol_info(self, symbol: str):
        """Return broker-specific symbol info object (for position sizing).
        Override in subclasses. Returns None if not available."""
        return None
