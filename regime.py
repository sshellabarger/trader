"""
Market Regime Detector — determines if the broad market supports mean-reversion strategies.

Uses daily bars of a market proxy (default SPY) to check if the market is
in an uptrend (above EMA) or downtrend (below EMA).

VWAP reversion buys oversold stocks expecting a bounce — this works in
normal/bullish markets but catches falling knives during crashes.
The April 2025 tariff selloff proved this: VWAP lost -$1,043 in one month
buying "oversold" stocks that kept falling.

This is a structural, generalizable filter:
  - SPY above 20-day EMA → normal market → VWAP reversion active
  - SPY below 20-day EMA → stressed market → VWAP reversion paused
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .indicators import ema

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Detect market regime from a broad market proxy."""

    def __init__(self, ema_period: int = 20):
        self.ema_period = ema_period
        self._regime: str = "unknown"  # "bullish", "bearish", "unknown"
        self._spy_price: Optional[float] = None
        self._spy_ema: Optional[float] = None

    @property
    def regime(self) -> str:
        return self._regime

    @property
    def is_bullish(self) -> bool:
        return self._regime == "bullish"

    @property
    def is_bearish(self) -> bool:
        return self._regime == "bearish"

    def update_from_bars(self, daily_bars: List[Dict]) -> str:
        """
        Update regime from daily bars of the market proxy.
        Returns regime string: "bullish", "bearish", or "unknown".
        """
        if not daily_bars or len(daily_bars) < self.ema_period:
            self._regime = "unknown"
            return self._regime

        closes = [float(b["c"]) for b in daily_bars]
        ema_values = ema(closes, self.ema_period)

        self._spy_price = closes[-1]
        self._spy_ema = ema_values[-1]

        if self._spy_ema is None:
            self._regime = "unknown"
        elif self._spy_price > self._spy_ema:
            self._regime = "bullish"
        else:
            self._regime = "bearish"

        return self._regime

    def update_from_price_and_ema(self, price: float, ema_val: float) -> str:
        """Update regime from pre-computed values."""
        self._spy_price = price
        self._spy_ema = ema_val

        if price > ema_val:
            self._regime = "bullish"
        else:
            self._regime = "bearish"

        return self._regime

    def status(self) -> Dict:
        return {
            "regime": self._regime,
            "spy_price": self._spy_price,
            "spy_ema": self._spy_ema,
        }
