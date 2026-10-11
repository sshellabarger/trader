"""Human-in-the-loop review and execution.

Nothing is placed without (1) an explicit per-trade approval, and (2) in live
mode, typing the ticker to confirm. Orders go one at a time, and after each live
order you're paused to review the fill before the next. Dry run by default.
"""
from __future__ import annotations

from typing import Callable, List

from .engine import Trade


def review_and_execute(trades: List[Trade], client=None, *, live: bool = False,
                       max_order_value: float = 50000.0, max_orders: int = 25,
                       prompt: Callable[[str], str] = input,
                       out: Callable[[str], None] = print) -> List[dict]:
    mode = "LIVE" if live else "DRY RUN (nothing will be placed)"
    out(f"\nProposed {len(trades)} trades — {mode}:")
    for i, t in enumerate(trades, 1):
        out(f"  {i:2d}. {t.side:4s} {t.quantity:>6d} {t.symbol:6s} "
            f"~${t.est_value:>11,.0f}   {t.reason}")
    out("")

    executed: List[dict] = []
    placed = 0
    for i, t in enumerate(trades, 1):
        if placed >= max_orders:
            out(f"Reached max_orders={max_orders}; stopping."); break
        head = (f"[{i}/{len(trades)}] {t.side} {t.quantity} {t.symbol} "
                f"(~${t.est_value:,.0f}) — {t.reason}")

        if t.est_value > max_order_value:
            out(f"SKIP (guardrail): {head}")
            out(f"  exceeds max_order_value ${max_order_value:,.0f}; split it or raise --max-order-value.")
            continue

        ans = prompt(f"{head}\n  approve / skip / quit? [a/s/q]: ").strip().lower()
        if ans in ("q", "quit"):
            out("Stopping at your request."); break
        if ans not in ("a", "approve", "y", "yes"):
            out("  skipped."); continue

        if not live:
            out("  DRY RUN: approved, not placed.")
            executed.append({"trade": t, "status": "DRY_RUN"}); continue

        typed = prompt(f"  CONFIRM LIVE: type {t.symbol} to place (anything else cancels): ").strip().upper()
        if typed != t.symbol.upper():
            out("  confirmation mismatch; not placed."); continue
        try:
            oid = client.place_market_order(t)
        except Exception as e:                       # noqa: BLE001
            out(f"  ORDER FAILED: {e}"); continue
        placed += 1
        out(f"  PLACED order {oid}.")
        executed.append({"trade": t, "order_id": oid, "status": "PLACED"})
        prompt("  press Enter when you've reviewed the fill to continue...")

    n_placed = sum(1 for e in executed if e.get("status") == "PLACED")
    n_dry = sum(1 for e in executed if e.get("status") == "DRY_RUN")
    out(f"\nDone. {n_placed} placed, {n_dry} approved in dry run, "
        f"{len(trades) - len(executed)} skipped/declined.")
    return executed
