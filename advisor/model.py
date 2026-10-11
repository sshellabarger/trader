"""The target model shared by the IRA advisor and the paper account's core.

One place for the weights so the paper account is a live sandbox for exactly
the model the IRA follows: the five mega-caps capped at 5% (MSFT 3%) and the
remainder in the diversified, ballasted ETF core from core_portfolio.py.
"""
from __future__ import annotations

import csv
from typing import Dict, Tuple

CONVICTION: Dict[str, float] = {"AMZN": .05, "AAPL": .05, "GOOGL": .05, "NVDA": .05, "MSFT": .03}
CORE_RAW: Dict[str, float] = {"SPY": 25, "IEF": 23, "EFA": 12, "TLT": 12, "EEM": 8,
                              "GLD": 8, "DBC": 7, "VNQ": 5}
CASHLIKE = {"CASH", "SPAXX", "FDRXX", "SWVXX", "FZFXX", "FNSXX"}


def default_targets() -> Dict[str, float]:
    """Weights summing to 1.0: conviction names at their caps, the ETF core
    scaled to fill the rest."""
    conv = sum(CONVICTION.values())
    scale = (1 - conv) / sum(CORE_RAW.values())
    t = dict(CONVICTION)
    for s, w in CORE_RAW.items():
        t[s] = w * scale
    return t


def parse_targets(s: str) -> Dict[str, float]:
    """'SYM:w,SYM:w' -> {SYM: w}. Weights are taken as given (not normalised),
    so a model that leaves part of the book in cash can say so."""
    out: Dict[str, float] = {}
    for part in (s or "").split(","):
        if part.strip():
            k, v = part.split(":")
            out[k.strip().upper()] = float(v)
    return out


def _num(x):
    if x is None:
        return None
    x = str(x).strip().replace("$", "").replace(",", "").replace("%", "")
    if x in ("", "--", "-", "N/A"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def load_positions_csv(path: str) -> Tuple[Dict[str, dict], float]:
    """Parse a Schwab/Fidelity-style positions export -> (positions, cash).
    positions: {sym: {'qty', 'value', 'price'}}."""
    pos: Dict[str, dict] = {}
    cash = 0.0
    with open(path) as fh:
        r = csv.DictReader(fh)
        low = {c.lower().strip(): c for c in (r.fieldnames or [])}
        symc = low.get("symbol")
        valc = (low.get("mkt val (market value)") or low.get("market value")
                or low.get("current value"))
        qtyc = low.get("qty (quantity)") or low.get("quantity") or low.get("qty")
        for row in r:
            sym = (row.get(symc) or "").strip().upper() if symc else ""
            val = _num(row.get(valc)) if valc else None
            qty = _num(row.get(qtyc)) if qtyc else None
            if not sym or val is None:
                continue
            if sym in CASHLIKE:
                cash += val
                continue
            pos[sym] = {"qty": qty or 0.0, "value": val,
                        "price": (val / qty if qty else 0.0)}
    return pos, cash
