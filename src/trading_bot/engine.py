from __future__ import annotations

import os, time, logging
from typing import Dict, List

from .broker_alpaca import AlpacaBroker
from .settings import get_settings
from .state import add_event, set_health, set_kv, get_kv, upsert_position
from .universe import load_universe
from .news import fetch_newsapi_counts
from .news import get_news_counts
from .earnings import fetch_earnings_calendar
from .strategies import score_stock_candidates


log = logging.getLogger("engine")

class Trader:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.broker = AlpacaBroker(
            paper=str(os.environ.get("ALPACA_PAPER", "true")).lower() in ("1","true","yes"),
            feed=os.environ.get("ALPACA_DATA_FEED", "iex"),
            timeout=float(os.environ.get("ALPACA_TIMEOUT", "6.0")),
        )
        self.crypto_enabled = bool(self.settings.get("crypto", {}).get("enabled"))
        self.strict_batch_only = bool(self.settings.get("data", {}).get("strict_batch_only"))
        self.health_interval_s = int(self.settings.get("scheduling", {}).get("health_refresh_min", 20)) * 60
        self._last_health_ts = 0.0

        self.stock_universe: List[str] = load_universe()
        # cadence
        sched = self.settings.get("scheduling", {})
        self._last_news_ts = 0.0
        self._news_interval_s = int(sched.get("news_interval_s", 600))
        self._last_earn_ts = 0.0
        self._earn_interval_s = int(sched.get("earnings_refresh_min", 60))*60
        self._last_cand_ts = 0.0
        self._cand_interval_s = int(sched.get("candidate_refresh_min", 20))*60

        # working
        self._news_counts: Dict[str,int] = {}
        self._earnings: Dict[str,str] = {}

    # ---------------- Health ----------------
    def _health_check(self) -> None:
        now = time.time()
        set_health("health_last_run", True, f"cadence={self.health_interval_s//60}min", ts=now)
        try:
            clock = self.broker.get_clock()
            set_health("clock", True, f"is_open={clock.get('is_open')}")
        except Exception as e:
            set_health("clock", False, str(e))
        try:
            acct = self.broker.get_account()
            set_health("account", True, f"equity={acct.get('equity')}")
        except Exception as e:
            set_health("account", False, str(e))
        try:
            pos = self.broker.list_positions() or []
            set_health("positions", True, f"{len(pos)} positions")
        except Exception as e:
            set_health("positions", False, str(e))
        try:
            sample = self.settings.get("scheduling", {}).get("health_stock_symbols", ["AAPL","MSFT","NVDA"])
            snaps = self.broker.snapshots_batch_stocks(sample)
            got = len(snaps) if isinstance(snaps, dict) else 0
            ok = bool(snaps) and got > 0
            set_health("marketdata_stocks_smoke", ok, f"got={got} sample={','.join(sample)}")
        except Exception as e:
            set_health("marketdata_stocks_smoke", False, str(e))
        if self.crypto_enabled:
            try:
                cu = self.settings.get("crypto", {}).get("universe", ["BTCUSD","ETHUSD"])
                csnaps = self.broker.snapshots_batch_crypto(cu)
                got = len(csnaps) if isinstance(csnaps, dict) else 0
                ok = bool(csnaps) and got > 0
                set_health("marketdata_crypto_smoke", ok, f"got={got} universe={','.join(cu)}")
            except Exception as e:
                set_health("marketdata_crypto_smoke", False, str(e))

    def self_test(self) -> None:
        self._health_check()
        add_event("INFO", "self-test ok")

    # ---------------- Schedulers ----------------
    def _refresh_positions(self) -> None:
        try:
            pos = self.broker.list_positions() or []
            for p in pos:
                sym = p.get("symbol")
                qty = float(p.get("qty") or p.get("quantity") or 0)
                avg = float(p.get("avg_entry_price") or p.get("avg_price") or 0)
                if sym: upsert_position(sym, qty, avg)
            add_event("INFO", f"positions refreshed: {len(pos)}")
        except Exception as e:
            add_event("ERROR", "positions refresh failed", {"err": str(e)})

    def _refresh_news(self) -> None:
        try:
            cfg = self.settings.get("news", {}) or {}
            counts = get_news_counts(
                self.stock_universe,
                cfg.get("window_hours", 6),
                cfg.get("provider_order", ["alpaca", "finnhub", "newsapi"]),
                rotate_batch=int(cfg.get("rotate_batch", 60)),
            )
            self._news_counts = counts or {}
            set_kv("news_counts", self._news_counts)
            total = sum(self._news_counts.values())
            ok = total > 0
            set_health("news_scheduler", ok, f"total_hits={total}; providers={cfg.get('provider_order')}",
                       ts=time.time())
            if not ok:
                # don’t spam; a WARN in events is enough to tell us why there’s no data
                from .state import add_event
                add_event("WARN", "news counts empty (likely rate-limit or no recent items)")
        except Exception as e:
            set_health("news_scheduler", False, str(e))


    def _refresh_earnings(self) -> None:
        try:
            cal = fetch_earnings_calendar()
            self._earnings = cal
            set_kv("earnings_calendar", cal)
            set_health("earnings_scheduler", True, f"symbols={len(cal)}", ts=time.time())
        except Exception as e:
            set_health("earnings_scheduler", False, str(e))

    def _refresh_candidates(self) -> None:
        # limit universe per settings
        max_syms = int(self.settings.get("scheduling", {}).get("candidate_max_symbols", 200))
        syms = self.stock_universe[:max_syms]
        snaps = {}
        try:
            snaps = self.broker.snapshots_batch_stocks(syms)
        except Exception as e:
            add_event("ERROR", "stocks snapshots failed", {"err": str(e)})
        stocks = score_stock_candidates(snaps, self._news_counts, self._earnings)

        # crypto optional
        cands = {**stocks}
        if self.settings.get("crypto", {}).get("enabled"):
            cu = self.settings.get("crypto", {}).get("universe", ["BTCUSD","ETHUSD"])
            try:
                csnaps = self.broker.snapshots_batch_crypto(cu)
                for sym, snap in (csnaps or {}).items():
                    mb = (snap.get("minuteBar") or {}).get("c")
                    db = (snap.get("dailyBar") or {}).get("c")
                    mover = 0.0
                    try:
                        if mb and db and float(db) > 0:
                            mover = float(mb)/float(db) - 1.0
                    except Exception:
                        mover = 0.0
                    cands[sym] = {"mover": mover, "score": max(0.0, min(1.0, 0.5 + 4.0*mover))}
            except Exception as e:
                add_event("ERROR", "crypto snapshots failed", {"err": str(e)})
        set_kv("candidates", cands)

    # ---------------- Main loop ----------------
    def run(self) -> None:
        add_event("INFO", "loop start")
        while True:
            now = time.time()

            # manual health trigger
            run_req = get_kv("health_run_request", None)
            if run_req:
                self._health_check()
                set_kv("health_run_request", None)

            # health cadence
            if now - self._last_health_ts >= self.health_interval_s:
                self._health_check()
                self._last_health_ts = now

            # schedulers
            if now - self._last_news_ts >= self._news_interval_s and self.settings.get("strategies", {}).get("news"):
                self._refresh_news()
                self._last_news_ts = now

            if now - self._last_earn_ts >= self._earn_interval_s and self.settings.get("strategies", {}).get("earnings"):
                self._refresh_earnings()
                self._last_earn_ts = now

            if now - self._last_cand_ts >= self._cand_interval_s:
                self._refresh_candidates()
                self._last_cand_ts = now

            # positions every loop
            self._refresh_positions()

            time.sleep(20)
