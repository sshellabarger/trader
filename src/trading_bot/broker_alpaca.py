
from __future__ import annotations
import os, time, logging, requests
from typing import Dict, List, Any
log = logging.getLogger("alpaca")
class AlpacaBroker:
    def __init__(self, feed: str="iex", paper: bool=True, timeout: float=6.0):
        self.base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.base_data = "https://data.alpaca.markets"; self.feed = feed; self.timeout = timeout
        self.key = os.environ.get("ALPACA_API_KEY_ID"); self.secret = os.environ.get("ALPACA_API_SECRET_KEY")
        self.session = requests.Session(); self.session.headers.update({"Apca-Api-Key-Id": self.key or "", "Apca-Api-Secret-Key": self.secret or "", "User-Agent": "trading-bot/2.0"})
    def _get_json(self, url: str, params: Dict[str, Any]|None=None) -> Any:
        t0 = time.time()
        try:
            r = self.session.get(url, params=params, timeout=self.timeout); ok = (200 <= r.status_code < 300)
            if r.status_code == 429: log.warning("HTTP 429: %s", url)
            r.raise_for_status(); data = r.json(); elapsed = int((time.time()-t0)*1000)
            log.debug("HTTP", extra={"service":"alpaca","event":"http","url":url,"status":r.status_code,"elapsed_ms":elapsed})
            return data
        except Exception as e:
            elapsed = int((time.time()-t0)*1000); log.warning("HTTP error %s (%sms) url=%s", e, elapsed, url); return None
    def account(self) -> dict: return self._get_json(f"{self.base}/v2/account") or {}
    def positions(self) -> List[dict]:
        arr = self._get_json(f"{self.base}/v2/positions") or []; 
        if isinstance(arr, dict): arr = arr.get("positions", []); 
        return arr or []
    def is_market_open(self) -> dict: return self._get_json(f"{self.base_data}/v2/clock") or {}
    def stock_snapshots(self, symbols: List[str]) -> Dict[str, Any]:
        if not symbols: return {}
        sym_str = ",".join(symbols[:200]); url = f"{self.base_data}/v2/stocks/snapshots"; params = {"symbols": sym_str, "feed": self.feed}
        data = self._get_json(url, params=params) or {}; snaps = data.get("snapshots") or data.get("data") or {}; return snaps or {}
    def crypto_snapshots(self, symbols: List[str]) -> Dict[str, Any]:
        if not symbols: return {}
        sym_str = ",".join(symbols[:200]); url = f"{self.base_data}/v1beta3/crypto/us/snapshots"; params = {"symbols": sym_str}
        data = self._get_json(url, params=params) or {}; snaps = data.get("snapshots") or data.get("data") or {}; return snaps or {}
    def submit_order(self, symbol: str, qty: float, side: str, market: bool=True):
        url = f"{self.base}/v2/orders"; payload = {"symbol": symbol, "qty": str(qty), "side": side.lower(), "type": "market", "time_in_force": "day"}
        t0 = time.time()
        try:
            r = self.session.post(url, json=payload, timeout=self.timeout); r.raise_for_status(); elapsed = int((time.time()-t0)*1000)
            log.info("order ok", extra={"symbol":symbol,"side":side,"qty":qty,"elapsed_ms":elapsed}); return r.json()
        except Exception as e:
            elapsed = int((time.time()-t0)*1000); log.error("order fail", extra={"symbol":symbol,"side":side,"qty":qty,"err":str(e),"elapsed_ms":elapsed}); return {}
