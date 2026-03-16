"""
5-Minute Opening Range Breakout (ORB) — ETF Edition
-----------------------------------------------------
Based on Zarattini & Aziz (2023): "Can Day Trading Really Be Profitable?"

The paper's approach:
  1. Observe the first 5-minute bar of QQQ/TQQQ.
  2. If it closes UP from open → go long. If DOWN → go short.
  3. Stop loss at the range low (long) or range high (short).
  4. Profit target at 10× the risk (range size).
  5. If neither stop nor target is hit, exit at market close.
  6. Risk 1% of capital per trade.

Results from the paper (2016–2023):
  - QQQ ORB: 675% total return, 33% annualized alpha
  - TQQQ ORB: 1,484% total return, 47% annualized alpha
  - Sharpe Ratio: 1.2

For leveraged ETF trading:
  - Bullish breakout → buy TQQQ (3× long Nasdaq)
  - Bearish breakout → buy SQQQ (3× short Nasdaq) — avoids short-selling complexity
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
        self._today_signal_fired: bool = False  # only one ORB trade per day

    def evaluate(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Optional[Dict] = None,
    ) -> Optional[Signal]:

        if not self.sc.orb_enabled:
            return None

        # ORB fires once per day — in the bar right after the opening range
        if self._today_signal_fired:
            return None

        # Need exactly enough bars: the range period + 1
        range_minutes = self.sc.orb_range_minutes
        if len(bars) < range_minutes + 1:
            return None

        # Only evaluate on the bar immediately after the range forms
        # (avoids re-evaluating and firing multiple times)
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

        # The opening range
        range_high = max(float(b["h"]) for b in range_bars)
        range_low = min(float(b["l"]) for b in range_bars)
        range_open = float(range_bars[0]["o"])
        range_close = float(range_bars[-1]["c"])
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        # Skip if range is too tiny (just noise)
        if range_size < self.sc.orb_min_range_dollars:
            return None

        # Skip if range is chaotic relative to ATR
        atr = indicators.get("atr_14")
        if atr and atr > 0:
            if range_size / atr > self.sc.orb_max_range_atr_ratio:
                return None

        # Direction: did the first N minutes close up or down from open?
        direction_up = range_close > range_open

        # Current price (first bar after range)
        current_bar = bars[rm]
        entry_price = float(current_bar["c"])

        if direction_up:
            # LONG signal
            stop_loss = range_low
            risk = entry_price - stop_loss
            if risk <= 0:
                return None

            take_profit = entry_price + (risk * self.sc.orb_profit_target_r)

            self._today_signal_fired = True

            # Determine which symbol to actually trade
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
                    f"ORB long: {rm}min range closed up "
                    f"({range_open:.2f}→{range_close:.2f}), "
                    f"range=${range_size:.2f}"
                ),
                indicators={
                    "range_high": range_high,
                    "range_low": range_low,
                    "range_size": round(range_size, 2),
                    "direction": "long",
                    "atr": atr,
                },
            )

        elif self.sc.orb_trade_both_directions:
            # SHORT signal (via SQQQ if leveraged)
            stop_loss = range_high
            risk = stop_loss - entry_price
            if risk <= 0:
                return None

            take_profit = entry_price - (risk * self.sc.orb_profit_target_r)

            self._today_signal_fired = True

            # For leveraged mode, we BUY SQQQ instead of shorting
            if self.sc.use_leveraged:
                trade_symbol = self.sc.leveraged_bear
                # Flip to LONG direction since we're buying SQQQ
                # Stop and target need to be recalculated for SQQQ's price
                # In the backtest, this is handled by the direction flag
                return Signal(
                    symbol=trade_symbol,
                    strategy=self.name,
                    action=SignalAction.ENTER,
                    direction=SignalDirection.LONG,  # buying SQQQ = long position
                    strength=0.7,
                    entry_price=entry_price,
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    reason=(
                        f"ORB short (via {trade_symbol}): {rm}min range closed down "
                        f"({range_open:.2f}→{range_close:.2f}), "
                        f"range=${range_size:.2f}"
                    ),
                    indicators={
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_size": round(range_size, 2),
                        "direction": "short_via_inverse",
                        "atr": atr,
                    },
                )
            else:
                return Signal(
                    symbol=candidate.symbol,
                    strategy=self.name,
                    action=SignalAction.ENTER,
                    direction=SignalDirection.SHORT,
                    strength=0.7,
                    entry_price=entry_price,
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    reason=(
                        f"ORB short: {rm}min range closed down "
                        f"({range_open:.2f}→{range_close:.2f}), "
                        f"range=${range_size:.2f}"
                    ),
                    indicators={
                        "range_high": range_high,
                        "range_low": range_low,
                        "range_size": round(range_size, 2),
                        "direction": "short",
                        "atr": atr,
                    },
                )

        return None

    def reset_daily(self):
        """Reset for new trading day."""
        super().reset_daily()
        self._today_signal_fired = False
