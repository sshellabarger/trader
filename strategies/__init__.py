"""
Base strategy interface and shared signal types.
Every strategy must produce Signal objects that the engine can act on.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from ..config import Config
from ..scanner import Candidate


class SignalDirection(Enum):
    LONG = "long"
    SHORT = "short"  # reserved for future use
    FLAT = "flat"


class SignalAction(Enum):
    ENTER = "enter"
    EXIT = "exit"
    HOLD = "hold"


@dataclass
class Signal:
    """A trading signal produced by a strategy."""
    symbol: str
    strategy: str                # name of the strategy that produced it
    action: SignalAction
    direction: SignalDirection
    strength: float              # 0.0 – 1.0 confidence
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    indicators: Dict = field(default_factory=dict)  # snapshot of key indicators
    timestamp: Optional[str] = None

    @property
    def risk_reward(self) -> Optional[float]:
        """Calculate risk/reward ratio if prices are set."""
        if self.entry_price and self.stop_loss and self.take_profit:
            risk = abs(self.entry_price - self.stop_loss)
            reward = abs(self.take_profit - self.entry_price)
            return reward / risk if risk > 0 else None
        return None


class BaseStrategy(ABC):
    """
    Abstract base class for all strategies.
    Subclasses must implement `evaluate()` which returns a list of Signals.
    """

    name: str = "base"

    def __init__(self, config: Config, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or logging.getLogger(f"strategy.{self.name}")
        self._active_trades: Dict[str, Signal] = {}  # symbol → entry signal

    @abstractmethod
    def evaluate(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Optional[Dict] = None,
    ) -> Optional[Signal]:
        """
        Evaluate a candidate and return a Signal (or None if no action).

        Args:
            candidate: scanner Candidate with snapshot data
            bars: intraday 1-min bars for this symbol (today)
            indicators: pre-computed indicator dict from indicators.compute_indicators()
            position: current position dict from broker (None if flat)

        Returns:
            Signal or None
        """
        ...

    def on_fill(self, symbol: str, signal: Signal):
        """Called when an order for this strategy is filled."""
        if signal.action == SignalAction.ENTER:
            self._active_trades[symbol] = signal
        elif signal.action == SignalAction.EXIT:
            self._active_trades.pop(symbol, None)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._active_trades

    @property
    def active_count(self) -> int:
        return len(self._active_trades)
