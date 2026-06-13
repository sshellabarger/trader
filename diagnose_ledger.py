"""Ledger diagnostics — replays existing backtest trade CSVs to quantify where
the edge lives, without needing market-data access (Alpaca is unreachable from
some sandboxes). Stdlib only, to match the project's minimal dependencies.

Two headline diagnostics from the 2026-06-13 session:

  1. Per-symbol expectancy. The SQQQ bear leg is a structural net loser in every
     coherent slice (it is an unhedged counter-trend long that fights the
     Nasdaq's upward drift). This motivates orb_trade_both_directions=False.

  2. Opening-range %-of-price buckets, to confirm the ORB %-band filter keeps
     the profitable middle band and drops sub-noise / oversized-whipsaw ranges.

Any filter that only *removes* trades can be evaluated exactly here: a dropped
trade contributes $0 instead of its realized pnl. Caveat: summed dollar pnl
ignores equity-path position sizing, so it is reliable for ranking and for
profit-factor / win-rate (path-independent), not for absolute return. Confirm
with a real backtest once data access is available.

Usage:
    python -m trader.diagnose_ledger [path/to/trades_*.csv ...]
    # default: backtest_results/trades_2025-01-02_to_2025-06-30.csv
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys
from typing import Dict, List


def _pf(rows: List[dict]) -> Dict[str, float]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "pnl": 0.0, "pf": 0.0, "win": 0.0}
    pnls = [float(r["pnl"]) for r in rows]
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p <= 0))
    pf = wins / loss if loss > 0 else float("inf")
    win_rate = sum(1 for p in pnls if p > 0) / n * 100
    return {"n": n, "pnl": sum(pnls), "pf": pf, "win": win_rate}


def _fmt(label: str, s: Dict[str, float]) -> str:
    return (f"  {label:16s} n={s['n']:3d}  pnl=${s['pnl']:>+10,.0f}  "
            f"PF={s['pf']:5.2f}  win={s['win']:4.1f}%")


def _range_pct(row: dict) -> float:
    m = re.search(r"range=\$([0-9.]+)", row.get("entry_reason", ""))
    if not m:
        return float("nan")
    rng = float(m.group(1))
    entry = float(row["entry_price"]) or 1.0
    return rng / entry * 100.0


def diagnose(paths: List[str]) -> None:
    rows: List[dict] = []
    for p in paths:
        with open(p, newline="") as f:
            rows.extend(csv.DictReader(f))
    orb = [r for r in rows if r.get("strategy") == "orb"]
    print(f"Loaded {len(rows)} trades ({len(orb)} ORB) from {len(paths)} file(s)\n")

    print("By strategy:")
    for strat in sorted({r["strategy"] for r in rows}):
        print(_fmt(strat, _pf([r for r in rows if r["strategy"] == strat])))

    print("\nORB by symbol (bull TQQQ vs bear SQQQ):")
    for sym in sorted({r["symbol"] for r in orb}):
        print(_fmt(sym, _pf([r for r in orb if r["symbol"] == sym])))

    print("\nORB by opening-range %-of-price band:")
    edges = [(0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.1),
             (1.1, 1.3), (1.3, 1.6), (1.6, 2.0), (2.0, 99)]
    for lo, hi in edges:
        bucket = [r for r in orb if lo <= _range_pct(r) < hi]
        print(_fmt(f"{lo:.1f}-{hi:.1f}%", _pf(bucket)))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        default = os.path.join(here, "backtest_results",
                               "trades_2025-01-02_to_2025-06-30.csv")
        args = [default] if os.path.exists(default) else sorted(
            glob.glob(os.path.join(here, "backtest_results", "trades_*.csv")))
    if not args:
        print("No trade CSVs found. Pass a path to a trades_*.csv file.")
        sys.exit(1)
    diagnose(args)
