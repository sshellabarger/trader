"""
Gap & Go Strategy
------------------
Logic:
  1. Identify stocks gapping up 3%+ on high relative volume pre-market.
  2. Wait for first pullback/consolidation after the opening surge.
  3. Enter on the first higher-low / continuation above the pullback high.
  4. Stop loss below the pullback low.
  5. Target 2× risk or trail with EMA.

Edge: gap + volume + catalyst = institutional interest.
The first pullback entry avoids chasing and gives a defined risk point.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from . import BaseStrategy, Candidate, Config, Signal, SignalAction, SignalDirection


class GapAndGoStrategy(BaseStrategy):
    name = "gap_and_go"

    def __init__(self, config: Config, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.sc = config.strategy
        self._pullback_state: Dict[str, Dict] = {}

    def evaluate(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Optional[Dict] = None,
    ) -> Optional[Signal]:

        if not self.sc.gap_enabled:
            return None

        # Need minimum bars
        if len(bars) < 10:
            return None

        # Check if within time window
        if len(bars) > self.sc.gap_max_entry_minutes:
            # Past the entry window, only manage exits
            if position is not None and self.has_position(candidate.symbol):
                return self._check_exit(candidate, bars, indicators, position)
            return None

        # --- Exit logic ---
        if position is not None and self.has_position(candidate.symbol):
            return self._check_exit(candidate, bars, indicators, position)

        # --- Entry logic ---
        if position is not None:
            return None

        if self.active_count >= self.sc.max_trades_per_strategy:
            return None

        # Must be a gapper
        if abs(candidate.gap_pct) < self.sc.gap_min_pct:
            return None

        # Must have volume surge
        if candidate.relative_volume < self.sc.gap_volume_surge:
            return None

        return self._check_entry(candidate, bars, indicators)

    # ------------------------------------------------------------------
    # Entry — first pullback pattern
    # ------------------------------------------------------------------

    def _check_entry(
        self, candidate: Candidate, bars: List[Dict], indicators: Dict
    ) -> Optional[Signal]:
        sym = candidate.symbol
        price = candidate.price

        # Only trade in direction of gap (long for gap up)
        if candidate.gap_pct < self.sc.gap_min_pct:
            return None  # only long gaps for now

        # Track pullback state
        state = self._pullback_state.get(sym, {"phase": "surge", "pullback_low": None, "surge_high": None})

        closes = [float(b["c"]) for b in bars]
        highs = [float(b["h"]) for b in bars]
        lows = [float(b["l"]) for b in bars]

        # Find the initial surge high (highest point in first bars)
        surge_window = min(15, len(bars))
        surge_high = max(highs[:surge_window])
        state["surge_high"] = surge_high

        # Detect pullback: price retraces from surge high
        if state["phase"] == "surge":
            # Look for the first meaningful pullback
            recent_lows = lows[-5:] if len(lows) >= 5 else lows
            current_low = min(recent_lows)
            retrace_pct = ((surge_high - current_low) / surge_high) * 100

            if retrace_pct >= 0.3:  # at least 0.3% pullback
                state["phase"] = "pullback"
                state["pullback_low"] = current_low
                self._pullback_state[sym] = state
                return None  # wait for continuation

        elif state["phase"] == "pullback":
            # Update pullback low if price goes lower
            recent_low = min(lows[-3:]) if len(lows) >= 3 else min(lows)
            if state["pullback_low"] is None or recent_low < state["pullback_low"]:
                state["pullback_low"] = recent_low

            # Check for continuation: price breaks above the pullback high
            if len(bars) < 3:
                self._pullback_state[sym] = state
                return None

            # Continuation: recent close above the high of the pullback candle
            pullback_idx = None
            for i in range(len(bars) - 1, max(0, len(bars) - 10), -1):
                if float(bars[i]["l"]) == state["pullback_low"]:
                    pullback_idx = i
                    break

            if pullback_idx is None:
                self._pullback_state[sym] = state
                return None

            # Need at least one bar after pullback
            if pullback_idx >= len(bars) - 1:
                self._pullback_state[sym] = state
                return None

            pullback_high = max(float(b["h"]) for b in bars[pullback_idx:pullback_idx + 3] if pullback_idx + 3 <= len(bars))

            # Entry trigger: current close above pullback high
            if price > pullback_high:
                state["phase"] = "entered"
                self._pullback_state[sym] = state

                stop_loss = state["pullback_low"] - (price * 0.001)  # just below pullback low
                risk = price - stop_loss
                if risk <= 0:
                    return None

                take_profit = price + (risk * self.config.risk.take_profit_rr_ratio)

                atr_val = indicators.get("atr_14")
                if atr_val and atr_val > 0:
                    # Ensure stop is at least 1 ATR away
                    atr_stop = price - (self.config.risk.default_stop_atr_multiple * atr_val)
                    stop_loss = min(stop_loss, atr_stop)

                strength = self._calculate_strength(candidate, indicators)

                return Signal(
                    symbol=sym,
                    strategy=self.name,
                    action=SignalAction.ENTER,
                    direction=SignalDirection.LONG,
                    strength=strength,
                    entry_price=price,
                    stop_loss=round(stop_loss, 2),
                    take_profit=round(take_profit, 2),
                    reason=(
                        f"Gap&Go entry: {candidate.gap_pct:.1f}% gap, "
                        f"pullback to {state['pullback_low']:.2f}, "
                        f"continuation above {pullback_high:.2f}"
                    ),
                    indicators={
                        "gap_pct": round(candidate.gap_pct, 2),
                        "rvol": round(candidate.relative_volume, 2),
                        "surge_high": surge_high,
                        "pullback_low": state["pullback_low"],
                        "rsi": indicators.get("rsi_14"),
                        "vwap": indicators.get("vwap"),
                    },
                )

        self._pullback_state[sym] = state
        return None

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def _check_exit(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Dict,
    ) -> Optional[Signal]:
        sym = candidate.symbol
        price = candidate.price
        entry_signal = self._active_trades.get(sym)
        if not entry_signal:
            return None

        # EMA trend break: if price crosses below 9-EMA, momentum is dying
        ema9 = indicators.get("ema_9")
        if ema9 and price < ema9:
            # Confirm with 2 consecutive closes below
            recent_closes = [float(b["c"]) for b in bars[-3:]]
            if len(recent_closes) >= 2 and all(c < ema9 for c in recent_closes[-2:]):
                return Signal(
                    symbol=sym,
                    strategy=self.name,
                    action=SignalAction.EXIT,
                    direction=SignalDirection.FLAT,
                    strength=0.7,
                    reason=f"Gap&Go exit: price {price:.2f} broke below EMA9 {ema9:.2f}",
                )

        # VWAP break: gap momentum is gone
        vwap_val = indicators.get("vwap")
        if vwap_val and entry_signal.direction == SignalDirection.LONG and price < vwap_val:
            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.EXIT,
                direction=SignalDirection.FLAT,
                strength=0.8,
                reason=f"Gap&Go exit: price {price:.2f} fell below VWAP {vwap_val:.2f}",
            )

        # RSI divergence: momentum fading
        rsi_val = indicators.get("rsi_14")
        if rsi_val and rsi_val > 80:
            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.EXIT,
                direction=SignalDirection.FLAT,
                strength=0.5,
                reason=f"Gap&Go exit: RSI {rsi_val:.0f} extremely overbought",
            )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_strength(self, candidate: Candidate, indicators: Dict) -> float:
        score = 0.5  # base for qualified gap setup

        # Bigger gap = more institutional interest
        if candidate.gap_pct > 7:
            score += 0.15
        elif candidate.gap_pct > 5:
            score += 0.1

        # Higher relative volume = stronger conviction
        if candidate.relative_volume > 4:
            score += 0.15
        elif candidate.relative_volume > 2.5:
            score += 0.08

        # VWAP confirmation: price above VWAP
        vwap_dev = indicators.get("vwap_deviation_pct", 0)
        if vwap_dev > 0:
            score += 0.05

        # RSI in momentum zone (not extreme)
        rsi_val = indicators.get("rsi_14")
        if rsi_val and 55 < rsi_val < 75:
            score += 0.08

        return max(0.0, min(1.0, score))
