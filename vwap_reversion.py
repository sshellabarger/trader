"""
VWAP Mean Reversion Strategy
------------------------------
Logic:
  1. VWAP acts as an intraday "fair value" magnet.
  2. When price deviates significantly below VWAP (for longs):
     - RSI confirms oversold (< 30–35)
     - Price near lower Bollinger Band
     - Volume confirms selling exhaustion
  3. Enter long expecting reversion toward VWAP.
  4. Stop below recent swing low or ATR-based.
  5. Target: VWAP itself or upper band.

Edge: institutional algorithms target VWAP for execution,
creating genuine mean-reverting behavior around it.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from . import BaseStrategy, Candidate, Config, Signal, SignalAction, SignalDirection


class VWAPReversionStrategy(BaseStrategy):
    name = "vwap_reversion"

    def __init__(self, config: Config, logger: Optional[logging.Logger] = None):
        super().__init__(config, logger)
        self.sc = config.strategy

    def evaluate(
        self,
        candidate: Candidate,
        bars: List[Dict],
        indicators: Dict,
        position: Optional[Dict] = None,
    ) -> Optional[Signal]:

        if not self.sc.vwap_enabled:
            return None

        # Need enough bars for indicators
        if len(bars) < 30:
            return None

        # --- Exit logic ---
        if position is not None and self.has_position(candidate.symbol):
            return self._check_exit(candidate, bars, indicators, position)

        # --- Entry logic ---
        if position is not None:
            return None

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
        price = candidate.price

        vwap_val = indicators.get("vwap")
        rsi_val = indicators.get("rsi_14")
        bb_lower = indicators.get("bb_lower")
        bb_upper = indicators.get("bb_upper")
        bb_pct_b = indicators.get("bb_pct_b")
        atr_val = indicators.get("atr_14")
        vwap_dev = indicators.get("vwap_deviation_pct", 0)

        if vwap_val is None or rsi_val is None:
            return None

        # --- Long entry: oversold below VWAP ---
        if self._is_long_setup(price, vwap_val, vwap_dev, rsi_val, bb_pct_b, bb_lower):

            # Stop loss: below recent swing low or ATR-based
            recent_lows = [float(b["l"]) for b in bars[-10:]]
            swing_low = min(recent_lows)

            if atr_val and atr_val > 0:
                atr_stop = price - (self.config.risk.default_stop_atr_multiple * atr_val)
                stop_loss = min(swing_low, atr_stop)
            else:
                stop_loss = swing_low - (price * 0.002)  # fallback: 0.2% below swing low

            # Target: VWAP or midpoint between price and VWAP
            target = vwap_val  # primary target is reversion to VWAP
            risk = price - stop_loss
            if risk <= 0:
                return None

            # Ensure minimum R:R
            reward = target - price
            if reward / risk < 1.0:
                # Extend target to upper band or 1.5R
                target = price + (risk * self.config.risk.take_profit_rr_ratio)

            strength = self._calculate_strength(
                vwap_dev, rsi_val, bb_pct_b,
                indicators.get("relative_volume"),
                "long"
            )

            if strength < 0.45:
                return None  # not enough confluence

            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.ENTER,
                direction=SignalDirection.LONG,
                strength=strength,
                entry_price=price,
                stop_loss=round(stop_loss, 2),
                take_profit=round(target, 2),
                reason=(
                    f"VWAP reversion long: price {price:.2f} is {vwap_dev:.1f}% below VWAP "
                    f"{vwap_val:.2f}, RSI={rsi_val:.0f}"
                ),
                indicators={
                    "vwap": vwap_val,
                    "vwap_deviation_pct": round(vwap_dev, 2),
                    "rsi": round(rsi_val, 1),
                    "bb_pct_b": round(bb_pct_b, 3) if bb_pct_b else None,
                    "atr": atr_val,
                    "rvol": indicators.get("relative_volume"),
                },
            )

        return None

    def _is_long_setup(
        self, price: float, vwap_val: float, vwap_dev: float,
        rsi_val: float, bb_pct_b: Optional[float], bb_lower: Optional[float]
    ) -> bool:
        """Check if conditions are met for a long VWAP reversion entry."""

        # Must be below VWAP by at least the threshold
        if vwap_dev > -self.sc.vwap_deviation_pct:
            return False

        # RSI must be oversold
        if rsi_val > self.sc.vwap_rsi_oversold:
            return False

        # Bollinger Band confirmation (price near or below lower band)
        if bb_pct_b is not None and bb_pct_b > 0.15:
            return False  # not close enough to lower band

        return True

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

        vwap_val = indicators.get("vwap")
        rsi_val = indicators.get("rsi_14")

        # Target hit: price reverted back to or above VWAP
        if vwap_val and price >= vwap_val:
            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.EXIT,
                direction=SignalDirection.FLAT,
                strength=0.9,
                reason=f"VWAP reversion target: price {price:.2f} reached VWAP {vwap_val:.2f}",
            )

        # RSI overbought: momentum shifted, take profit
        if rsi_val and rsi_val > 65:
            return Signal(
                symbol=sym,
                strategy=self.name,
                action=SignalAction.EXIT,
                direction=SignalDirection.FLAT,
                strength=0.6,
                reason=f"VWAP exit: RSI {rsi_val:.0f} overbought, taking profit",
            )

        # Momentum died: price making new lows after entry (reversion failed)
        if entry_signal.entry_price and price < entry_signal.entry_price * 0.995:
            recent_closes = [float(b["c"]) for b in bars[-5:]]
            if all(c < entry_signal.entry_price for c in recent_closes):
                return Signal(
                    symbol=sym,
                    strategy=self.name,
                    action=SignalAction.EXIT,
                    direction=SignalDirection.FLAT,
                    strength=0.7,
                    reason="VWAP exit: reversion failed, sustained below entry",
                )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_strength(
        self,
        vwap_dev: float,
        rsi_val: float,
        bb_pct_b: Optional[float],
        rvol: Optional[float],
        direction: str,
    ) -> float:
        """Calculate signal strength based on confluence of indicators."""
        score = 0.3  # base for passing the setup check

        # Stronger deviation from VWAP = stronger signal
        if abs(vwap_dev) > 2.0:
            score += 0.2
        elif abs(vwap_dev) > 1.5:
            score += 0.12
        elif abs(vwap_dev) > 1.0:
            score += 0.05

        # Deeper RSI = more oversold = higher bounce probability
        if direction == "long":
            if rsi_val < 20:
                score += 0.2
            elif rsi_val < 25:
                score += 0.12
            elif rsi_val < 30:
                score += 0.05

        # Bollinger Band confirmation
        if bb_pct_b is not None:
            if bb_pct_b < 0.0:  # below lower band
                score += 0.15
            elif bb_pct_b < 0.1:
                score += 0.08

        # Volume: moderate is better for reversion (exhaustion, not panic)
        if rvol:
            if 1.0 < rvol < 2.5:
                score += 0.05  # healthy volume
            elif rvol > 4.0:
                score -= 0.05  # extreme volume might mean trend, not reversion

        return max(0.0, min(1.0, score))
