"""
Scheduler: runs the slow daily jobs the always-on engine doesn't do.

  09:35 ET on trading days   core rebalance (prior-close signals, next-open fills)
  16:15 ET Mon-Fri           heartbeat (+ IRA review when IRA_REVIEW_ENABLED)

It is its own compose service (`scheduler`) with restart: always, sharing the
image, .env, ./data and ./trade_logs with the engine. One loop, one job at a
time, each job wrapped so an exception becomes an alert instead of a crash,
and a per-day marker so a restart can't double-run a job.

Trading days come from Alpaca's calendar (holidays included), fetched once
per day; if that call fails the rebalance falls back to "is the market open
right now?", which is correct at 09:35 on any trading day.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, Optional
from zoneinfo import ZoneInfo

from . import heartbeat as hb
from .broker import AlpacaBroker
from .config import Config
from .core import CorePortfolio
from .notify import Notifier

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


def _parse_hhmm(s: str, default: str) -> tuple:
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except (ValueError, AttributeError):
        pass
    h, m = default.split(":")
    return int(h), int(m)


class Scheduler:
    def __init__(self, config: Config, broker: Optional[AlpacaBroker] = None,
                 notifier: Optional[Notifier] = None, *, now: Callable[[], datetime] = None):
        self.config = config
        self.broker = broker or AlpacaBroker(config.broker)
        self.notifier = notifier or Notifier()
        self._now = now or (lambda: datetime.now(_ET))
        self._done: Dict[str, str] = {}          # job -> date last run
        self._calendar_day: Optional[str] = None
        self._is_trading_day: Optional[bool] = None
        self.rebalance_at = _parse_hhmm(config.core.rebalance_time, "09:35")
        self.heartbeat_at = _parse_hhmm(config.core.heartbeat_time, "16:15")

    # ---- calendar ------------------------------------------------------------
    def is_trading_day(self, d: date) -> bool:
        key = d.isoformat()
        if self._calendar_day == key and self._is_trading_day is not None:
            return self._is_trading_day
        verdict: Optional[bool] = None
        try:
            cal = self.broker.get_calendar(key, key)
            verdict = any(str(c.get("date")) == key for c in cal)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler: calendar fetch failed: %s", exc)
        if verdict is None:
            verdict = d.weekday() < 5 and bool(self.broker.is_market_open())
        self._calendar_day, self._is_trading_day = key, verdict
        return verdict

    # ---- jobs ------------------------------------------------------------------
    def job_rebalance(self) -> None:
        if not self.config.core.enabled:
            logger.info("scheduler: core disabled; rebalance skipped")
            return
        core = CorePortfolio(self.config, self.broker, self.notifier)
        placed = core.rebalance(dry_run=self.config.dry_run)
        if placed:
            lines = [f"{t.side} {t.quantity} {t.symbol} ~${t.est_value:,.0f}  {t.reason}" for t in placed]
            self.notifier.send(f"[trader] core rebalance {self._now().date()}: {len(placed)} orders",
                               "\n".join(lines), push_body="; ".join(l.split("  ")[0] for l in lines))

    def job_heartbeat(self) -> None:
        hb.run(self.config, self.broker, self.notifier, send=True)

    # ---- loop ------------------------------------------------------------------
    def _due(self, job: str, at: tuple, now: datetime, window_min: int = 90) -> bool:
        """Due once per day, from `at` until `at`+window (so a restart shortly
        after the slot still runs it, but a restart hours later does not)."""
        if self._done.get(job) == now.date().isoformat():
            return False
        start = now.replace(hour=at[0], minute=at[1], second=0, microsecond=0)
        return start <= now < start + timedelta(minutes=window_min)

    def tick(self) -> None:
        now = self._now()
        today = now.date()
        if self._due("rebalance", self.rebalance_at, now):
            self._done["rebalance"] = today.isoformat()
            if self.is_trading_day(today):
                self._run("rebalance", self.job_rebalance)
            else:
                logger.info("scheduler: %s is not a trading day; no rebalance", today)
        if self._due("heartbeat", self.heartbeat_at, now, window_min=180):
            self._done["heartbeat"] = today.isoformat()
            if today.weekday() < 5:
                self._run("heartbeat", self.job_heartbeat)

    def _run(self, name: str, fn: Callable[[], None]) -> None:
        logger.info("scheduler: running %s", name)
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.error("scheduler: %s failed: %s", name, exc, exc_info=True)
            try:
                self.notifier.alert(f"[trader] {name} FAILED", f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass

    def run_forever(self, interval_s: int = 30) -> None:
        logger.info("scheduler up: rebalance %02d:%02d ET (core %s), heartbeat %02d:%02d ET, "
                    "channels: %s", *self.rebalance_at,
                    "ON" if self.config.core.enabled else "off", *self.heartbeat_at,
                    self.notifier.config.describe())
        # Prove the credentials at startup; a dead key should page immediately,
        # not wait for the first 16:15.
        if not self.broker.get_account():
            self.notifier.alert("[trader] scheduler: account unreachable at startup",
                                "Alpaca /v2/account returned nothing. Check APCA_* in .env.")
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("scheduler tick error: %s", exc, exc_info=True)
            time.sleep(interval_s)
