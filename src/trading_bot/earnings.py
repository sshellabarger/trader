
import os, time, logging, requests
from .state import set_kv, get_kv
log = logging.getLogger("earnings")
def refresh_earnings_calendar(days_ahead: int=7) -> dict:
    token = os.environ.get("FINNHUB_API_KEY")
    if not token: set_kv("earnings_calendar", {}); return {}
    now = time.time(); start = time.strftime("%Y-%m-%d", time.gmtime(now)); end = time.strftime("%Y-%m-%d", time.gmtime(now + days_ahead*86400))
    url = "https://finnhub.io/api/v1/calendar/earnings"
    try:
        r = requests.get(url, params={"from": start, "to": end, "token": token}, timeout=6); r.raise_for_status()
        data = r.json() or {}; set_kv("earnings_calendar", data); return data
    except Exception as e:
        log.warning("earnings calendar error: %s", e); return get_kv("earnings_calendar", {})
