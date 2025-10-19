
from __future__ import annotations
import os, time, threading, logging
from typing import Dict, List, Any
from .logger import configure_logging
from .settings import get_settings
from .state import set_health, add_event, set_kv, get_kv, record_trade
from .universe import load_universe
from .broker_alpaca import AlpacaBroker
from .news import get_news_counts
from .earnings import refresh_earnings_calendar
from .strategies import score_momentum, score_mean_reversion, score_news, combine_scores
log = logging.getLogger("engine")
class Trader:
    def __init__(self):
        configure_logging()
        self.settings = get_settings()
        self.broker = AlpacaBroker(feed=os.environ.get("ALPACA_DATA_FEED","iex"), paper=(os.environ.get("ALPACA_PAPER","true").lower()=="true"), timeout=float(os.environ.get("ALPACA_TIMEOUT","6.0")))
        self.stock_universe = load_universe(); self.crypto_universe = self.settings.get("crypto",{}).get("universe", ["BTC/USD","ETH/USD"])
        self._news_counts: Dict[str,int] = {}; self._candidates: List[Dict[str,Any]] = []
        set_kv("universe", self.stock_universe); add_event("INFO", f"init universe={len(self.stock_universe)} crypto={self.crypto_universe}")
    def self_test(self) -> None:
        snaps = self.broker.stock_snapshots(self.stock_universe[:3]); set_health("marketdata_stock", bool(snaps), f"count={len(snaps)}")
        csnaps = {}; 
        if self.settings.get("crypto",{}).get("enabled"): csnaps = self.broker.crypto_snapshots(self.crypto_universe[:2])
        set_health("marketdata_crypto", bool(csnaps), f"count={len(csnaps)}")
        acct = self.broker.account(); set_health("account", bool(acct and acct.get('account_number')), "ok" if acct else "empty")
    def _schedule(self, fn, seconds: int, name: str):
        def loop():
            while True:
                try: fn()
                except Exception as e: set_health(name, False, str(e))
                time.sleep(seconds)
        t = threading.Thread(target=loop, daemon=True); t.start()
    def start_schedulers(self):
        news_s = int(self.settings.get("scheduling",{}).get("news_interval_s", 1200))
        self._schedule(self._refresh_news, news_s, "news_scheduler")
        self._schedule(self._refresh_earnings, 60*int(self.settings.get("scheduling",{}).get("earnings_refresh_min",60)), "earnings_scheduler")
        self._schedule(self._health_check, 60*int(self.settings.get("scheduling",{}).get("health_refresh_min",20)), "health_scheduler")
        self._schedule(self._refresh_candidates, 60*int(self.settings.get("scheduling",{}).get("candidate_refresh_min",20)), "candidates_scheduler")
    def _health_check(self):
        clock = self.broker.is_market_open() or {}; ok_clock = bool(clock); set_health("clock", ok_clock, f"is_open={clock.get('is_open') if clock else 'n/a'}")
        snaps = self.broker.stock_snapshots(self.stock_universe[:5]); set_health("stock_snapshots_smoke", bool(snaps), f"count={len(snaps)}")
        if self.settings.get("crypto",{}).get("enabled"):
            cs = self.broker.crypto_snapshots(self.crypto_universe[:2]); set_health("crypto_snapshots_smoke", bool(cs), f"count={len(cs)}")
        # positions snapshot into KV for UI
        try:
            pos = self.broker.positions(); set_kv("positions", pos)
        except Exception as e:
            add_event("WARN", f"positions fetch failed: {e}")
    def _refresh_earnings(self):
        data = refresh_earnings_calendar(7); set_health("earnings_calendar", bool(data), f"ok={bool(data)}")
    def _refresh_news(self):
        cfg = self.settings.get("news", {}) or {}
        counts = get_news_counts(self.stock_universe, cfg.get("window_hours",6), cfg.get("provider_order",["alpaca","finnhub","newsapi"]), rotate_batch=int(cfg.get("rotate_batch",60)))
        self._news_counts = counts or {}; set_kv("news_counts", self._news_counts); total = sum(self._news_counts.values()); set_health("news_scheduler", total>0, f"total_hits={total}")
    def _refresh_candidates(self):
        max_syms = int(self.settings.get("scheduling",{}).get("candidate_max_symbols",150)); base = self.stock_universe[:max_syms]
        snaps = self.broker.stock_snapshots(base)
        if not snaps: add_event("WARN", "no snapshots for candidates"); self._candidates = []; return
        out = []
        for sym, snap in snaps.items():
            m = score_momentum(snap); mr = score_mean_reversion(snap); nh = self._news_counts.get(sym.upper(), 0); n = score_news(nh)
            score = combine_scores({"momentum":m, "mean_reversion":mr, "news":n}, self.settings.get("weights",{}))
            out.append({"symbol":sym, "score":round(score,3), "news":nh, "last": (snap.get("latestTrade",{}) or {}).get("p")})
        out.sort(key=lambda x: x["score"], reverse=True); self._candidates = out[:50]; set_kv("candidates", self._candidates)
    def run(self):
        self.self_test(); self.start_schedulers()
        while True:
            clock = self.broker.is_market_open() or {}
            if not clock or not clock.get("is_open"): add_event("INFO", "market closed"); time.sleep(30); continue
            if not self._candidates: time.sleep(5); continue
            top = self._candidates[0]; th = self.settings.get("thresholds",{})
            if float(top["score"]) >= float(th.get("enter",0.62)):
                o = self.broker.submit_order(top["symbol"], 1, "buy")
                if o: record_trade(top["symbol"], "buy", 1, float(top.get("last") or 0), "auto"); add_event("INFO", f"BUY {top['symbol']} score={top['score']} last={top.get('last')}")
            time.sleep(5)
