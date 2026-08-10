"""
Kalshi weather fair-value model (phase 0 — measurement only, no orders).

Fair values for KXHIGH* daily-high markets from the GEFS (31-member) and
ECMWF (51-member) ensembles via Open-Meteo, evaluated at each series'
official NWS settlement station (verified against live market rules
2026-08-10: Chicago Midway, NYC Central Park, Denver, Austin Bergstrom).

Method: per ensemble member, the max of hourly temperature_2m over the
station's LOCAL calendar day is that member's daily high. Apply the
per-station correction (additive bias, multiplicative spread about the pooled
mean), dress each member with a Gaussian kernel and average CDFs, weighting
the two model pools equally by default so ECMWF's 51 members don't outvote
GEFS's 31. NWS highs settle on whole degrees, so a "between 84 and 85"
bucket is P(83.5 <= X < 85.5) and "88 or above" (floor_strike 87, type
greater) is P(X > 87.5).

Strike direction gotcha: LOW-tail and HIGH-tail markets both use T tickers
(KXHIGHCHI-26AUG11-T80 is "79 or below", -T87 is "88 or above"). Live code
must read strike_type/floor_strike/cap_strike from the API; backtests over
recorded snapshots (which don't carry strike fields) infer direction via
specs_from_tickers(): min-strike T = low tail, max-strike T = high tail.

Honesty notes:
- Correction knobs default to identity (bias 0, spread 1). Fit them ONLY from
  accumulated settled days, never to make today's edges look bigger.
- The bar is the recorded market Brier (~0.09 pooled at 15-18Z, first
  measured 2026-08-10). A model that can't beat that number on a growing
  sample does not graduate, per the Oct 1 gate.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ..config import _env_float
from .config import taker_fee_cents

logger = logging.getLogger(__name__)

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


@dataclass(frozen=True)
class Station:
    name: str
    latitude: float
    longitude: float
    timezone: str


# Coordinates are the settlement stations themselves, not city centers.
STATIONS: Dict[str, Station] = {
    "KXHIGHCHI": Station("Chicago Midway (KMDW)", 41.786, -87.752, "America/Chicago"),
    "KXHIGHNY": Station("NYC Central Park (KNYC)", 40.779, -73.969, "America/New_York"),
    "KXHIGHDEN": Station("Denver Intl (KDEN)", 39.847, -104.656, "America/Denver"),
    "KXHIGHAUS": Station("Austin Bergstrom (KAUS)", 30.183, -97.680, "America/Chicago"),
}


@dataclass
class WeatherConfig:
    """Env-tunable knobs. Defaults are deliberately identity/neutral."""
    kernel_sigma: float = 1.5      # Gaussian dressing per member, deg F
    ecmwf_weight: float = 0.5      # pool weight; GEFS gets 1 - this
    bias: Dict[str, float] = None        # per-series additive deg F
    spread: Dict[str, float] = None      # per-series multiplicative about mean

    def __post_init__(self):
        self.kernel_sigma = _env_float("KALSHI_WX_KERNEL", self.kernel_sigma)
        self.ecmwf_weight = _env_float("KALSHI_WX_ECMWF_WEIGHT", self.ecmwf_weight)
        self.bias = dict(self.bias or {})
        self.spread = dict(self.spread or {})
        for series in STATIONS:
            self.bias.setdefault(series, _env_float(f"KALSHI_WX_BIAS_{series}", 0.0))
            self.spread.setdefault(series, _env_float(f"KALSHI_WX_SPREAD_{series}", 1.0))


def parse_event_date(event_ticker: str) -> Optional[str]:
    """KXHIGHCHI-26AUG11 -> '2026-08-11' (None if the suffix isn't a date)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not m or m.group(2) not in _MON:
        return None
    return f"20{m.group(1)}-{_MON[m.group(2)]:02d}-{int(m.group(3)):02d}"


def _default_fetch_json(url: str, params: dict, timeout: int = 30):
    import requests
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


_MODEL_POOLS = (("gfs", "gfs_seamless"), ("ecmwf", "ecmwf_ifs025"))


def fetch_ensemble_daymax(
    series: str,
    date_iso: str,
    fetch_json: Optional[Callable] = None,
) -> Dict[str, List[float]]:
    """Per-member daily-high pools {'gfs': [...], 'ecmwf': [...]} for the
    station's LOCAL calendar day. One request PER MODEL: single-model
    responses drop the model suffix from member keys (so a combined call is
    the only place suffixes are reliable), and per-model calls keep one
    model's archive gap from blanking the other. A member missing more than
    a quarter of the day's hours is dropped rather than guessed.

    Archive reality (measured 2026-08-10): the ensemble endpoint keeps member
    VALUES only ~4-5 days back — older dates return key skeletons full of
    nulls. Backtests deeper than that need the daily archiver cache."""
    station = STATIONS[series]
    fetch = fetch_json or _default_fetch_json
    pools: Dict[str, List[float]] = {"gfs": [], "ecmwf": []}
    for pool_name, model in _MODEL_POOLS:
        try:
            data = fetch(ENSEMBLE_URL, {
                "latitude": station.latitude, "longitude": station.longitude,
                "hourly": "temperature_2m", "models": model,
                "start_date": date_iso, "end_date": date_iso,
                "temperature_unit": "fahrenheit", "timezone": station.timezone,
            })
        except Exception as exc:  # noqa: BLE001 - one model down != no answer
            logger.warning(f"weather: {series} {date_iso} {model} fetch failed: {exc}")
            continue
        if isinstance(data, list):
            data = data[0] if data else {}
        hourly = (data or {}).get("hourly") or {}
        for key, values in hourly.items():
            if not key.startswith("temperature_2m") or not isinstance(values, list):
                continue
            good = [v for v in values if isinstance(v, (int, float))]
            if len(good) >= 18:
                pools[pool_name].append(max(good))
    return pools


def corrected_pools(pools: Dict[str, List[float]], bias: float,
                    spread: float) -> Dict[str, List[float]]:
    """mu + (x - mu) * spread + bias, centered on the all-member mean."""
    members = [x for pool in pools.values() for x in pool]
    if not members or (bias == 0.0 and spread == 1.0):
        return pools
    mu = sum(members) / len(members)
    return {name: [mu + (x - mu) * spread + bias for x in pool]
            for name, pool in pools.items()}


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def prob_le(x: float, pools: Dict[str, List[float]], cfg: WeatherConfig) -> float:
    """P(daily high <= x): kernel-dressed CDF, model pools weighted equally
    (by pool, not by member count) unless a pool is empty."""
    weights = {"ecmwf": cfg.ecmwf_weight, "gfs": 1.0 - cfg.ecmwf_weight}
    num, den = 0.0, 0.0
    for name, pool in pools.items():
        if not pool:
            continue
        w = weights.get(name, 0.5)
        cdf = sum(_phi((x - m) / cfg.kernel_sigma) for m in pool) / len(pool)
        num += w * cdf
        den += w
    return num / den if den else 0.5


@dataclass(frozen=True)
class MarketSpec:
    ticker: str
    kind: str                    # 'ge' | 'le' | 'between'
    floor: Optional[float] = None  # ge: yes iff high >= floor+1 (integer F)
    cap: Optional[float] = None    # le: yes iff high <= cap-1


def spec_from_api_market(m: dict) -> Optional[MarketSpec]:
    """Build a spec from a live market payload (has strike fields)."""
    t = m.get("ticker", "")
    st = m.get("strike_type")
    if st == "greater" and m.get("floor_strike") is not None:
        return MarketSpec(t, "ge", floor=float(m["floor_strike"]))
    if st == "less" and m.get("cap_strike") is not None:
        return MarketSpec(t, "le", cap=float(m["cap_strike"]))
    if st == "between" and m.get("floor_strike") is not None \
            and m.get("cap_strike") is not None:
        return MarketSpec(t, "between", floor=float(m["floor_strike"]),
                          cap=float(m["cap_strike"]))
    logger.warning(f"weather: unrecognized strike fields on {t}")
    return None


def specs_from_tickers(tickers: List[str]) -> Tuple[Dict[str, MarketSpec], List[str]]:
    """Infer specs from tickers alone (recorded snapshots carry no strike
    fields). B{m} is the 2-degree bucket centered on m. Of the T strikes in
    an event, the smallest is the low tail ('<= K-1') and the largest the
    high tail ('>= K+1') — verified against live strike_type fields."""
    t_strikes: List[Tuple[float, str]] = []
    specs: Dict[str, MarketSpec] = {}
    skipped: List[str] = []
    b_floors: List[float] = []
    for t in tickers:
        leaf = t.rsplit("-", 1)[-1]
        try:
            val = float(leaf[1:])
        except (ValueError, IndexError):
            skipped.append(t)
            continue
        if leaf.startswith("B"):
            floor, cap = val - 0.5, val + 0.5
            specs[t] = MarketSpec(t, "between", floor=floor, cap=cap)
            b_floors.append(floor)
        elif leaf.startswith("T"):
            t_strikes.append((val, t))
        else:
            skipped.append(t)
    if len(t_strikes) >= 2:
        t_strikes.sort()
        lo_val, lo_t = t_strikes[0]
        hi_val, hi_t = t_strikes[-1]
        specs[lo_t] = MarketSpec(lo_t, "le", cap=lo_val)
        specs[hi_t] = MarketSpec(hi_t, "ge", floor=hi_val)
        for val, t in t_strikes[1:-1]:      # ambiguous middle T: shouldn't exist
            skipped.append(t)
    elif len(t_strikes) == 1:
        val, t = t_strikes[0]
        if b_floors and val <= min(b_floors):
            specs[t] = MarketSpec(t, "le", cap=val)
        elif b_floors:
            specs[t] = MarketSpec(t, "ge", floor=val)
        else:
            skipped.append(t)               # single T, no context: refuse
    return specs, skipped


def fair_value_cents(spec: MarketSpec, pools: Dict[str, List[float]],
                     cfg: WeatherConfig) -> float:
    """Model probability in cents. Integer-degree settlement means the
    half-degree edges below."""
    if spec.kind == "ge":
        p = 1.0 - prob_le(spec.floor + 0.5, pools, cfg)
    elif spec.kind == "le":
        p = prob_le(spec.cap - 0.5, pools, cfg)
    else:
        p = prob_le(spec.cap + 0.5, pools, cfg) - prob_le(spec.floor - 0.5, pools, cfg)
    return max(0.0, min(100.0, 100.0 * p))


def evaluate(spec: MarketSpec, fair: float, yes_bid: int, yes_ask: int) -> dict:
    """Net edge per the phase-0 paper rule:
    net_edge = |fair - mid| - taker_fee(mid) - half_spread (all cents)."""
    mid = (yes_bid + yes_ask) / 2.0
    half = (yes_ask - yes_bid) / 2.0
    fee = taker_fee_cents(int(round(mid)), 1)
    edge = abs(fair - mid) - fee - half
    return {"ticker": spec.ticker, "fair": round(fair, 1), "bid": yes_bid,
            "ask": yes_ask, "mid": mid, "fee": fee,
            "net_edge": round(edge, 2), "side": "yes" if fair > mid else "no"}
