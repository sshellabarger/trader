"""Pure trade-suggestion logic. No network, no side effects, fully testable.

A "model" is a dict of target weights {symbol: weight}. Weights need not sum to
1; the remainder is implicitly cash. Symbols you hold but that aren't in the
model have an implicit target of 0 (full exit). suggest_trades turns the gap
between where you are and the model into whole-share BUY/SELL trades, skipping
drift smaller than `band`. This single function expresses the whole plan: trim
the mega-caps to their cap, sell the tail to zero, and buy the core ETFs.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Dict, List


@dataclass
class Trade:
    symbol: str
    side: str            # "BUY" or "SELL"
    quantity: int
    reason: str
    est_price: float = 0.0

    @property
    def est_value(self) -> float:
        return round(self.quantity * self.est_price, 2)


def _pct(v, total):
    return f"{(v / total * 100) if total else 0:.0f}%"


def suggest_trades(positions: Dict[str, dict], cash: float,
                   target_weights: Dict[str, float], prices: Dict[str, float],
                   band: float = 0.0, min_dollars: float = 100.0) -> List[Trade]:
    """positions: {sym: {'qty': float, 'value': float, 'price': float}}.
    Returns whole-share trades to move each holding toward its target dollar
    value, skipping relative drift within `band` (full exits and new buys always
    act). SELLs are returned before BUYs so cash is freed before it's spent."""
    total = sum(p.get("value", 0.0) for p in positions.values()) + cash
    trades: List[Trade] = []
    for s in sorted(set(positions) | set(target_weights)):
        tgt_val = target_weights.get(s, 0.0) * total
        cur_val = positions.get(s, {}).get("value", 0.0)
        price = prices.get(s) or positions.get(s, {}).get("price") or 0.0

        if tgt_val > 0 and abs(cur_val - tgt_val) <= band * tgt_val:
            continue                                  # within drift band
        delta = tgt_val - cur_val
        if abs(delta) < min_dollars or price <= 0:
            continue

        if delta < 0:                                  # SELL
            held = positions.get(s, {}).get("qty", 0) or 0
            qty = min(int(floor(abs(delta) / price)), int(floor(held)))
            if qty < 1:
                continue
            reason = (f"exit ({_pct(cur_val, total)} -> 0%)" if tgt_val == 0
                      else f"trim toward {_pct(tgt_val, total)} (now {_pct(cur_val, total)})")
            trades.append(Trade(s, "SELL", qty, reason, price))
        else:                                          # BUY
            qty = int(floor(delta / price))
            if qty < 1:
                continue
            trades.append(Trade(s, "BUY", qty,
                                f"buy toward {_pct(tgt_val, total)} (now {_pct(cur_val, total)})",
                                price))

    trades.sort(key=lambda t: (t.side != "SELL", -t.est_value))   # sells first, largest first
    return trades
