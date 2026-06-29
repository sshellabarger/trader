"""
Universe screen — build the stock sleeve's candidate pool from a liquidity and
volatility filter, so the pool stays current instead of a frozen hand-list.

For each symbol we pull recent DAILY bars and compute:
  * average dollar volume (mean of close*volume) over `window` days — the
    liquidity floor, so fills are reliable;
  * ATR% (simple ATR over `atr_period`, as a percent of the last close) — the
    volatility floor, so the name moves enough intraday to be worth trading.

Names that clear both floors and a price band are ranked by ATR% (most movement
first, since liquidity is already guaranteed) and the top `size` become the
pool, written to a JSON file the sleeve reads via STOCK_SLEEVE_POOL_FILE.

The pure functions (avg_dollar_volume / atr_pct / score_symbol / screen) are
network-free and unit-tested; build_pool is the thin live wrapper that fetches
bars through the broker. Intended to run WEEKLY (cron / systemd timer /
scheduled task) on the droplet; it does not run inside the trading loop.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class ScreenCriteria:
    min_price: float = 5.0
    max_price: float = 1000.0
    min_dollar_volume: float = 20_000_000.0   # 20-day avg $ volume (liquidity floor)
    min_atr_pct: float = 2.5                   # ATR as a % of price (volatility floor)
    window: int = 20                           # days for average dollar volume
    atr_period: int = 14
    size: int = 60                             # pool size (top-N by ATR%)


@dataclass
class SymbolScore:
    symbol: str
    price: float
    dollar_volume: float
    atr_pct: float


def avg_dollar_volume(bars: Sequence[Dict], window: int = 20) -> Optional[float]:
    """Mean of close*volume over the last `window` daily bars (None if empty)."""
    if not bars:
        return None
    vals = []
    for b in bars[-window:]:
        try:
            vals.append(float(b["c"]) * float(b["v"]))
        except (KeyError, TypeError, ValueError):
            continue
    return sum(vals) / len(vals) if vals else None


def atr_pct(bars: Sequence[Dict], period: int = 14) -> Optional[float]:
    """Simple ATR over `period` days as a percent of the last close.

    True range_i = max(high-low, |high-prev_close|, |low-prev_close|). Needs
    period+1 bars (the first TR needs a prior close). Returns None when there is
    not enough history, so thin / just-listed names are excluded conservatively.
    """
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        try:
            h = float(bars[i]["h"]); l = float(bars[i]["l"]); pc = float(bars[i - 1]["c"])
        except (KeyError, TypeError, ValueError):
            return None
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-period:]) / period
    try:
        last_close = float(bars[-1]["c"])
    except (KeyError, TypeError, ValueError):
        return None
    if last_close <= 0:
        return None
    return atr / last_close * 100.0


def score_symbol(symbol: str, bars: Sequence[Dict],
                 window: int = 20, atr_period: int = 14) -> Optional[SymbolScore]:
    """Pure scoring of one symbol from its daily bars. None if not scoreable."""
    if not bars:
        return None
    adv = avg_dollar_volume(bars, window)
    a = atr_pct(bars, atr_period)
    if adv is None or a is None:
        return None
    try:
        price = float(bars[-1]["c"])
    except (KeyError, TypeError, ValueError):
        return None
    return SymbolScore(symbol=symbol.upper(), price=price, dollar_volume=adv, atr_pct=a)


def screen(scores: Sequence[SymbolScore], criteria: ScreenCriteria) -> List[SymbolScore]:
    """Apply the price/liquidity/volatility floors, rank by ATR% (movement —
    liquidity is already guaranteed by the floor), and keep the top `size`."""
    passed = [
        s for s in scores
        if criteria.min_price <= s.price <= criteria.max_price
        and s.dollar_volume >= criteria.min_dollar_volume
        and s.atr_pct >= criteria.min_atr_pct
    ]
    passed.sort(key=lambda s: (s.atr_pct, s.dollar_volume), reverse=True)
    return passed[: criteria.size]


def prefilter_seed(broker, seed: Sequence[str], criteria: ScreenCriteria,
                   day_dollar_volume_floor: float = 0.0) -> List[str]:
    """Cheap snapshot pass to drop obviously-ineligible names before the
    per-symbol daily-bar fetch (price band + a lenient 1-day $-volume floor).
    Fail-open: returns the seed unchanged on any error or empty result."""
    floor = day_dollar_volume_floor or (criteria.min_dollar_volume * 0.3)
    try:
        snaps = broker.get_snapshots(list(seed))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"prefilter snapshots failed: {exc}; using full seed")
        return list(seed)
    if not snaps:
        return list(seed)
    survivors: List[str] = []
    for sym, snap in snaps.items():
        snap = snap or {}
        daily = snap.get("dailyBar") or {}
        trade = snap.get("latestTrade") or {}
        price = float(trade.get("p", 0) or daily.get("c", 0) or 0)
        vol = float(daily.get("v", 0) or 0)
        if price < criteria.min_price or price > criteria.max_price:
            continue
        if price * vol < floor:
            continue
        survivors.append(sym)
    return survivors or list(seed)


def build_pool(broker, seed: Sequence[str], criteria: ScreenCriteria,
               prefilter: bool = True, lookback_days: int = 90,
               sleep_between: float = 0.0) -> List[SymbolScore]:
    """Fetch recent daily bars for the seed (snapshot-prefiltered when large),
    score, and screen. Returns the ranked pool. Network-bound; the math lives in
    the pure helpers above."""
    seed = list(dict.fromkeys(s.upper() for s in seed))   # dedupe, keep order
    if prefilter:
        seed = prefilter_seed(broker, seed, criteria)
        logger.info(f"prefilter -> {len(seed)} candidates")
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    limit = max(criteria.window, criteria.atr_period + 1) + 60
    scores: List[SymbolScore] = []
    for sym in seed:
        try:
            bars = broker.get_bars(sym, timeframe="1Day", start=start, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{sym}: daily bars fetch failed: {exc}")
            continue
        sc = score_symbol(sym, bars or [], criteria.window, criteria.atr_period)
        if sc is not None:
            scores.append(sc)
        if sleep_between:
            time.sleep(sleep_between)
    pool = screen(scores, criteria)
    logger.info(f"scored {len(scores)} -> {len(pool)} in pool")
    return pool


def write_pool(path: str, pool: Sequence[SymbolScore], criteria: ScreenCriteria) -> None:
    """Write the pool as JSON: a flat symbol list plus metadata and the per-name
    scores, so a human can see why each name is in."""
    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "criteria": asdict(criteria),
        "symbols": [s.symbol for s in pool],
        "scores": [asdict(s) for s in pool],
    }
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)


def load_pool_symbols(path: str) -> List[str]:
    """Read symbols from a pool file written by write_pool (JSON object with a
    "symbols" list), a plain JSON list, or a CSV/text fallback. [] if unreadable
    or missing, so the caller can fall back to the static universe."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            text = fh.read().strip()
    except OSError as exc:
        logger.warning(f"pool file unreadable ({path}): {exc}")
        return []
    if text[:1] in ("{", "["):
        try:
            data = json.loads(text)
            syms = data.get("symbols", []) if isinstance(data, dict) else data
            return [str(s).strip().upper() for s in syms if str(s).strip()]
        except (ValueError, AttributeError) as exc:
            logger.warning(f"pool file parse failed ({path}): {exc}")
            return []
    try:
        from .universe import load_universe_csv
        return load_universe_csv(path)
    except Exception:  # noqa: BLE001
        return []
