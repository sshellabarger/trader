"""
Daily heartbeat for the paper account: one message a human reads every
trading day, so eleven silent weeks can never happen again.

What it reports (all from live broker calls, so the message itself proves the
credentials work):
  * equity, day change, cash, buying power, and since-inception return
    versus buy-and-hold QQQ over the same window (the CLAUDE.md benchmark);
  * positions, split into the held core and the engine's intraday book;
  * today's fills (from the broker, not the journal, which rolls at midnight);
  * core status per name: target / effective / actual weight, trend, and
    what tomorrow's rebalance would do;
  * a news digest on everything held, negatives first;
  * liveness: when the engine last wrote its journal, and which notification
    channels are configured.

The IRA review (advisor/daily_review.py) is appended when enabled, so one
email covers both books.

A heartbeat is a report, not a gate: it never places or cancels anything.
"""
from __future__ import annotations

import glob
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .broker import AlpacaBroker
from .config import Config
from .core import CorePortfolio
from .marketdata import NewsDigest, fetch_news_digest, fetch_stats
from .notify import Notifier

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


@dataclass
class Heartbeat:
    date: str
    ok: bool = True                       # False = account unreachable
    equity: float = 0.0
    last_equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    inception: Optional[Dict] = None      # {start, days, acct_ret, qqq_ret}
    core_positions: List[Dict] = field(default_factory=list)
    intraday_positions: List[Dict] = field(default_factory=list)
    fills_today: List[Dict] = field(default_factory=list)
    core: Optional[Dict] = None           # CorePortfolio.snapshot()
    news: Dict[str, NewsDigest] = field(default_factory=dict)
    journal_last: str = ""                # date of the newest summary file
    journal_summary: Optional[Dict] = None
    warnings: List[str] = field(default_factory=list)
    channels: str = ""
    ira_text: str = ""
    ira_push: str = ""

    @property
    def day_change(self) -> float:
        return self.equity - self.last_equity if self.last_equity else 0.0

    @property
    def day_change_pct(self) -> float:
        return (self.day_change / self.last_equity * 100.0) if self.last_equity else 0.0


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(config: Config, broker: AlpacaBroker, *, notifier: Optional[Notifier] = None,
          with_news: bool = True, with_ira: Optional[bool] = None) -> Heartbeat:
    today = datetime.now(_ET).date().isoformat()
    hb = Heartbeat(date=today, channels=(notifier.config.describe() if notifier else ""))

    acct = broker.get_account()
    if not acct:
        hb.ok = False
        hb.warnings.append("ACCOUNT UNREACHABLE: Alpaca returned nothing for /v2/account "
                           "(bad keys, wrong base URL, or network). Nothing is trading.")
        _journal_liveness(hb, config)
        return hb
    hb.equity = _f(acct.get("equity"))
    hb.last_equity = _f(acct.get("last_equity"))
    hb.cash = _f(acct.get("cash"))
    hb.buying_power = _f(acct.get("buying_power"))
    if str(acct.get("status", "")).upper() != "ACTIVE":
        hb.warnings.append(f"account status is {acct.get('status')}")

    positions = broker.get_positions()
    core = CorePortfolio(config, broker, notifier)
    core_pos, intra_pos = core.split_positions(positions)
    hb.core_positions = [_pos_row(p) for p in core_pos]
    hb.intraday_positions = [_pos_row(p) for p in intra_pos]

    hb.fills_today = _fills_today(broker, today)
    hb.inception = _inception(broker)

    if config.core.enabled:
        try:
            hb.core = core.snapshot(positions, hb.equity, with_trend=True)
        except Exception as exc:  # noqa: BLE001
            hb.warnings.append(f"core snapshot failed: {exc}")

    held = sorted({p["symbol"] for p in hb.core_positions + hb.intraday_positions})
    if with_news and held:
        hb.news = fetch_news_digest(config.broker.api_key, config.broker.api_secret, held)

    _journal_liveness(hb, config)

    if with_ira is None:
        with_ira = _env_bool("IRA_REVIEW_ENABLED", False)
    if with_ira:
        try:
            from .advisor import daily_review as ira
            r = ira.build_review(broker, api_key=config.broker.api_key,
                                 api_secret=config.broker.api_secret,
                                 band=_env_float("IRA_BAND", 0.25),
                                 min_dollars=_env_float("IRA_MIN_TRADE_DOLLARS", 500.0),
                                 with_news=with_news)
            hb.ira_text = ira.render_text(r)
            hb.ira_push = ira.render_push(r)
        except Exception as exc:  # noqa: BLE001
            logger.error("heartbeat: IRA review failed: %s", exc, exc_info=True)
            hb.ira_text = f"IRA REVIEW failed: {exc}"
            hb.ira_push = "IRA review failed"
    return hb


def _pos_row(p: Dict) -> Dict:
    return {
        "symbol": str(p.get("symbol", "")),
        "qty": _f(p.get("qty")),
        "value": _f(p.get("market_value")),
        "upl": _f(p.get("unrealized_pl")),
        "upl_pct": _f(p.get("unrealized_plpc")) * 100.0,
        "price": _f(p.get("current_price")),
    }


def _fills_today(broker: AlpacaBroker, today: str) -> List[Dict]:
    try:
        orders = broker.get_orders(status="closed", limit=200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("heartbeat: orders fetch failed: %s", exc)
        return []
    out = []
    for o in orders:
        if str(o.get("status")) != "filled":
            continue
        ts = str(o.get("filled_at") or o.get("submitted_at") or "")
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_ET).date().isoformat()
        except ValueError:
            continue
        if d != today:
            continue
        out.append({
            "time": datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(_ET).strftime("%H:%M"),
            "symbol": o.get("symbol"), "side": str(o.get("side", "")).upper(),
            "qty": _f(o.get("filled_qty")), "price": _f(o.get("filled_avg_price")),
            "core": str(o.get("client_order_id", "")).startswith("core-"),
        })
    out.sort(key=lambda r: r["time"])
    return out


def _inception(broker: AlpacaBroker) -> Optional[Dict]:
    """Account return since the first equity point on record vs QQQ over the
    same window, from daily closes. Best-effort."""
    try:
        ph = broker.get_portfolio_history(period="1A", timeframe="1D")
        if not ph:
            return None
        pts = [(t, e) for t, e in zip(ph.get("timestamp", []), ph.get("equity", [])) if e]
        if len(pts) < 2:
            return None
        t0, e0 = pts[0]
        t1, e1 = pts[-1]
        d0 = datetime.fromtimestamp(t0, tz=timezone.utc).date()
        d1 = datetime.fromtimestamp(t1, tz=timezone.utc).date()
        bars = broker.get_bars("QQQ", timeframe="1Day", start=d0.isoformat(), limit=400)
        q0 = q1 = None
        for b in bars or []:
            d = str(b.get("t", ""))[:10]
            c = _f(b.get("c"))
            if c <= 0:
                continue
            if q0 is None and d >= d0.isoformat():
                q0 = c
            if d <= d1.isoformat():
                q1 = c
        return {
            "start": d0.isoformat(), "end": d1.isoformat(), "days": (d1 - d0).days,
            "acct_ret": (e1 / e0 - 1.0) * 100.0 if e0 else None,
            "qqq_ret": (q1 / q0 - 1.0) * 100.0 if (q0 and q1) else None,
            "start_equity": e0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("heartbeat: inception calc failed: %s", exc)
        return None


def _journal_liveness(hb: Heartbeat, config: Config) -> None:
    """The engine writes trade_logs/summary_<date>.json at every day roll.
    If the newest one is more than ~3 days old on a weekday, the engine
    container is probably not running."""
    log_dir = os.getenv("TRADE_LOG_DIR", "trade_logs")
    files = sorted(glob.glob(os.path.join(log_dir, "summary_*.json")))
    if not files:
        hb.warnings.append(f"no engine journal files found in {log_dir}/ (engine never ran here?)")
        return
    newest = files[-1]
    hb.journal_last = os.path.basename(newest)[len("summary_"):-len(".json")]
    try:
        with open(newest) as fh:
            hb.journal_summary = json.load(fh)
    except Exception:  # noqa: BLE001
        hb.journal_summary = None
    try:
        age = (date.fromisoformat(hb.date) - date.fromisoformat(hb.journal_last)).days
    except ValueError:
        return
    if age > 3:
        hb.warnings.append(f"engine journal last written {hb.journal_last} ({age} days ago): "
                           f"is the trader container running?")
    js = hb.journal_summary or {}
    ctx = js.get("context", {}) if isinstance(js, dict) else {}
    if ctx and ctx.get("equity_close") in (0, 0.0) and age <= 3:
        hb.warnings.append(f"engine journal {hb.journal_last} recorded equity_close=0: the "
                           f"engine could not read the account (bad keys?)")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _money(x: float) -> str:
    return f"${x:,.0f}"


def render_text(hb: Heartbeat) -> str:
    L: List[str] = []
    if not hb.ok:
        L.append(f"TRADER HEARTBEAT {hb.date}  **ACCOUNT UNREACHABLE**")
        L.extend("  ! " + w for w in hb.warnings)
        L.append(f"  channels: {hb.channels}")
        if hb.ira_text:
            L += ["", hb.ira_text]
        return "\n".join(L)

    L.append(f"TRADER HEARTBEAT {hb.date}  (paper)")
    L.append(f"  equity {_money(hb.equity)}  day {hb.day_change:+,.0f} ({hb.day_change_pct:+.2f}%)"
             f"  cash {_money(hb.cash)}  buying power {_money(hb.buying_power)}")
    if hb.inception and hb.inception.get("acct_ret") is not None:
        i = hb.inception
        q = i.get("qqq_ret")
        L.append(f"  since {i['start']} ({i['days']}d): account {i['acct_ret']:+.2f}%"
                 + (f"  vs QQQ buy&hold {q:+.2f}%" if q is not None else ""))
    for w in hb.warnings:
        L.append(f"  ! {w}")

    core_val = sum(p["value"] for p in hb.core_positions)
    intra_val = sum(p["value"] for p in hb.intraday_positions)
    L.append("")
    L.append(f"  Positions: core {_money(core_val)} ({core_val / hb.equity * 100 if hb.equity else 0:.0f}%)"
             f"  intraday {_money(intra_val)}")
    for label, rows in (("core", hb.core_positions), ("intraday", hb.intraday_positions)):
        for p in sorted(rows, key=lambda r: -r["value"]):
            L.append(f"    {label:8s} {p['symbol']:6s} {p['qty']:>8.0f} @ {p['price']:>9.2f}  "
                     f"{_money(p['value']):>10s}  upl {p['upl']:+,.0f} ({p['upl_pct']:+.1f}%)")
    if not hb.core_positions and not hb.intraday_positions:
        L.append("    (flat)")

    L.append("")
    if hb.fills_today:
        L.append(f"  Fills today ({len(hb.fills_today)}):")
        for f in hb.fills_today:
            L.append(f"    {f['time']} {f['side']:4s} {f['qty']:>6.0f} {f['symbol']:6s} @ {f['price']:.2f}"
                     f"{'  [core]' if f['core'] else ''}")
    else:
        L.append("  Fills today: none")

    if hb.core:
        c = hb.core
        L.append("")
        L.append(f"  Core ({c['alloc_pct']:.0f}% allocation, trend gate "
                 f"{'ON sma' + str(c['trend_sma']) if c['trend_gate'] else 'off'}): "
                 f"holding {c['core_pct']:.1f}% of equity")
        L.append("    sym    target   eff    actual  trend     action")
        for v in c["views"]:
            L.append(f"    {v.symbol:6s} {v.target_pct:5.1f}%  {v.effective_pct:5.1f}%  "
                     f"{v.actual_pct:5.1f}%  {v.trend.label:7s}  {v.action}")
        if c["trades"]:
            L.append("    Next rebalance would: " + "; ".join(
                f"{t.side} {t.quantity} {t.symbol}" for t in c["trades"]))
        else:
            L.append("    Next rebalance: no trades (within bands)")
        lr = c.get("last_rebalance") or {}
        if lr:
            L.append(f"    Last rebalance {lr.get('date')}: {len(lr.get('trades', []))} orders"
                     + (f", FAILED {lr['failed']}" if lr.get("failed") else ""))

    if hb.news:
        flagged = [d for d in hb.news.values() if d.flag]
        others = [d for d in hb.news.values() if not d.flag and d.articles]
        L.append("")
        L.append("  News on holdings (last 36h):")
        for d in sorted(flagged, key=lambda d: d.mean_sentiment or 0) + others:
            tag = f" [{d.flag}]" if d.flag else ""
            L.append(f"    {d.symbol}{tag}:")
            for a in d.articles:
                s = f"{a.sentiment_score:+.2f}" if a.sentiment_score is not None else "  n/a"
                L.append(f"      {s} {a.headline[:100]}")
        quiet = [s for s, d in hb.news.items() if not d.articles]
        if quiet:
            L.append(f"    quiet: {', '.join(quiet)}")

    L.append("")
    js = hb.journal_summary or {}
    if js:
        L.append(f"  Engine journal: last file {hb.journal_last} "
                 f"(trades {js.get('trades', 0)}, pnl {js.get('pnl', 0):+,.2f})")
    L.append(f"  Channels: {hb.channels}")
    if hb.ira_text:
        L += ["", hb.ira_text]
    return "\n".join(L)


def render_push(hb: Heartbeat) -> str:
    if not hb.ok:
        return f"ACCOUNT UNREACHABLE {hb.date}: nothing is trading. " + (hb.ira_push or "")
    parts = [f"Paper {_money(hb.equity)} ({hb.day_change_pct:+.2f}%)"]
    core_val = sum(p["value"] for p in hb.core_positions)
    if hb.equity:
        parts.append(f"core {core_val / hb.equity * 100:.0f}%")
    parts.append(f"{len(hb.fills_today)} fills")
    if hb.core and hb.core.get("trades"):
        parts.append("next: " + ", ".join(f"{t.side} {t.quantity} {t.symbol}"
                                          for t in hb.core["trades"][:3]))
    if hb.warnings:
        parts.append(f"{len(hb.warnings)} warning(s)")
    if hb.ira_push:
        parts.append(hb.ira_push)
    return " | ".join(parts)


def subject(hb: Heartbeat) -> str:
    if not hb.ok:
        return f"[trader] {hb.date} ACCOUNT UNREACHABLE"
    flag = " !" if hb.warnings else ""
    return f"[trader] {hb.date} {_money(hb.equity)} ({hb.day_change_pct:+.2f}%){flag}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(config: Config, broker: Optional[AlpacaBroker] = None,
        notifier: Optional[Notifier] = None, *, send: bool = True,
        with_news: bool = True, with_ira: Optional[bool] = None) -> Heartbeat:
    broker = broker or AlpacaBroker(config.broker)
    notifier = notifier or Notifier()
    hb = build(config, broker, notifier=notifier, with_news=with_news, with_ira=with_ira)
    text = render_text(hb)
    logger.info("Heartbeat:\n%s", text)
    if send:
        priority = "high" if (not hb.ok or hb.warnings) else "default"
        notifier.send(subject(hb), text, push_body=render_push(hb), priority=priority,
                      tags=(["warning"] if priority == "high" else None))
    return hb


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
