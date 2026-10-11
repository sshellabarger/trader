"""Daily read-only review of the IRA: where it is versus the model, how each
holding is trending, what the news says, and what the advisor WOULD trade.

PROPOSE ONLY. This module has no path to an order. Scott reads the report
and, if he agrees, runs `python -m trader.advisor.cli --live` on the Mac
and approves each trade by hand.

Positions come from, in order:
  1. Schwab (REST, read-only) when SCHWAB_API_KEY/SCHWAB_APP_SECRET and a
     token file are present. A successful pull is cached to
     data/ira_positions.json.
  2. A Schwab positions export (IRA_POSITIONS_CSV, default
     data/ira_positions.csv): the standard "Export" from the Positions
     page, dropped on the droplet as is. The no-API path: replace the file
     when things change; prices/trend/news still come from Alpaca.
  3. A hand-maintained holdings file (IRA_HOLDINGS_FILE, default
     data/ira_holdings.txt): `SYMBOL SHARES` per line, `CASH amount`.
  4. The Schwab cache, revalued at today's prices, when Schwab was used
     before and can't be reached now (usually the 7-day token lapsed).

Prices, trend and news come from Alpaca (free), since Schwab has no news
endpoint and the token may be stale.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..marketdata import NewsDigest, SymbolStats, fetch_news_digest, fetch_stats_many
from .engine import Trade, suggest_trades
from .model import default_targets, parse_targets

logger = logging.getLogger(__name__)

CACHE_PATH_DEFAULT = "data/ira_positions.json"
CSV_PATH_DEFAULT = "data/ira_positions.csv"
HOLDINGS_PATH_DEFAULT = "data/ira_holdings.txt"


@dataclass
class HoldingView:
    symbol: str
    qty: float
    value: float
    pct: float
    target_pct: float
    stats: SymbolStats
    news: NewsDigest
    action: str = ""


@dataclass
class IRAReview:
    as_of: str
    source: str                      # "schwab" | "cache (YYYY-MM-DD)" | "csv" | "none"
    total: float = 0.0
    cash: float = 0.0
    account: str = ""
    band: float = 0.25
    holdings: List[HoldingView] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def flagged(self) -> List[HoldingView]:
        return [h for h in self.holdings
                if h.news.flag == "negative" or h.stats.above_trend is False]


# ---------------------------------------------------------------------------
# Position sources
# ---------------------------------------------------------------------------

def _save_cache(path: str, snap: Dict) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    snap = dict(snap)
    snap["fetched_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(snap, fh, indent=2)
    os.replace(tmp, path)


def _load_cache(path: str) -> Optional[Dict]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("ira: unreadable cache %s: %s", path, exc)
        return None


def _export_date(path: str) -> str:
    """Schwab's first line reads: Positions for account ... as of 12:56 PM ET,
    2026/10/11 — use that date; fall back to the file's mtime."""
    try:
        with open(path) as fh:
            first = fh.readline()
        m = re.search(r"(\d{4})/(\d{2})/(\d{2})", first)
        if m:
            return "-".join(m.groups())
    except OSError:
        pass
    return datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat()


def load_positions(cache_path: str = CACHE_PATH_DEFAULT,
                   csv_path: Optional[str] = None,
                   account_index: int = 0,
                   holdings_path: Optional[str] = None):
    """-> (snapshot dict | None, source label, warnings). snapshot has
    positions {sym: {qty, value, price}}, cash, total, account."""
    warnings: List[str] = []
    key = os.getenv("SCHWAB_API_KEY", "")
    secret = os.getenv("SCHWAB_APP_SECRET", "")
    if key and secret:
        from .schwab_rest import SchwabRest, TokenExpired, default_token_path
        token_path = default_token_path()
        if os.path.exists(token_path):
            try:
                client = SchwabRest(key, secret, token_path)
                snap = client.positions(account_index)
                age = client.refresh_token_age_days()
                if age >= 5.5:
                    warnings.append(
                        f"Schwab refresh token is {age:.1f} days old; it dies at 7. "
                        f"Re-auth on the Mac and re-copy before then.")
                try:
                    _save_cache(cache_path, snap)
                except OSError as exc:
                    warnings.append(f"could not write {cache_path}: {exc}")
                return snap, "schwab", warnings
            except TokenExpired as exc:
                warnings.append(f"Schwab: {exc}")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Schwab unreachable: {exc}")
        else:
            warnings.append(f"Schwab token file not found at {token_path}")
    csv_path = csv_path or os.getenv("IRA_POSITIONS_CSV", "") or CSV_PATH_DEFAULT
    if csv_path and os.path.exists(csv_path):
        from .model import load_positions_csv
        try:
            pos, cash = load_positions_csv(csv_path)
            total = sum(p["value"] for p in pos.values()) + cash
            exported = _export_date(csv_path)
            return ({"positions": pos, "cash": cash, "total": total, "account": ""},
                    f"Schwab export dated {exported}", warnings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not read {csv_path}: {exc}")
    holdings_path = holdings_path or os.getenv("IRA_HOLDINGS_FILE", "") or HOLDINGS_PATH_DEFAULT
    if holdings_path and os.path.exists(holdings_path):
        from .model import load_holdings_txt
        try:
            pos, cash = load_holdings_txt(holdings_path)
            when = datetime.fromtimestamp(os.path.getmtime(holdings_path)).date().isoformat()
            return ({"positions": pos, "cash": cash, "total": 0.0, "account": ""},
                    f"manual file, updated {when}", warnings)
        except ValueError as exc:
            warnings.append(str(exc))
    cached = _load_cache(cache_path)
    if cached:
        when = str(cached.get("fetched_at", ""))[:10] or "unknown date"
        return cached, f"cache ({when})", warnings
    warnings.append("no IRA position source available (Schwab, holdings file, CSV or cache)")
    return None, "none", warnings


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

def build_review(broker, *, api_key: str, api_secret: str,
                 targets: Optional[Dict[str, float]] = None,
                 band: float = 0.25, min_dollars: float = 500.0,
                 cache_path: str = CACHE_PATH_DEFAULT,
                 csv_path: Optional[str] = None,
                 holdings_path: Optional[str] = None,
                 sma_n: int = 200, news_hours: int = 36,
                 with_news: bool = True) -> IRAReview:
    targets = targets or (parse_targets(os.getenv("IRA_TARGETS", "")) or default_targets())
    snap, source, warnings = load_positions(cache_path, csv_path, holdings_path=holdings_path)
    review = IRAReview(as_of=datetime.now(timezone.utc).isoformat(timespec="minutes"),
                       source=source, band=band, warnings=warnings)
    if not snap:
        return review

    positions: Dict[str, dict] = {s: dict(v) for s, v in snap.get("positions", {}).items()}
    cash = float(snap.get("cash", 0.0) or 0.0)
    symbols = sorted(set(positions) | set(targets))

    stats = fetch_stats_many(broker, symbols, sma_n)
    # Revalue at the latest completed close so a cached snapshot isn't stale
    # by its own prices, and so buy-only targets have a price.
    prices: Dict[str, float] = {}
    for s in symbols:
        close = stats[s].close
        if close:
            prices[s] = close
            if s in positions and positions[s].get("qty"):
                positions[s]["price"] = close
                positions[s]["value"] = positions[s]["qty"] * close
        elif s in positions:
            prices[s] = positions[s].get("price", 0.0)
            if prices[s] <= 0:
                warnings.append(f"{s}: no price available (not on Alpaca's feed?); "
                                f"valued at $0 in this report")
    total = sum(p.get("value", 0.0) for p in positions.values()) + cash
    review.total, review.cash = total, cash
    review.account = str(snap.get("account", ""))

    news = fetch_news_digest(api_key, api_secret, sorted(positions), hours=news_hours) \
        if with_news else {s: NewsDigest(s) for s in positions}

    trades = suggest_trades(positions, cash, targets, prices, band=band, min_dollars=min_dollars)
    review.trades = trades
    by_sym = {t.symbol: t for t in trades}

    for s in sorted(positions, key=lambda k: -positions[k].get("value", 0.0)):
        p = positions[s]
        t = by_sym.get(s)
        review.holdings.append(HoldingView(
            symbol=s, qty=p.get("qty", 0.0), value=p.get("value", 0.0),
            pct=(p.get("value", 0.0) / total * 100) if total else 0.0,
            target_pct=targets.get(s, 0.0) * 100,
            stats=stats[s], news=news.get(s, NewsDigest(s)),
            action=(f"{t.side} {t.quantity}" if t else ""),
        ))
    return review


# ---------------------------------------------------------------------------
# Rendering (plain text; email + log)
# ---------------------------------------------------------------------------

def _fmt_pct(x: Optional[float]) -> str:
    return "   n/a" if x is None else f"{x:+6.1f}%"


def render_text(r: IRAReview, max_rows: int = 120, max_trades: int = 40) -> str:
    L: List[str] = []
    L.append(f"IRA REVIEW  (source: {r.source}; propose-only, nothing is placed)")
    if r.source == "none":
        L.extend("  ! " + w for w in r.warnings)
        return "\n".join(L)
    L.append(f"  total ${r.total:,.0f}   cash ${r.cash:,.0f}   holdings {len(r.holdings)}"
             f"   band ±{r.band:.0%}")
    if r.source.startswith("Schwab export"):
        L.append("  (positions from data/ira_positions.csv, revalued at the last close; "
                 "replace that file after you trade)")
    elif r.source.startswith("manual"):
        L.append("  (shares from data/ira_holdings.txt, valued at the last close; "
                 "edit that file when you trade)")
    for w in r.warnings:
        L.append(f"  ! {w}")

    flagged = r.flagged
    if flagged:
        # Positions of at least 0.5% (or with negative news) get a line each;
        # the long tail of sub-0.5% names below trend is counted, not listed.
        major = [h for h in flagged if h.pct >= 0.5 or h.news.flag == "negative"]
        minor = [h for h in flagged if h not in major]
        L.append("")
        L.append(f"  Needs a look ({len(flagged)} flagged):")
        for h in major:
            why = []
            if h.stats.above_trend is False:
                why.append(f"below 200dma ({h.stats.dist_to_sma_pct:+.1f}%)")
            if h.news.flag == "negative":
                why.append(f"negative news ({h.news.mean_sentiment:+.2f}, "
                           f"{len(h.news.articles)} items)")
            L.append(f"    {h.symbol:6s} {h.pct:5.1f}%  " + "; ".join(why))
            for a in h.news.articles[:2]:
                L.append(f"           - {a.headline[:95]}")
        if minor:
            L.append(f"    + {len(minor)} positions under 0.5% each below trend "
                     f"(~${sum(h.value for h in minor):,.0f} total): "
                     + ", ".join(h.symbol for h in minor[:30])
                     + (" ..." if len(minor) > 30 else ""))

    L.append("")
    L.append("  Holdings (largest first):")
    L.append("    sym      now   target   1d      1w      1m    trend   action")
    for h in r.holdings[:max_rows]:
        L.append(f"    {h.symbol:6s} {h.pct:5.1f}%  {h.target_pct:5.1f}%  "
                 f"{_fmt_pct(h.stats.ret_1d)} {_fmt_pct(h.stats.ret_1w)} {_fmt_pct(h.stats.ret_1m)}"
                 f"   {h.stats.trend_label:5s}   {h.action}")
    if len(r.holdings) > max_rows:
        L.append(f"    ... {len(r.holdings) - max_rows} more")

    L.append("")
    if r.trades:
        sells = [t for t in r.trades if t.side == "SELL"]
        buys = [t for t in r.trades if t.side == "BUY"]
        exits = [t for t in sells if t.reason.startswith("exit")]
        trims = [t for t in sells if not t.reason.startswith("exit")]
        L.append(f"  Proposed ({len(sells)} sells ~${sum(t.est_value for t in sells):,.0f}, "
                 f"{len(buys)} buys ~${sum(t.est_value for t in buys):,.0f}):")
        for t in trims + buys:
            L.append(f"    {t.side:4s} {t.quantity:>6d} {t.symbol:6s} ~${t.est_value:>10,.0f}  {t.reason}")
        if exits:
            L.append(f"    EXIT {len(exits)} names not in the model, ~${sum(t.est_value for t in exits):,.0f} total:")
            shown = exits[:max_trades]
            L.append("      " + ", ".join(f"{t.symbol} {t.quantity}" for t in shown))
            if len(exits) > max_trades:
                L.append(f"      ... {len(exits) - max_trades} more")
        L.append("  To act: on the Mac, `python -m trader.advisor.cli --live --band "
                 f"{r.band}` and approve each trade.")
    else:
        L.append("  Proposed: nothing (within bands).")
    return "\n".join(L)


def render_push(r: IRAReview) -> str:
    if r.source == "none":
        return "IRA: no position source"
    n_flag = len(r.flagged)
    return f"IRA ${r.total:,.0f} | {len(r.trades)} proposals | {n_flag} flagged | {r.source}"
