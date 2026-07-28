"""
Kalshi sleeve configuration — env-driven like the rest of the bot, so the
droplet can tune the recorder without a code change. Code defaults are safe:
public data only, verified series, modest poll rates well under Kalshi's
documented ~10 req/s basic tier.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from ..config import _env_float, _env_int  # shared .env loading + parsers

# Verified live series as of 2026-07-28 (checked against the public API):
#   KXHIGHCHI / KXHIGHNY / KXHIGHDEN  daily high-temperature (weather lab)
#   KXCPI                             monthly CPI MoM (econ sleeve)
#   KXMLBGAME                         MLB single-game winners (sports sleeve)
# Unverified-but-expected (recorder warns once/day if a series returns nothing,
# so a rename is visible in the logs, never a silent gap):
#   KXHIGHAUS                         Austin daily high
#   KXNFLGAME                         NFL single-game winners (lists ~Aug/Sep)
# Use `python -m trader kalshi-discover` to browse current series tickers.
_DEFAULT_SERIES = (
    "KXHIGHCHI,KXHIGHNY,KXHIGHDEN,KXHIGHAUS,KXCPI,KXMLBGAME,KXNFLGAME"
)


@dataclass
class KalshiConfig:
    # ── Exchange endpoints ───────────────────────────────────────────────
    # Production. For order-code testing use the demo exchange instead:
    #   KALSHI_BASE_URL=https://demo-api.kalshi.co/trade-api/v2
    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    # ── Auth (trading/portfolio endpoints only — the recorder never needs
    # these; market data is public). Key ID comes from the Kalshi dashboard;
    # the private key PEM stays on disk and is used to sign requests locally.
    # repr=False for the same reason as BrokerConfig: secrets never belong in
    # dataclass reprs (pytest failure pastes, log echoes).
    api_key_id: str = field(default="", repr=False)
    private_key_path: str = field(default="", repr=False)

    # ── Recorder: what to track ──────────────────────────────────────────
    series: str = _DEFAULT_SERIES

    # ── Recorder: cadence ────────────────────────────────────────────────
    # base: routine polling. hot: any series with a market closing within
    # hot_window_hours (sports in the pre-game/in-game window, econ around a
    # release, weather late afternoon) is polled at the faster rate.
    base_interval_sec: int = 60
    hot_interval_sec: int = 10
    hot_window_hours: float = 2.0

    # ── Recorder: order-book depth snapshots ─────────────────────────────
    # Full books are fetched only for markets near close (one request each,
    # so capped per cycle and prioritized by soonest close). book_depth is
    # levels per side kept in the JSONL line.
    book_window_hours: float = 2.0
    book_max_per_cycle: int = 20
    book_depth: int = 5

    # ── Recorder: output + rate budget ───────────────────────────────────
    data_dir: str = "data/kalshi"
    requests_per_second: float = 5.0     # stay well under the ~10/s basic tier
    settlement_lookback_hours: int = 72  # daily sweep looks back this far

    def __post_init__(self):
        self.base_url = os.getenv("KALSHI_BASE_URL", self.base_url).rstrip("/")
        self.api_key_id = self.api_key_id or os.getenv("KALSHI_API_KEY_ID", "")
        self.private_key_path = self.private_key_path or os.getenv(
            "KALSHI_PRIVATE_KEY_PATH", "")
        self.series = os.getenv("KALSHI_SERIES", self.series)
        self.base_interval_sec = _env_int(
            "KALSHI_BASE_INTERVAL_SEC", self.base_interval_sec)
        self.hot_interval_sec = _env_int(
            "KALSHI_HOT_INTERVAL_SEC", self.hot_interval_sec)
        self.hot_window_hours = _env_float(
            "KALSHI_HOT_WINDOW_HOURS", self.hot_window_hours)
        self.book_window_hours = _env_float(
            "KALSHI_BOOK_WINDOW_HOURS", self.book_window_hours)
        self.book_max_per_cycle = _env_int(
            "KALSHI_BOOK_MAX_PER_CYCLE", self.book_max_per_cycle)
        self.book_depth = _env_int("KALSHI_BOOK_DEPTH", self.book_depth)
        self.data_dir = os.getenv("KALSHI_DATA_DIR", self.data_dir)
        self.requests_per_second = _env_float(
            "KALSHI_REQUESTS_PER_SECOND", self.requests_per_second)
        self.settlement_lookback_hours = _env_int(
            "KALSHI_SETTLEMENT_LOOKBACK_HOURS", self.settlement_lookback_hours)

    def series_list(self) -> list:
        return [s.strip().upper() for s in self.series.split(",") if s.strip()]


def taker_fee_cents(price_cents: int, count: int = 1,
                    multiplier: float = 0.07) -> int:
    """Kalshi's published taker fee: ceil(multiplier x C x P x (1-P)) in cents,
    peaking ~1.75c/contract at 50c. Maker (resting) orders are ~free. Kept here
    so the later paper engine and any notebook use ONE fee definition.
    """
    p = max(0, min(100, price_cents)) / 100.0
    return int(math.ceil(multiplier * count * p * (1.0 - p) * 100))
