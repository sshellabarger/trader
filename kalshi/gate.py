"""
Score the sports forward sample against the Oct-1 funding gate.

Joins data/kalshi/sports_scans.jsonl (written by kalshi-sports-scan) to
data/kalshi/settlements.jsonl (written by the recorder) and reports each gate
criterion:

  1. >= 30 settled paper trades
  2. profit factor >= 1.3 after fees at conservative (taker) fills
  3. model Brier < market Brier

Rules, fixed up front so the numbers can't be tuned after the fact:
- Only scans taken BEFORE the game's commence time count.
- Brier: one row per game, the LAST pre-game scan of the home-team ticker
  (the two tickers of a game are complements, so scoring both would just
  double the sample). Market probability is the quote mid.
- Paper trades: one per game, the FIRST pre-game scan whose net_edge clears
  min_edge. Taker fill (buy YES at the ask, or NO at 100 - bid) plus the
  taker fee, fixed stake per trade. This is the conservative-fill case the
  gate asks for; resting maker fills are not assumed.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from .config import taker_fee_cents
from .sports import parse_event

GATE_MIN_TRADES = 30
GATE_MIN_PF = 1.3


def load_jsonl(path: str) -> List[dict]:
    """Every parseable line; a torn last line (writer mid-append) is skipped."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _ts(iso: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def outcomes(settlements: Iterable[dict]) -> Dict[str, int]:
    """ticker -> 1 (yes) / 0 (no). Voided or blank results are left out."""
    out = {}
    for s in settlements:
        res = str(s.get("result") or "").lower()
        if s.get("ticker") and res in ("yes", "no"):
            out[s["ticker"]] = 1 if res == "yes" else 0
    return out


def taker_pnl_cents(side: str, bid: int, ask: int, y: int,
                    stake_cents: int) -> Optional[dict]:
    """Buy YES at the ask or NO at (100 - bid) with a fixed stake, taker fee
    included. None when the stake can't buy a single contract."""
    price = ask if side == "yes" else 100 - bid
    won = y if side == "yes" else 1 - y
    n = int(stake_cents // max(1, price + taker_fee_cents(price, 1)))
    if n <= 0:
        return None
    fee = taker_fee_cents(price, n)
    return {"n": n, "price": price, "fee": fee,
            "pnl_cents": n * (100 * won - price) - fee}


def _home_ticker(row: dict) -> bool:
    p = parse_event(row.get("series", ""), row.get("event", ""))
    return bool(p) and row.get("ticker", "").rsplit("-", 1)[-1] == p["home"]


def score(scans: List[dict], settlements: List[dict], min_edge: float = 2.0,
          stake_cents: int = 2500) -> dict:
    y_of = outcomes(settlements)
    pre = []
    for r in scans:
        t, start = _ts(r.get("t", "")), _ts(r.get("commence", ""))
        if t is None or start is None or t >= start:
            continue
        if not all(k in r for k in ("ticker", "event", "fair", "bid", "ask")):
            continue
        pre.append({**r, "_t": t})
    pre.sort(key=lambda r: r["_t"])

    by_event: Dict[str, List[dict]] = {}
    for r in pre:
        by_event.setdefault(r["event"], []).append(r)

    brier_pairs, trades, pending = [], [], []
    for event, rows in sorted(by_event.items()):
        # Brier: last pre-game scan, home ticker preferred, else a fixed pick.
        home = [r for r in rows if _home_ticker(r)]
        pick = home or [r for r in rows
                        if r["ticker"] == min(x["ticker"] for x in rows)]
        last = pick[-1]
        if last["ticker"] in y_of:
            mid = (last["bid"] + last["ask"]) / 2.0
            brier_pairs.append((last["fair"] / 100.0, mid / 100.0,
                                y_of[last["ticker"]]))

        entry = next((r for r in rows
                      if r.get("net_edge", float("-inf")) >= min_edge), None)
        if entry is None:
            continue
        if entry["ticker"] not in y_of:
            pending.append(entry["ticker"])
            continue
        fill = taker_pnl_cents(entry.get("side", "yes"), entry["bid"],
                               entry["ask"], y_of[entry["ticker"]], stake_cents)
        if fill:
            trades.append({"ticker": entry["ticker"], "t": entry["t"],
                           "side": entry.get("side"), "fair": entry["fair"],
                           "edge": entry["net_edge"], **fill})

    edges = [r["net_edge"] for r in pre if "net_edge" in r]
    return {"scan_rows": len(scans), "pregame_rows": len(pre),
            "games": len(by_event), "brier_pairs": brier_pairs,
            "trades": trades, "pending": pending, "edges": edges,
            "min_edge": min_edge, "stake_cents": stake_cents}


def verdict(result: dict) -> dict:
    trades, pairs = result["trades"], result["brier_pairs"]
    gp = sum(t["pnl_cents"] for t in trades if t["pnl_cents"] > 0)
    gl = -sum(t["pnl_cents"] for t in trades if t["pnl_cents"] < 0)
    pf = (gp / gl) if gl else (float("inf") if gp else None)
    bm = (sum((p[0] - p[2]) ** 2 for p in pairs) / len(pairs)) if pairs else None
    bk = (sum((p[1] - p[2]) ** 2 for p in pairs) / len(pairs)) if pairs else None
    checks = {
        "trades": len(trades) >= GATE_MIN_TRADES,
        "profit_factor": pf is not None and pf >= GATE_MIN_PF,
        "brier": bm is not None and bm < bk,
    }
    return {"n_trades": len(trades), "pf": pf,
            "pnl_cents": sum(t["pnl_cents"] for t in trades),
            "model_brier": bm, "market_brier": bk, "n_brier": len(pairs),
            "checks": checks, "passed": all(checks.values())}


def report(result: dict) -> dict:
    v = verdict(result)
    edges = result["edges"]
    print("== Kalshi sports gate (Oct-1 criteria) ==")
    print(f"scan rows {result['scan_rows']}  pre-game {result['pregame_rows']}  "
          f"games {result['games']}")
    if edges:
        cleared = sum(1 for e in edges if e >= result["min_edge"])
        print(f"net edge: max {max(edges):+.2f}c  rows >= 0: "
              f"{sum(1 for e in edges if e >= 0)}  rows >= "
              f"{result['min_edge']}c: {cleared}")

    def mark(ok):
        return "PASS" if ok else "FAIL"

    pf = "--" if v["pf"] is None else f"{v['pf']:.2f}"
    print(f"\n[{mark(v['checks']['trades'])}] settled paper trades "
          f"{v['n_trades']} (need >= {GATE_MIN_TRADES}; "
          f"{len(result['pending'])} awaiting settlement)")
    print(f"[{mark(v['checks']['profit_factor'])}] profit factor {pf} "
          f"(need >= {GATE_MIN_PF}; taker fills, "
          f"${result['stake_cents'] / 100:.0f} stakes, "
          f"pnl ${v['pnl_cents'] / 100:+.2f})")
    if v["model_brier"] is None:
        print("[FAIL] Brier: no settled games in the sample yet")
    else:
        print(f"[{mark(v['checks']['brier'])}] Brier model {v['model_brier']:.4f} "
              f"vs market {v['market_brier']:.4f} (n={v['n_brier']} games)")
    for t in result["trades"]:
        print(f"  {t['ticker']:<34} {t['side']:<3} fair {t['fair']:>5} "
              f"@{t['price']:>3} x{t['n']:<3} edge {t['edge']:>5} "
              f"${t['pnl_cents'] / 100:+.2f}")
    print(f"\nGATE: {'PASS' if v['passed'] else 'FAIL'}")
    return v
