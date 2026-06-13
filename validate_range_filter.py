"""
Ledger-based validation of the ORB opening-range %-band filter.

The sandbox used to iterate on this strategy had no market-data network access,
so the full backtest (which fetches 1-min bars from Alpaca) could not be re-run
there. Instead, this script replays the *existing* H1-2025 trade ledger
(backtest_results/trades_*.csv) and applies the SAME range%-of-price logic the
live ORB strategy now enforces, reporting before/after portfolio metrics.

This is a conservative lower bound for a trade-removal filter: it sums the
realized per-trade P&L of the surviving trades. It cannot model the trades that
*would* have been taken with the capital freed by skipping losers, nor the
better compounding from preserving capital — both of which only help. It is NOT
a substitute for re-running `python -m trader backtest` on a machine with data
across multiple windows + walk-forward; do that before trusting live.

Usage:
    python -m trader.validate_range_filter [trades_csv ...]
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys
from typing import List, Optional

from .config import Config

_RANGE_RE = re.compile(r"range=\$([0-9.]+)")


def _f(x: str) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _range_pct(row: dict) -> Optional[float]:
    """Opening-range size as a % of entry price, parsed from the trade record.
    Mirrors ORBStrategy: range_size / entry_price * 100."""
    m = _RANGE_RE.search(row.get("entry_reason", ""))
    price = _f(row.get("entry_price", ""))
    if not m or price <= 0:
        return None
    return float(m.group(1)) / price * 100.0


def _metrics(rows: List[dict]) -> dict:
    pnl = [_f(r["pnl"]) for r in rows]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "n": len(rows),
        "pnl": sum(pnl),
        "win_rate": (100.0 * len(wins) / len(rows)) if rows else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss else float("inf"),
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
    }


def _keep(row: dict, cfg: Config) -> bool:
    """Apply the new ORB %-band filter to a historical ORB trade. Non-ORB
    trades (e.g. vwap_reversion) are always kept — the filter only governs ORB."""
    if row.get("strategy") != "orb":
        return True
    rp = _range_pct(row)
    if rp is None:
        return True  # can't parse -> don't drop
    sc = cfg.strategy
    if rp < sc.orb_min_range_pct:
        return False
    if sc.orb_max_range_pct > 0 and rp > sc.orb_max_range_pct:
        return False
    return True


def _fmt(label: str, m: dict) -> str:
    pf = m["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (f"  {label:14s} n={m['n']:3d}  pnl=${m['pnl']:+10,.0f}  "
            f"win={m['win_rate']:4.1f}%  PF={pf_s:>5}  "
            f"avgW=${m['avg_win']:+,.0f}  avgL=${m['avg_loss']:+,.0f}")


def validate(path: str) -> None:
    with open(path) as f:
        rows = list(csv.DictReader(f))
    cfg = Config()
    kept = [r for r in rows if _keep(r, cfg)]
    print(f"\n{os.path.basename(path)}  "
          f"(band {cfg.strategy.orb_min_range_pct}-{cfg.strategy.orb_max_range_pct}% of price)")
    print(_fmt("baseline", _metrics(rows)))
    print(_fmt("filtered", _metrics(kept)))


def main(argv: List[str]) -> int:
    args = argv[1:]
    if not args:
        # Default: every full/partial ledger we can find next to the package.
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        args = sorted(glob.glob(os.path.join(here, "backtest_results", "trades_*.csv")))
    if not args:
        print("No trades CSV found. Pass a path, e.g. backtest_results/trades_*.csv")
        return 1
    for p in args:
        if os.path.exists(p):
            validate(p)
        else:
            print(f"skip (missing): {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
