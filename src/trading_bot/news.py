# src/trading_bot/news.py
from __future__ import annotations
import os, time, logging, requests
from typing import Dict, List

from .state import get_kv, set_kv

log = logging.getLogger("news")

# ---------- small utils ----------
def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def _ymd(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))

def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def _cooldown_active(key: str) -> bool:
    cd = get_kv(key)
    return isinstance(cd, dict) and float(cd.get("until", 0)) > time.time()

def _set_cooldown(key: str, minutes: int, why: str):
    until = time.time() + minutes*60
    set_kv(key, {"until": until, "why": why})
    log.warning("%s cooldown %d min: %s", key, minutes, why)

# ---------- Alpaca News ----------
def fetch_alpaca_news_counts(symbols: List[str], window_hours: int=6) -> Dict[str,int]:
    key = os.environ.get("ALPACA_API_KEY_ID"); sec = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        return {}
    base = "https://data.alpaca.markets/v1beta1/news"
    headers = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec, "User-Agent": "trading-bot/2.0"}
    end = time.time(); start = end - window_hours*3600
    out: Dict[str,int] = {}

    # query in chunks
    for group in _chunk([s.upper() for s in symbols[:200]], 50):
        params = {
            "symbols": ",".join(group),
            "start": _utc_iso(start),
            "end": _utc_iso(end),
            "limit": 50,
        }
        try:
            r = requests.get(base, headers=headers, params=params, timeout=6)
            if r.status_code == 429:
                log.warning("Alpaca News 429; backing off this cycle")
                break
            r.raise_for_status()
            data = r.json() or {}
            items = data.get("news") or data.get("news_list") or []
            # count by symbol appearance
            for sym in group:
                cnt = 0
                for it in items:
                    syms = it.get("symbols") or it.get("symbols_list") or []
                    if sym in syms:
                        cnt += 1
                out[sym] = out.get(sym, 0) + cnt
        except Exception as e:
            log.warning("Alpaca News error: %s", e)
    return out

# ---------- Finnhub company news ----------
def fetch_finnhub_counts(symbols: List[str], window_hours: int=6) -> Dict[str,int]:
    token = os.environ.get("FINNHUB_API_KEY")
    if not token:
        return {}
    end = time.time(); start = end - window_hours*3600
    start_d, end_d = _ymd(start), _ymd(end)
    out: Dict[str,int] = {}
    for s in [x.upper() for x in symbols[:80]]:
        try:
            url = "https://finnhub.io/api/v1/company-news"
            params = {"symbol": s, "from": start_d, "to": end_d, "token": token}
            r = requests.get(url, params=params, timeout=6)
            if r.status_code == 429:
                log.warning("Finnhub 429; backing off this cycle")
                break
            r.raise_for_status()
            arr = r.json() or []
            out[s] = len(arr)
        except Exception as e:
            log.warning("Finnhub error for %s: %s", s, e)
    return out

# ---------- NewsAPI (as last resort) ----------
def fetch_newsapi_counts(symbols: List[str], window_hours: int=6, batch_size: int=20,
                         cooldown_min: int=120) -> Dict[str,int]:
    api_key = os.environ.get("NEWSAPI_KEY")
    if not api_key or _cooldown_active("newsapi_cooldown"):
        return {}
    end = time.time(); start = end - window_hours*3600
    headers = {"User-Agent": "trading-bot/2.0"}
    out: Dict[str,int] = {}
    for s in [x.upper() for x in symbols[:100]]:
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": s, "language": "en", "sortBy": "publishedAt", "pageSize": 20,
                "from": _utc_iso(start), "to": _utc_iso(end), "apiKey": api_key,
            }
            r = requests.get(url, params=params, headers=headers, timeout=6)
            if r.status_code == 429:
                log.warning("NewsAPI 429 rateLimited; enabling cooldown for %d min", cooldown_min)
                _set_cooldown("newsapi_cooldown", cooldown_min, "rateLimited")
                return out
            r.raise_for_status()
            data = r.json() or {}
            out[s] = int(data.get("totalResults") or 0)
        except Exception as e:
            log.warning("NewsAPI error for %s: %s", s, e)
    return out

# ---------- Orchestrator the engine should call ----------
def get_news_counts(symbols: List[str], window_hours: int, provider_order: List[str],
                    rotate_key: str="news_rotate_idx", rotate_batch: int=60) -> Dict[str,int]:
    """Try providers in order; rotate through the universe to avoid hammering APIs."""
    symbols = [s.upper() for s in (symbols or [])]
    if not symbols:
        return {}
    # rotation window to spread calls across loops
    idx = int(get_kv(rotate_key, 0) or 0)
    start = (idx * rotate_batch) % max(len(symbols), 1)
    sub = symbols[start:start+rotate_batch] or symbols[:rotate_batch]
    set_kv(rotate_key, idx + 1)

    counts: Dict[str,int] = {}
    for prov in provider_order:
        if prov == "alpaca":
            counts = fetch_alpaca_news_counts(sub, window_hours)
        elif prov == "finnhub":
            counts = fetch_finnhub_counts(sub, window_hours)
        elif prov == "newsapi":
            counts = fetch_newsapi_counts(sub, window_hours)
        else:
            continue
        if counts:
            return counts
    return counts  # may be {}