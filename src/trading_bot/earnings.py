from __future__ import annotations
import os, time, logging, requests
from typing import Dict, List

log = logging.getLogger("earnings")

def fetch_earnings_calendar(days_back: int=1, days_fwd: int=3) -> Dict[str, str]:
    """Return {SYMBOL: 'YYYY-MM-DD'} using Finnhub earnings calendar.
    Uses FINNHUB_API_KEY env var. If missing or error -> {}.
    """
    token = os.environ.get("FINNHUB_API_KEY")
    if not token:
        log.debug("FINNHUB_API_KEY not set; skipping earnings fetch.")
        return {}
    try:
        t = time.time()
        start = time.strftime("%Y-%m-%d", time.gmtime(t - days_back*86400))
        end   = time.strftime("%Y-%m-%d", time.gmtime(t + days_fwd*86400))
        url = "https://finnhub.io/api/v1/calendar/earnings"
        params = {"from": start, "to": end, "token": token}
        r = requests.get(url, params=params, timeout=6)
        if r.status_code != 200:
            log.warning("Finnhub %s %s", r.status_code, r.text[:160])
            return {}
        data = r.json() or {}
        out: Dict[str,str] = {}
        for item in (data.get("earningsCalendar") or []):
            sym = (item.get("symbol") or "").upper()
            date = item.get("date") or ""
            if sym and date:
                out[sym] = date
        return out
    except Exception as e:
        log.warning("Finnhub error: %s", e)
        return {}
