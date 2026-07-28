"""
Kalshi market recorder — the phase-0 workhorse.

Appends compact JSONL snapshots of every tracked market so the later fair-value
models and paper engine can be scored against what the market ACTUALLY showed,
including whether a resting limit would have been filled. Three line types,
one file per UTC day plus a settlements file:

  {"t": ..., "type": "md",     "ticker": ..., "yes_bid": ..., "yes_ask": ...,
   "no_bid": ..., "no_ask": ..., "last": ..., "vol": ..., "vol24": ...,
   "oi": ..., "close": ..., "status": ...}
  {"t": ..., "type": "book",   "ticker": ..., "yes": [[price, count], ...],
   "no": [[price, count], ...]}                       (markets near close only)
  {"t": ..., "type": "settle", "ticker": ..., "result": ..., "close": ...}

Design notes:
- Poll cadence is per SERIES: base_interval_sec normally, hot_interval_sec when
  any market in the series closes within hot_window_hours (sports pre-game,
  econ release windows, weather late afternoon). This keeps the request budget
  flat while catching the minutes that matter.
- Order books cost one request per market, so only markets closing within
  book_window_hours get them, capped at book_max_per_cycle, soonest first.
- A series that returns no open markets warns once per UTC day (a renamed
  ticker is visible in logs, never a silent gap) and keeps polling at the base
  rate so it picks up new listings (e.g. KXNFLGAME in late August).
- The settlement sweep runs at startup and on every UTC date change, deduped
  against settlements.jsonl, so outcomes join the snapshots automatically.
- Clock and client are injectable for tests; writes are plain append-only
  JSONL, crash-safe by construction.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from .client import KalshiClient
from .config import KalshiConfig

logger = logging.getLogger(__name__)

# Market fields copied into "md" lines (compact key -> API field).
_MD_FIELDS = (
    ("yes_bid", "yes_bid"),
    ("yes_ask", "yes_ask"),
    ("no_bid", "no_bid"),
    ("no_ask", "no_ask"),
    ("last", "last_price"),
    ("vol", "volume"),
    ("vol24", "volume_24h"),
    ("oi", "open_interest"),
    ("close", "close_time"),
    ("status", "status"),
)


def _parse_close_epoch(close_time: Optional[str]) -> Optional[float]:
    """Kalshi close_time is ISO 8601 (e.g. '2026-07-29T22:00:00Z')."""
    if not close_time or not isinstance(close_time, str):
        return None
    try:
        return datetime.fromisoformat(
            close_time.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class KalshiRecorder:
    def __init__(
        self,
        client: Optional[KalshiClient] = None,
        config: Optional[KalshiConfig] = None,
        now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.config = config or KalshiConfig()
        self.client = client or KalshiClient(self.config)
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn

        self._next_poll: Dict[str, float] = {
            s: 0.0 for s in self.config.series_list()}
        self._warned_empty: Dict[str, str] = {}   # series -> UTC date warned
        self._settled_seen: Set[str] = set()
        self._settle_sweep_date: str = ""          # UTC date of last sweep
        self.lines_written = 0
        self.cycles = 0

        os.makedirs(self.config.data_dir, exist_ok=True)
        self._load_settled_seen()

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def _utc_date(self) -> str:
        return datetime.fromtimestamp(
            self.now_fn(), tz=timezone.utc).strftime("%Y%m%d")

    def _iso_now(self) -> str:
        return datetime.fromtimestamp(
            self.now_fn(), tz=timezone.utc).isoformat(timespec="seconds")

    def _snapshot_path(self) -> str:
        return os.path.join(self.config.data_dir,
                            f"snapshots-{self._utc_date()}.jsonl")

    def _settlements_path(self) -> str:
        return os.path.join(self.config.data_dir, "settlements.jsonl")

    def _append(self, path: str, obj: Dict[str, Any]):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self.lines_written += 1

    def _load_settled_seen(self):
        """Rebuild the settlement dedupe set from disk so restarts never
        double-write outcomes."""
        path = self._settlements_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        tick = json.loads(line).get("ticker")
                        if tick:
                            self._settled_seen.add(tick)
                    except (ValueError, AttributeError):
                        continue
        except OSError as exc:
            logger.warning(f"Could not read settlements file: {exc}")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_series(self, series: str) -> List[Dict]:
        """Snapshot one series' open markets; returns markets closing within
        the book window (candidates for depth snapshots)."""
        now = self.now_fn()
        markets = self.client.get_markets(series_ticker=series, status="open")

        if not markets:
            today = self._utc_date()
            if self._warned_empty.get(series) != today:
                logger.warning(
                    f"Series {series}: no open markets (renamed ticker, "
                    f"off-season, or venue issue) — will keep checking")
                self._warned_empty[series] = today
            self._next_poll[series] = now + self.config.base_interval_sec
            return []

        path = self._snapshot_path()
        ts = self._iso_now()
        hot = False
        book_candidates: List[Dict] = []
        hot_horizon = self.config.hot_window_hours * 3600.0
        book_horizon = self.config.book_window_hours * 3600.0

        for m in markets:
            line: Dict[str, Any] = {
                "t": ts, "type": "md",
                "ticker": m.get("ticker"),
                "event": m.get("event_ticker"),
            }
            for compact, field in _MD_FIELDS:
                if m.get(field) is not None:
                    line[compact] = m.get(field)
            self._append(path, line)

            close_epoch = _parse_close_epoch(m.get("close_time"))
            if close_epoch is not None and close_epoch > now:
                if close_epoch - now <= hot_horizon:
                    hot = True
                if close_epoch - now <= book_horizon:
                    book_candidates.append(
                        {"ticker": m.get("ticker"), "close_epoch": close_epoch})

        interval = (self.config.hot_interval_sec if hot
                    else self.config.base_interval_sec)
        self._next_poll[series] = now + interval
        return book_candidates

    def snapshot_books(self, candidates: List[Dict]):
        """Depth snapshots for markets nearest to close, within the request
        budget (one request per book)."""
        if not candidates:
            return
        candidates = sorted(candidates, key=lambda c: c.get("close_epoch", 0))
        path = self._snapshot_path()
        for cand in candidates[: self.config.book_max_per_cycle]:
            ticker = cand.get("ticker")
            if not ticker:
                continue
            book = self.client.get_orderbook(ticker,
                                             depth=self.config.book_depth)
            if book is None:
                continue
            self._append(path, {
                "t": self._iso_now(), "type": "book", "ticker": ticker,
                "yes": (book.get("yes") or [])[: self.config.book_depth],
                "no": (book.get("no") or [])[: self.config.book_depth],
            })

    # ------------------------------------------------------------------
    # Settlements
    # ------------------------------------------------------------------

    def sweep_settlements(self) -> int:
        """Fetch recently settled markets for every tracked series and append
        unseen outcomes. Returns the number of new settlements written."""
        since = int(self.now_fn() - self.config.settlement_lookback_hours * 3600)
        path = self._settlements_path()
        wrote = 0
        for series in self.config.series_list():
            for m in self.client.get_settlements(series, since):
                ticker = m.get("ticker")
                if not ticker or ticker in self._settled_seen:
                    continue
                self._settled_seen.add(ticker)
                self._append(path, {
                    "t": self._iso_now(), "type": "settle",
                    "ticker": ticker,
                    "event": m.get("event_ticker"),
                    "result": m.get("result"),
                    "close": m.get("close_time"),
                })
                wrote += 1
        self._settle_sweep_date = self._utc_date()
        if wrote:
            logger.info(f"Settlement sweep: {wrote} new outcomes recorded")
        return wrote

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def cycle(self):
        """One scheduler tick: poll due series, snapshot books, sweep
        settlements on date change."""
        now = self.now_fn()
        if self._settle_sweep_date != self._utc_date():
            self.sweep_settlements()

        book_candidates: List[Dict] = []
        for series, due in list(self._next_poll.items()):
            if now >= due:
                book_candidates.extend(self.poll_series(series))
        self.snapshot_books(book_candidates)
        self.cycles += 1

    def run_forever(self, tick_seconds: float = 1.0):
        cfg = self.config
        logger.info(
            f"Kalshi recorder up: {len(self._next_poll)} series "
            f"({', '.join(sorted(self._next_poll))}), "
            f"base {cfg.base_interval_sec}s / hot {cfg.hot_interval_sec}s, "
            f"books last {cfg.book_window_hours}h before close, "
            f"-> {cfg.data_dir}")
        status = self.client.get_exchange_status()
        if status:
            logger.info(f"Exchange status: {status}")
        last_report = self.now_fn()
        try:
            while True:
                self.cycle()
                if self.now_fn() - last_report >= 600:
                    logger.info(
                        f"Recorder: {self.cycles} cycles, "
                        f"{self.lines_written} lines written")
                    last_report = self.now_fn()
                self.sleep_fn(tick_seconds)
        except KeyboardInterrupt:
            logger.info(
                f"Recorder stopped: {self.cycles} cycles, "
                f"{self.lines_written} lines written")
