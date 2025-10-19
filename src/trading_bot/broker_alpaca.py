from __future__ import annotations

import os, time, logging, requests
from typing import Dict, List, Optional

log = logging.getLogger("alpaca")

class AlpacaBroker:
    def __init__(self, paper: Optional[bool]=None, feed: str="iex", timeout: float=6.0):
        self.key = os.environ.get("ALPACA_API_KEY_ID")
        self.secret = os.environ.get("ALPACA_API_SECRET_KEY")
        if not self.key or not self.secret:
            raise RuntimeError("Missing ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY")
        self.paper = str(os.environ.get("ALPACA_PAPER","true")).lower() in ("1","true","yes") if paper is None else paper
        self.base_trading = "https://paper-api.alpaca.markets" if self.paper else "https://api.alpaca.markets"
        self.base_data_stocks = "https://data.alpaca.markets/v2/stocks"
        self.base_data_crypto  = "https://data.alpaca.markets/v1beta3/crypto/us"
        self.timeout = timeout
        self.feed = feed

        self._session = requests.Session()
        self._session.headers.update({
            "Apca-Api-Key-Id": self.key,
            "Apca-Api-Secret-Key": self.secret,
            "Accept": "application/json",
            "User-Agent": "trading-bot/2.0"
        })

    def _get(self, url: str, **kw):
        t0 = time.time()
        r = self._session.get(url, timeout=self.timeout, **kw)
        elapsed = int((time.time()-t0)*1000)
        # Only log WARN/ERROR at INFO level; DEBUG will include successes
        if r.status_code >= 400:
            log.warning("HTTP %s %s %sms %s", r.status_code, url, elapsed, r.text[:200])
        else:
            log.debug("HTTP %s %s %sms", r.status_code, url, elapsed)
        r.raise_for_status()
        return r.json() if r.content else {}

    def _post(self, url: str, json: dict):
        t0 = time.time()
        r = self._session.post(url, json=json, timeout=self.timeout)
        elapsed = int((time.time()-t0)*1000)
        if r.status_code >= 400:
            log.warning("HTTP %s %s %sms %s", r.status_code, url, elapsed, r.text[:200])
        else:
            log.debug("HTTP %s %s %sms", r.status_code, url, elapsed)
        r.raise_for_status()
        return r.json() if r.content else {}

    @staticmethod
    def _to_pair(sym: str) -> str:
        s = (sym or "").upper()
        if "/" in s: return s
        if s.endswith("USD") and len(s) > 3:
            return f"{s[:-3]}/USD"
        return s

    # ----- account/trading -----
    def get_clock(self) -> dict:
        return self._get(f"{self.base_trading}/v2/clock")

    def get_account(self) -> dict:
        return self._get(f"{self.base_trading}/v2/account")

    def list_positions(self) -> List[dict]:
        return self._get(f"{self.base_trading}/v2/positions") or []

    def submit_order(self, symbol: str, side: str, qty: Optional[float]=None, notional: Optional[float]=None,
                     type: str="market", time_in_force: str="gtc") -> dict:
        payload = {"symbol": symbol, "side": side.lower(), "type": type, "time_in_force": time_in_force}
        if qty is not None: payload["qty"] = str(qty)
        if notional is not None: payload["notional"] = str(notional)
        return self._post(f"{self.base_trading}/v2/orders", json=payload)

    # ----- market data -----
    def snapshots_batch_stocks(self, symbols: List[str]) -> Dict[str, dict]:
        if not symbols: return {}
        syms = ",".join(symbols[:1000])
        url = f"{self.base_data_stocks}/snapshots?symbols={syms}&feed={self.feed}"
        data = self._get(url) or {}
        return data.get("snapshots") or data

    def snapshots_batch_crypto(self, symbols: List[str]) -> Dict[str, dict]:
        if not symbols: return {}
        pairs = ",".join(self._to_pair(s) for s in symbols)
        url = f"{self.base_data_crypto}/snapshots?symbols={pairs}"
        data = self._get(url) or {}
        snaps = data.get("snapshots") or {}
        out: Dict[str, dict] = {}
        for k, snap in snaps.items():
            norm = k.replace("/", "")
            out[norm] = snap
        return out

    @staticmethod
    def mid_from_snapshot(snap: dict):
        q = (snap or {}).get("latestQuote") or {}
        b = q.get("bp") or q.get("bidPrice")
        a = q.get("ap") or q.get("askPrice")
        if b and a:
            try:
                return (float(b) + float(a)) / 2.0
            except Exception:
                return None
        t = (snap or {}).get("latestTrade") or {}
        p = t.get("p") or t.get("price")
        try:
            return float(p) if p is not None else None
        except Exception:
            return None
