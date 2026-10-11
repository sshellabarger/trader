"""The target model shared by the IRA advisor and the paper account's core.

One place for the weights so the paper account is a live sandbox for exactly
the model the IRA follows: the five mega-caps capped at 5% (MSFT 3%) and the
remainder in the diversified, ballasted ETF core from core_portfolio.py.
"""
from __future__ import annotations

import csv
import re
from typing import Dict, Tuple

_TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")

CONVICTION: Dict[str, float] = {"AMZN": .05, "AAPL": .05, "GOOGL": .05, "NVDA": .05, "MSFT": .03}
CORE_RAW: Dict[str, float] = {"SPY": 25, "IEF": 23, "EFA": 12, "TLT": 12, "EEM": 8,
                              "GLD": 8, "DBC": 7, "VNQ": 5}
CASHLIKE = {"CASH", "SPAXX", "FDRXX", "SWVXX", "FZFXX", "FNSXX", "CASH & CASH INVESTMENTS"}


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


def load_holdings_txt(path: str) -> Tuple[Dict[str, dict], float]:
    """Hand-maintained holdings: one `SYMBOL SHARES` per line (comma or
    whitespace separated), `CASH 12345` for cash, `#` comments. Values and
    prices are left at 0; the review fills them from the latest close, so
    the file only changes when you actually buy or sell. -> (positions, cash)"""
    pos: Dict[str, dict] = {}
    cash = 0.0
    with open(path) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p for p in line.replace(",", " ").split() if p]
            sym = parts[0].upper().replace("/", ".")      # MOG/A -> MOG.A (Alpaca form)
            qty = _num(parts[1]) if len(parts) > 1 else None
            if qty is None:
                raise ValueError(f"{path}: cannot read a share count from line: {raw.strip()!r}")
            if sym in CASHLIKE:
                cash += qty
                continue
            pos[sym] = {"qty": qty, "value": 0.0, "price": 0.0}
    return pos, cash


def load_positions_csv(path: str) -> Tuple[Dict[str, dict], float]:
    """Parse a Schwab/Fidelity-style positions export -> (positions, cash).
    positions: {sym: {'qty', 'value', 'price'}}. Skips any preamble lines
    before the header row (Schwab exports start with an "as of" line)."""
    pos: Dict[str, dict] = {}
    cash = 0.0
    with open(path) as fh:
        lines = fh.readlines()
    start = next((i for i, l in enumerate(lines)
                  if l.lstrip().lstrip('"').lower().startswith("symbol")), 0)
    import io
    with io.StringIO("".join(lines[start:])) as fh:
        r = csv.DictReader(fh)
        low = {c.lower().strip(): c for c in (r.fieldnames or [])}
        symc = low.get("symbol")
        valc = (low.get("mkt val (market value)") or low.get("market value")
                or low.get("current value"))
        qtyc = low.get("qty (quantity)") or low.get("quantity") or low.get("qty")
        for row in r:
            sym = (row.get(symc) or "").strip().upper().replace("/", ".") if symc else ""
            val = _num(row.get(valc)) if valc else None
            qty = _num(row.get(qtyc)) if qtyc else None
            if not sym or val is None:
                continue
            if sym in CASHLIKE:
                cash += val
                continue
            if not qty or not _TICKER.match(sym):
                # Footer rows ("Positions Total") and placeholder lines such as
                # Schwab's "NO NUMBER ... XXXSUBMITTED" pending-share entries.
                continue
            pos[sym] = {"qty": qty, "value": val, "price": val / qty}
    return pos, cash
