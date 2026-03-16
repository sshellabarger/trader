"""
5-Minute Opening Range Breakout (ORB) — ETF Edition
Based on Zarattini & Aziz (2023): "Can Day Trading Really Be Profitable?"

Both bullish and bearish days generate LONG entries on the primary symbol.
- Bullish: stop at range_low (normal ORB)
- Bearish: stop at range_high (captures opening range spread)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from . import BaseStrategy, Candidate, Config, Signal, SignalAction, SignalDirection


class ORBStrategy(BaseStrategy):
    name = "orb"

    def __init__(self, config: Config, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.sc = config.strategy
        self._today_signal_fired: bool = False

    def evaluate(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Optional[Dict] = None,
    ) -> Optional[Signal]:

        if not self.sc.orb_enabled:
            return None
        if self._today_signal_fired:
            return None

        range_minutes = self.sc.orb_range_minutes
        if len(bars) < range_minutes + 1:
            return None
        if len(bars) > range_minutes + 3:
            return None
        if position is not None:
            return None
        if self.is_blocked(candidate.symbol):
            return None

        return self._check_entry(candidate, bars, indicators)

    def _check_entry(
        self, candidate: Candidate, bars: List[Dict], indicators: Dict
    ) -> Optional[Signal]:

        rm = self.sc.orb_range_minutes
        range_bars = bars[:rm]

        range_high = max(float(b["h"]) for b in range_bars)
        range_low = min(float(b["l"]) for b in range_bars)
        range_open = float(range_bars[0]["o"])
        range_close = float(range_bars[-1]["c"])
        range_size = range_high - range_low

        if range_size <= 0 or range_size < self.sc.orb_min_range_dollars:
            return None

        atr = indicators.get("atr_14")
        if atr and atr > 0 and range_size / atr > self.sc.orb_max_range_atr_ratio:
            return None

        current_bar = bars[rm]
        entry_price = float(current_bar["c"])
        direction_up = range_close > range_open

        if direction_up:
            # Bullish day: standard ORB long
            stop_loss = range_low
            risk = entry_price - stop_loss
            if risk <= 0:
                return None
            take_profit = entry_price + (risk * self.sc.orb_profit_target_r)
        elif self.sc.orb_trade_both_directions:
            # Bearish day: enter long with stop at range_high
            # (captures the opening range spread as price reverts to range boundary)
            stop_loss = range_high
            risk = stop_loss - entry_price
            if risk <= 0:
                return None
            take_profit = entry_price - (risk * self.sc.orb_profit_target_r)
        else:
            return None

        self._today_signal_fired = True

        trade_symbol = candidate.symbol
        if self.sc.use_leveraged:
            trade_symbol = self.sc.leveraged_bull

        return Signal(
            symbol=trade_symbol,
            strategy=self.name,
            action=SignalAction.ENTER,
            direction=SignalDirection.LONG,
            strength=0.7,
            entry_price=entry_price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reason=(
                f"ORB {'long' if direction_up else 'range-capture'}: "
                f"{rm}min range ({range_open:.2f}->{range_close:.2f}), "
                f"range=${range_size:.2f}"
            ),
            indicators={
                "range_high": range_high,
                "range_low": range_low,
                "range_size": round(range_size, 2),
                "direction": "long" if direction_up else "range_capture",
                "atr": atr,
            },
        )

    def reset_daily(self):
        super().reset_daily()
        self._today_signal_fired = False
