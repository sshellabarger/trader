"""
Scanner — finds candidate stocks for day trading.

Two modes:
  1. Pre-market scan  → gappers with volume (for Gap & Go, ORB)
  2. Intraday scan    → momentum + volume surges during the session

Relies on Alpaca batch snapshots to stay within rate limits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .broker import AlpacaBroker
from .config import ScannerConfig

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    """A stock that passed the scanner filters."""
    symbol: str
    price: float
    prev_close: float
    gap_pct: float               # (open - prev_close) / prev_close × 100
    change_pct: float            # (current - prev_close) / prev_close × 100
    volume: float                # today's volume so far
    avg_volume: float            # previous day volume (proxy for avg)
    relative_volume: float       # volume / avg_volume
    high: float
    low: float
    open_price: float
    source: str = "snapshot"     # how we found it
    extra: Dict = field(default_factory=dict)

    @property
    def range_pct(self) -> float:
        return ((self.high - self.low) / self.open_price * 100) if self.open_price > 0 else 0.0


def scan_candidates(
    broker: AlpacaBroker,
    universe: List[str],
    config: ScannerConfig,
    long_bias: bool = False,
) -> List[Candidate]:
    """
    Scan a universe of symbols and return sorted candidates.
    Uses batch snapshots for efficiency.

    With long_bias=True, gap-UP names rank ahead of gap-downs (score order
    within each group). This matters because the trim below caps the list:
    a long-only consumer would otherwise lose a tradeable gap-up that scored
    just below five untradeable gap-downs.
    """
    if not universe:
        logger.warning("Empty universe — nothing to scan")
        return []

    # Fetch snapshots in bulk
    snapshots = broker.get_snapshots(universe)
    if not snapshots:
        logger.warning("No snapshots returned")
        return []

    candidates: List[Candidate] = []

    for symbol, snap in snapshots.items():
        try:
            candidate = _evaluate_snapshot(symbol, snap, config)
            if candidate is not None:
                candidates.append(candidate)
        except Exception as exc:
            logger.debug(f"Error evaluating {symbol}: {exc}")

    # Sort by absolute gap then relative volume (best candidates first).
    # long_bias additionally ranks all gap-ups ahead of all gap-downs.
    if long_bias:
        candidates.sort(
            key=lambda c: (c.gap_pct < 0, -(abs(c.gap_pct) * c.relative_volume)))
    else:
        candidates.sort(key=lambda c: (abs(c.gap_pct) * c.relative_volume), reverse=True)

    # Trim to max candidates
    candidates = candidates[: config.max_candidates]
    logger.info(f"Scanner found {len(candidates)} candidates from {len(snapshots)} symbols")
    return candidates


def _evaluate_snapshot(
    symbol: str, snap: Dict, config: ScannerConfig
) -> Optional[Candidate]:
    """Evaluate a single snapshot against scanner filters. Returns Candidate or None."""

    # Extract prices
    latest_trade = snap.get("latestTrade") or {}
    daily_bar = snap.get("dailyBar") or {}
    prev_bar = snap.get("prevDailyBar") or {}
    minute_bar = snap.get("minuteBar") or {}

    current_price = float(latest_trade.get("p", 0) or minute_bar.get("c", 0))
    if current_price <= 0:
        return None

    open_price = float(daily_bar.get("o", current_price))
    high = float(daily_bar.get("h", current_price))
    low = float(daily_bar.get("l", current_price))
    volume = float(daily_bar.get("v", 0))
    prev_close = float(prev_bar.get("c", 0))
    prev_volume = float(prev_bar.get("v", 1))  # avoid div/0

    # ----- Filters -----

    # Price range
    if current_price < config.min_price or current_price > config.max_price:
        return None

    # Volume
    if prev_volume < config.min_volume:
        return None

    # Gap calculation
    gap_pct = 0.0
    if prev_close > 0:
        gap_pct = ((open_price - prev_close) / prev_close) * 100

    # Change from previous close
    change_pct = 0.0
    if prev_close > 0:
        change_pct = ((current_price - prev_close) / prev_close) * 100

    # Relative volume
    relative_volume = volume / prev_volume if prev_volume > 0 else 0.0

    # Must have some activity
    if relative_volume < 0.5:
        return None

    return Candidate(
        symbol=symbol,
        price=current_price,
        prev_close=prev_close,
        gap_pct=gap_pct,
        change_pct=change_pct,
        volume=volume,
        avg_volume=prev_volume,
        relative_volume=relative_volume,
        high=high,
        low=low,
        open_price=open_price,
    )


def filter_gap_candidates(
    candidates: List[Candidate], config: ScannerConfig
) -> List[Candidate]:
    """Filter candidates that qualify for Gap & Go strategy."""
    return [
        c for c in candidates
        if abs(c.gap_pct) >= config.min_gap_pct
        and abs(c.gap_pct) <= config.max_gap_pct
        and c.relative_volume >= config.min_relative_volume
    ]


def filter_orb_candidates(
    candidates: List[Candidate], config: ScannerConfig
) -> List[Candidate]:
    """Filter candidates suitable for Opening Range Breakout."""
    return [
        c for c in candidates
        if c.relative_volume >= config.min_relative_volume
        and c.range_pct >= 0.2   # needs some initial range
    ]


def filter_vwap_candidates(
    candidates: List[Candidate], config: ScannerConfig
) -> List[Candidate]:
    """Filter candidates for VWAP reversion (need volume and a move away from open)."""
    return [
        c for c in candidates
        if c.relative_volume >= 1.0
        and abs(c.change_pct) >= 0.5  # needs to have moved
    ]
