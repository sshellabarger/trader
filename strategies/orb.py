"""
5-Minute Opening Range Breakout (ORB) — ETF Edition
Based on Zarattini & Aziz (2023): "Can Day Trading Really Be Profitable?"

ORB is LONG-ONLY per instrument. The opening range is the first
`orb_range_minutes` of the regular session, located by bar timestamp
(09:30 ET onward) so premarket bars and DST shifts cannot skew it.

  - Range closes UP from its open → LONG, stop at range low, target +10R.
  - Range closes DOWN (or flat)   → no trade on this instrument.

Profiting on DOWN days does not require shorting: the engine also trades
the inverse 3x ETF (SQQQ). A down-Nasdaq day breaks SQQQ's opening range
UP, so the same long-only logic goes long SQQQ. Every order is a long —
no borrow, no naked-short gap risk.

All prices are in the traded symbol's own terms: the strategy signals only
on `candidate.symbol` and never substitutes a different instrument.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import BaseStrategy, Candidate, Config, Signal, SignalAction, SignalDirection

_ET = ZoneInfo("America/New_York")


def _bar_time_et(bar: Dict) -> Optional[datetime]:
    """Parse a bar's timestamp into an ET-aware datetime, or None if unusable."""
    t = bar.get("t", "")
    if not isinstance(t, str):
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None  # fail closed rather than guess the timezone
    return dt.astimezone(_ET)


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
        if position is not None:
            return None
        if self.is_blocked(candidate.symbol):
            return None

        sliced = self._slice_session(bars)
        if sliced is None:
            return None
        range_bars, entry_bar = sliced

        return self._check_entry(candidate, range_bars, entry_bar, indicators)

    def _slice_session(
        self, bars: List[Dict]
    ) -> Optional[Tuple[List[Dict], Dict]]:
        """
        Locate the opening range and the entry bar by timestamp.

        Returns (range_bars, entry_bar) where range_bars fall inside
        [09:30, 09:30 + orb_range_minutes) ET and entry_bar is the latest
        bar, which must start inside the entry window immediately after the
        range. Returns None if the range hasn't completed yet, the entry
        window has passed, the range is too sparse, or timestamps are
        unusable.
        """
        if not bars:
            return None
        last_et = _bar_time_et(bars[-1])
        if last_et is None:
            return None

        open_dt = last_et.replace(hour=9, minute=30, second=0, microsecond=0)
        range_end = open_dt + timedelta(minutes=self.sc.orb_range_minutes)
        window_end = range_end + timedelta(minutes=self.sc.orb_entry_window_minutes)

        if not (range_end <= last_et < window_end):
            return None

        range_bars = []
        for b in bars:
            bt = _bar_time_et(b)
            if bt is not None and open_dt <= bt < range_end:
                range_bars.append(b)

        # Require enough coverage of the opening range; a 1-2 bar "range" on a
        # sparse IEX open is noise, not a range, and must not set a 10R trade.
        if len(range_bars) < self.sc.orb_min_range_bars:
            return None

        return range_bars, bars[-1]

    def _check_entry(
        self,
        candidate: Candidate,
        range_bars: List[Dict],
        entry_bar: Dict,
        indicators: Dict,
    ) -> Optional[Signal]:

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

        # Long-only: only a bullish opening range is a setup on this symbol.
        if range_close <= range_open:
            return None

        entry_price = float(entry_bar["c"])
        stop_loss = range_low
        risk = entry_price - stop_loss
        if risk <= 0:
            return None
        take_profit = entry_price + (risk * self.sc.orb_profit_target_r)
        if take_profit <= 0:
            return None

        return Signal(
            symbol=candidate.symbol,
            strategy=self.name,
            action=SignalAction.ENTER,
            direction=SignalDirection.LONG,
            strength=0.7,
            entry_price=entry_price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reason=(
                f"ORB long: {self.sc.orb_range_minutes}min range closed up "
                f"({range_open:.2f}->{range_close:.2f}), range=${range_size:.2f}"
            ),
            indicators={
                "range_high": range_high,
                "range_low": range_low,
                "range_size": round(range_size, 2),
                "direction": "long",
                "atr": atr,
            },
        )

    def can_open(self, symbol: str) -> bool:
        # One ORB entry per day across all instruments. evaluate() can't enforce
        # this within a tick (it runs for every symbol before any order fills),
        # so the engine consults this just before each entry executes — once one
        # ORB entry fills, the other instrument is blocked the same day. Without
        # it a choppy open could buy BOTH TQQQ and SQQQ (a delta-neutral straddle).
        return not self._today_signal_fired

    def on_fill(self, symbol: str, signal: Signal):
        """Consume the day's ORB signal only once an order has actually filled.

        Setting the flag here (not at signal generation) means a rejected or
        unfilled order does not forfeit the day — the entry window allows a
        retry on the next tick.
        """
        super().on_fill(symbol, signal)
        if signal.action == SignalAction.ENTER:
            self._today_signal_fired = True

    def reset_daily(self):
        super().reset_daily()
        self._today_signal_fired = False
