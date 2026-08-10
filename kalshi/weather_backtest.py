"""
Score the weather model against the recorded market week — model Brier vs
market Brier at fixed pre-outcome horizons, plus a $25/market paper-trade
tally under the phase-0 entry rule (net_edge >= 3c at 15Z/18Z).

Inputs: a CSV built from the recorder's banked snapshots+settlements (one row
per settled ticker: outcome + bid/ask at 12/15/18/21Z on the event day), and
Open-Meteo's archived ensemble runs for the same dates (cached to disk so
reruns are offline).

HONESTY CAVEAT, printed with every run: Open-Meteo's archive serves each
day's hours from short-lead runs (roughly the 00Z cycle for afternoon hours,
lead ~12-21h). A live 12-15Z entry would use the same 00Z/06Z cycles, so the
comparison is close-to-fair, but archived stitching can be slightly fresher
than live reality. Treat backtest wins as PROVISIONAL until the prospective
(live-fetch) sample confirms them. Correction knobs must NOT be fit on the
same week being scored.
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from typing import Dict, List

from .config import taker_fee_cents
from .weather import (STATIONS, WeatherConfig, corrected_pools,
                      fair_value_cents, fetch_ensemble_daymax,
                      specs_from_tickers)

HORIZONS = ("12Z", "15Z", "18Z", "21Z")
ENTRY_HORIZONS = ("15Z", "18Z")


def load_rows(path: str) -> List[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = {"ticker": r["ticker"], "event": r["event"],
                   "date": r["date"], "y": int(r["outcome"]), "quotes": {}}
            for hz in HORIZONS:
                b, a = r.get(f"bid{hz[:2]}", ""), r.get(f"ask{hz[:2]}", "")
                if b and a:
                    row["quotes"][hz] = (int(b), int(a))
            rows.append(row)
    return rows


def pools_for(series: str, date: str, cache_dir: str) -> Dict[str, List[float]]:
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{series}_{date}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    pools = fetch_ensemble_daymax(series, date)
    if pools.get("gfs") or pools.get("ecmwf"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pools, f)      # never cache an empty answer
    return pools


def run(inputs: str, cache_dir: str = "data/wx_cache", min_edge: float = 3.0,
        stake_cents: int = 2500) -> dict:
    cfg = WeatherConfig()
    rows = load_rows(inputs)
    by_event = defaultdict(list)
    for r in rows:
        if r["quotes"]:                       # pre-fix rows have no quotes
            by_event[(r["ticker"].split("-", 1)[0], r["event"], r["date"])].append(r)

    brier = defaultdict(lambda: defaultdict(list))   # series -> hz -> [(pm, pk, y)]
    trades = []
    fetch_failures, skipped_specs = [], []

    for (series, event, date), evrows in sorted(by_event.items()):
        if series not in STATIONS:
            continue
        try:
            pools = pools_for(series, date, cache_dir)
        except Exception as exc:            # noqa: BLE001 - report, don't die
            fetch_failures.append(f"{series} {date}: {exc}")
            continue
        if not (pools.get("gfs") or pools.get("ecmwf")):
            fetch_failures.append(f"{series} {date}: empty ensemble")
            continue
        pools = corrected_pools(pools, cfg.bias.get(series, 0.0),
                                cfg.spread.get(series, 1.0))
        specs, skipped = specs_from_tickers([r["ticker"] for r in evrows])
        skipped_specs.extend(skipped)
        for r in evrows:
            spec = specs.get(r["ticker"])
            if spec is None:
                continue
            fair = fair_value_cents(spec, pools, cfg)
            for hz, (bid, ask) in r["quotes"].items():
                mid = (bid + ask) / 2.0
                brier[series][hz].append((fair / 100.0, mid / 100.0, r["y"]))
                if hz in ENTRY_HORIZONS:
                    fee1 = taker_fee_cents(int(round(mid)), 1)
                    edge = abs(fair - mid) - fee1 - (ask - bid) / 2.0
                    if edge >= min_edge:
                        if fair > mid:      # buy YES at the ask
                            cost = ask + fee1
                            n = int(stake_cents // max(1, cost))
                            pnl = n * (100 * r["y"] - ask) - taker_fee_cents(ask, n)
                        else:               # buy NO at (100 - bid)
                            price = 100 - bid
                            cost = price + fee1
                            n = int(stake_cents // max(1, cost))
                            pnl = n * (100 * (1 - r["y"]) - price) - taker_fee_cents(price, n)
                        if n > 0:
                            trades.append({"ticker": r["ticker"], "hz": hz,
                                           "fair": round(fair, 1), "mid": mid,
                                           "edge": round(edge, 2), "n": n,
                                           "pnl_cents": pnl})
    return {"cfg": cfg, "brier": brier, "trades": trades,
            "fetch_failures": fetch_failures, "skipped_specs": skipped_specs}


def report(result: dict):
    brier, trades = result["brier"], result["trades"]

    def b(pairs, idx):
        return (sum((p[idx] - p[2]) ** 2 for p in pairs) / len(pairs)
                if pairs else None)

    print("\n== MODEL vs MARKET Brier (identity correction knobs) ==")
    print(f"{'series':<11}" + "".join(f"{h:>21}" for h in HORIZONS))
    pooled = defaultdict(list)
    for series in sorted(brier):
        cells = []
        for hz in HORIZONS:
            pairs = brier[series][hz]
            pooled[hz].extend(pairs)
            bm, bk = b(pairs, 0), b(pairs, 1)
            cells.append(f"{bm:.4f}/{bk:.4f}(n={len(pairs)})" if pairs else "--")
        print(f"{series:<11}" + "".join(f"{c:>21}" for c in cells))
    cells = []
    for hz in HORIZONS:
        bm, bk = b(pooled[hz], 0), b(pooled[hz], 1)
        cells.append(f"{bm:.4f}/{bk:.4f}" if pooled[hz] else "--")
    print(f"{'POOLED m/mkt':<11}" + "".join(f"{c:>21}" for c in cells))

    print(f"\n== paper trades (net_edge >= 3c at 15Z/18Z, $25 stakes) ==")
    if trades:
        pnl = sum(t["pnl_cents"] for t in trades)
        wins = [t for t in trades if t["pnl_cents"] > 0]
        gp = sum(t["pnl_cents"] for t in wins)
        gl = -sum(t["pnl_cents"] for t in trades if t["pnl_cents"] < 0)
        pf = (gp / gl) if gl else float("inf")
        print(f"trades {len(trades)}  wins {len(wins)}  "
              f"pnl ${pnl/100:+.2f}  PF {pf:.2f}")
        for t in trades:
            print(f"  {t['ticker']:<26} {t['hz']} fair {t['fair']:>5} "
                  f"mid {t['mid']:>5} edge {t['edge']:>5} x{t['n']:<3} "
                  f"${t['pnl_cents']/100:+.2f}")
    else:
        print("no trades cleared the 3c bar")
    if result["fetch_failures"]:
        print("\nfetch failures:", *result["fetch_failures"], sep="\n  ")
    if result["skipped_specs"]:
        print(f"skipped tickers (spec inference): {sorted(set(result['skipped_specs']))}")
    print("\nCAVEAT: archived-run leads approximate (not equal to) live 12-15Z "
          "information; provisional until the prospective sample confirms. "
          "Do not fit correction knobs on this same week.")


def archive_today(cache_dir: str = "data/wx_cache") -> int:
    """Fetch and cache today's + tomorrow's ensemble day-max pools for every
    station. Open-Meteo keeps member values only ~4-5 days, so calibration
    history exists ONLY if something saves it daily — this is that something.
    Idempotent: existing cache files are refreshed (later runs see later
    model cycles; the file kept is the freshest fetch of that event date)."""
    import datetime as _dt
    import zoneinfo

    saved = 0
    for series, station in STATIONS.items():
        today = _dt.datetime.now(zoneinfo.ZoneInfo(station.timezone)).date()
        for offset in (0, 1):
            date = (today + _dt.timedelta(days=offset)).isoformat()
            try:
                pools = fetch_ensemble_daymax(series, date)
            except Exception as exc:  # noqa: BLE001
                print(f"{series} {date}: fetch failed: {exc}")
                continue
            if not (pools.get("gfs") or pools.get("ecmwf")):
                print(f"{series} {date}: empty ensemble, not cached")
                continue
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, f"{series}_{date}.json"),
                      "w", encoding="utf-8") as f:
                json.dump(pools, f)
            saved += 1
    print(f"archived {saved} station-days -> {cache_dir}")
    return saved
