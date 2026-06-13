"""
Risk Manager — enforces position sizing, stop losses, exposure limits, and daily controls.

All trade entries must pass through risk validation before order submission.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import RiskConfig
from .strategies import Signal, SignalDirection

logger = logging.getLogger(__name__)


@dataclass
class PositionInfo:
    """Standardised position snapshot from broker."""
    symbol: str
    qty: int
    avg_entry: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    side: str = "long"


@dataclass
class SizeResult:
    """Output of position sizing calculation."""
    shares: int
    position_value: float
    risk_amount: float
    stop_distance: float
    limited_by: str = ""  # what capped the size


class RiskManager:
    """Portfolio-level risk enforcement."""

    def __init__(self, config: RiskConfig):
        self.config = config
        self.daily_start_equity: Optional[float] = None
        self.daily_trade_count: int = 0
        self.daily_pnl: float = 0.0
        self._high_water: Dict[str, float] = {}  # symbol → highest price for trailing stop

    # ------------------------------------------------------------------
    # Daily reset
    # ------------------------------------------------------------------

    def reset_daily(self, equity: float):
        self.daily_start_equity = equity
        self.daily_trade_count = 0
        self.daily_pnl = 0.0
        self._high_water.clear()
        logger.info(f"Daily risk reset — start equity: ${equity:,.2f}")

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        signal: Signal,
        equity: float,
        buying_power: float,
        existing_positions: int,
    ) -> SizeResult:
        """
        Risk-based position sizing.
        Size = (equity × risk%) / (entry − stop).
        Then cap by max position %, buying power, and max positions.
        """
        entry = signal.entry_price or 0
        stop = signal.stop_loss or 0
        if entry <= 0 or stop <= 0:
            return SizeResult(0, 0, 0, 0, "invalid_prices")

        stop_distance = abs(entry - stop)
        if stop_distance == 0:
            stop_distance = entry * 0.01  # fallback 1%

        # Risk amount (% of equity, capped by max dollar risk)
        risk_amount = equity * (self.config.risk_per_trade_pct / 100.0)
        if hasattr(self.config, 'max_risk_dollars') and self.config.max_risk_dollars > 0:
            risk_amount = min(risk_amount, self.config.max_risk_dollars)

        # Shares from risk
        risk_shares = int(risk_amount / stop_distance)

        # Cap by max position size
        max_value = equity * (self.config.max_position_pct / 100.0)
        max_shares_value = int(max_value / entry) if entry > 0 else 0

        # Cap by buying power
        max_shares_bp = int(buying_power / entry) if entry > 0 else 0

        shares = min(risk_shares, max_shares_value, max_shares_bp)
        limited_by = ""
        if shares == max_shares_value and shares < risk_shares:
            limited_by = "max_position_pct"
        elif shares == max_shares_bp and shares < risk_shares:
            limited_by = "buying_power"

        # Check max positions
        if existing_positions >= self.config.max_positions:
            return SizeResult(0, 0, risk_amount, stop_distance, "max_positions")

        # Minimum viable
        if shares < 1:
            return SizeResult(0, 0, risk_amount, stop_distance, "shares_too_small")

        position_value = shares * entry
        if position_value < 100:
            return SizeResult(0, 0, risk_amount, stop_distance, "value_too_small")

        return SizeResult(
            shares=shares,
            position_value=round(position_value, 2),
            risk_amount=round(risk_amount, 2),
            stop_distance=round(stop_distance, 4),
            limited_by=limited_by,
        )

    # ------------------------------------------------------------------
    # Pre-trade validation
    # ------------------------------------------------------------------

    def validate_entry(
        self,
        signal: Signal,
        equity: float,
        buying_power: float,
        positions: List[PositionInfo],
    ) -> Tuple[bool, str]:
        """
        Full pre-trade risk check. Returns (ok, reason).
        """
        # Daily trade limit
        if self.daily_trade_count >= self.config.max_daily_trades:
            return False, f"Daily trade limit ({self.config.max_daily_trades}) reached"

        # Daily loss limit
        if self.daily_start_equity and self.daily_start_equity > 0:
            daily_pnl_pct = ((equity - self.daily_start_equity) / self.daily_start_equity) * 100
            if daily_pnl_pct <= -self.config.daily_loss_limit_pct:
                return False, f"Daily loss limit hit: {daily_pnl_pct:.2f}%"

        # Total exposure
        total_exposure = sum(p.market_value for p in positions)
        exposure_pct = (total_exposure / equity * 100) if equity > 0 else 0
        entry_value = (signal.entry_price or 0) * 1  # will be recalculated with actual size
        if exposure_pct > self.config.max_total_exposure_pct:
            return False, f"Exposure {exposure_pct:.1f}% exceeds {self.config.max_total_exposure_pct}%"

        # Max positions
        if len(positions) >= self.config.max_positions:
            already_has = any(p.symbol == signal.symbol for p in positions)
            if not already_has:
                return False, f"Max positions ({self.config.max_positions}) reached"

        # Duplicate check
        if any(p.symbol == signal.symbol for p in positions):
            return False, f"Already holding {signal.symbol}"

        # Risk/reward check
        rr = signal.risk_reward
        if rr is not None and rr < 1.0:
            return False, f"Risk/reward {rr:.2f} below minimum 1.0"

        # Hard stop check
        if signal.entry_price and signal.stop_loss:
            loss_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100
            if loss_pct > self.config.hard_stop_pct:
                return False, f"Stop distance {loss_pct:.1f}% exceeds hard limit {self.config.hard_stop_pct}%"

        return True, "OK"

    # ------------------------------------------------------------------
    # Trailing stop management
    # ------------------------------------------------------------------

    def update_trailing_stop(
        self, symbol: str, current_price: float, atr: Optional[float] = None
    ) -> Optional[float]:
        """
        Update and return trailing stop price.
        Returns new stop price if it should be moved, None if no change.
        """
        if not self.config.trailing_stop_enabled:
            return None

        prev_high = self._high_water.get(symbol, 0)
        if current_price > prev_high:
            self._high_water[symbol] = current_price

        high = self._high_water[symbol]

        if atr and atr > 0:
            trail_stop = high - (self.config.trailing_stop_atr_multiple * atr)
        else:
            trail_pct = self.config.trailing_stop_atr_multiple * 0.5  # fallback %
            trail_stop = high * (1 - trail_pct / 100)

        return round(trail_stop, 2)

    # ------------------------------------------------------------------
    # EOD check
    # ------------------------------------------------------------------

    def should_close_all(self, minutes_to_close: Optional[float]) -> bool:
        """Should we close everything for end of day?"""
        if not self.config.close_all_eod:
            return False
        if minutes_to_close is None:
            return False
        return minutes_to_close <= self.config.eod_minutes_before_close

    def should_stop_new_entries(self, minutes_to_close: Optional[float]) -> bool:
        """Should we stop opening new positions?"""
        if minutes_to_close is None:
            return False
        return minutes_to_close <= self.config.no_trade_last_minutes

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def record_entry(self):
        """Count one round-trip against the daily trade limit (on entry)."""
        self.daily_trade_count += 1

    def record_pnl(self, pnl: float):
        """Accumulate realized P&L for the day (on exit). Does not affect the
        daily trade count — a round-trip is counted once, at entry."""
        self.daily_pnl += pnl

    def clear_symbol(self, symbol: str):
        self._high_water.pop(symbol, None)
