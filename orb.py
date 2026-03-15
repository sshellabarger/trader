"""
Opening Range Breakout (ORB) Strategy
--------------------------------------
Logic:
  1. Define the high/low of the first N minutes after open (default 15).
  2. Wait for price to break above (long) or below (short) that range.
  3. Confirm with volume surge and bar closing outside range.
  4. Stop loss at opposite side of range (or ATR-based).
  5. Target 2× risk (or trail with ATR).

This is one of the most well-documented intraday strategies with a genuine
statistical edge on volatile, liquid stocks.
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
        self._opening_ranges: Dict[str, Dict] = {}  # symbol → {high, low, set}

    def evaluate(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Optional[Dict] = None,
    ) -> Optional[Signal]:

        if not self.sc.orb_enabled:
            return None

        # Need enough bars to define the opening range
        range_minutes = self.sc.orb_range_minutes
        if len(bars) < range_minutes + self.sc.orb_confirmation_bars:
            return None

        # --- Exit logic (check first) ---
        if position is not None and self.has_position(candidate.symbol):
            return self._check_exit(candidate, bars, indicators, position)

        # --- Entry logic ---
        if position is not None:
            return None  # already in a position (from another strategy maybe)

        if self.active_count >= self.sc.max_trades_per_strategy:
            return None

        return self._check_entry(candidate, bars, indicators)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def _check_entry(
        self, candidate: Candidate, bars: List[Dict], indicators: Dict
    ) -> Optional[Signal]:

        sym = candidate.symbol
        rm = self.sc.orb_range_minutes

        # Calculate opening range from first N bars
        range_bars = bars[:rm]
        range_high = max(float(b["h"]) for b in range_bars)
        range_low = min(float(b["l"]) for b in range_bars)
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        range_pct = (range_size / candidate.price) * 100

        # Filter: range must be meaningful but not too wide
        if range_pct < self.sc.orb_min_range_pct:
            return None
        if range_pct > self.sc.orb_max_range_pct:
            return None

        # Store the range
        self._opening_ranges[sym] = {"high": range_high, "low": range_low}

        # Look at recent bars (after the range period)
        post_range_bars = bars[rm:]
        if len(post_range_bars) < self.sc.orb_confirmation_bars:
            return None

        # Check for breakout in the most recent confirmation window
        confirm_bars = post_range_bars[-self.sc.orb_confirmation_bars:]
        last_close = float(confirm_bars[-1]["c"])
        last_high = max(float(b["h"]) for b in confirm_bars)
        last_low = min(float(b["l"]) for b in confirm_bars)

        # Volume confirmation
        if self.sc.orb_volume_confirm:
            rvol = indicators.get("relative_volume")
            if rvol is not None and rvol < 1.2:
                return None  # need above-average volume

        atr = indicators.get("atr_14")

        # --- Long breakout ---
        if last_close > range_high and last_low > range_low:
            # All confirmation bars closed above range high
            all_above = all(float(b["c"]) > range_high for b in confirm_bars)
            if not all_above:
                return None

            stop_loss = range_low  # stop at opposite side of range
            if atr and atr > 0:
                # Tighten stop using ATR if it's tighter than range
                atr_stop = last_close - (self.config.risk.default_stop_atr_multiple * atr)
                stop_loss = max(stop_loss, atr_stop)

            risk = last_close - stop_loss
            take_profit = last_close + (risk * self.config.risk.take_profit_rr_ratio)

            strength = self._calculate_strength(candidate, indicators, range_pct, "long")

            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.ENTER,
                direction=SignalDirection.LONG,
                strength=strength,
                entry_price=last_close,
                stop_loss=round(stop_loss, 2),
                take_profit=round(take_profit, 2),
                reason=f"ORB long breakout: close {last_close:.2f} > range high {range_high:.2f}",
                indicators={
                    "range_high": range_high,
                    "range_low": range_low,
                    "range_pct": round(range_pct, 2),
                    "rvol": indicators.get("relative_volume"),
                    "rsi": indicators.get("rsi_14"),
                    "atr": atr,
                },
            )

        # --- Short breakout (for future use) ---
        # Currently only trading long per Phase 1 scope
        # if last_close < range_low and last_high < range_high:
        #     ...

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
        """Check if we should exit an ORB position."""
        sym = candidate.symbol
        entry_signal = self._active_trades.get(sym)
        if not entry_signal:
            return None

        current_price = candidate.price

        # VWAP rejection: if price falls back below VWAP, momentum is fading
        vwap_val = indicators.get("vwap")
        if vwap_val and current_price < vwap_val and entry_signal.direction == SignalDirection.LONG:
            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.EXIT,
                direction=SignalDirection.FLAT,
                strength=0.7,
                reason=f"ORB exit: price {current_price:.2f} fell below VWAP {vwap_val:.2f}",
            )

        # Range re-entry: if price falls back into the opening range, breakout failed
        or_data = self._opening_ranges.get(sym)
        if or_data and entry_signal.direction == SignalDirection.LONG:
            if current_price < or_data["high"]:
                return Signal(
                    symbol=sym,
                    strategy=self.name,
                    action=SignalAction.EXIT,
                    direction=SignalDirection.FLAT,
                    strength=0.8,
                    reason=f"ORB exit: price {current_price:.2f} fell back into range",
                )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_strength(
        self, candidate: Candidate, indicators: Dict, range_pct: float, direction: str
    ) -> float:
        """Calculate signal strength 0–1 based on confirming factors."""
        score = 0.5  # base score for a valid breakout

        # Volume boost
        rvol = indicators.get("relative_volume")
        if rvol and rvol > 2.0:
            score += 0.15
        elif rvol and rvol > 1.5:
            score += 0.08

        # RSI confirmation
        rsi_val = indicators.get("rsi_14")
        if rsi_val is not None:
            if direction == "long" and 50 < rsi_val < 75:
                score += 0.1  # momentum but not overbought
            elif direction == "long" and rsi_val >= 75:
                score -= 0.1  # overbought risk

        # Gap alignment (gap in same direction as breakout)
        if direction == "long" and candidate.gap_pct > 1.0:
            score += 0.1
        elif direction == "long" and candidate.gap_pct < -1.0:
            score -= 0.05  # counter-gap breakout is riskier

        # Tighter range = cleaner breakout
        if 0.5 <= range_pct <= 1.5:
            score += 0.05

        return max(0.0, min(1.0, score))
