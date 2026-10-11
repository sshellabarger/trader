"""Advisor CLI: suggest rebalance trades, then review and approve one at a time.

Dry run by default. Read positions live from Schwab, or from a positions CSV for
offline research. Examples:

  # Offline preview from your exported positions (no keys needed):
  python -m trader.advisor.cli --positions-csv "Rollover IRA-Positions.csv"

  # Live, against Schwab, still approving each trade by hand. Export the
  # credentials first (don't inline them in the command — that lands in shell
  # history):
  #   export SCHWAB_API_KEY=...  SCHWAB_APP_SECRET=...
  python -m trader.advisor.cli --live

The default target model is the restructure plan we built: the five mega-caps
capped at 5% (MSFT 3%), the rest in the diversified core. Override with --targets,
and use --band 0.25 for ongoing maintenance instead of an exact rebuild.
"""
from __future__ import annotations

import argparse
import os

from .engine import suggest_trades
from .model import default_targets, load_positions_csv, parse_targets
from .review import review_and_execute


def main():
    p = argparse.ArgumentParser(description="Schwab rebalance advisor (suggest, review, approve one at a time)")
    p.add_argument("--positions-csv", help="Offline: read positions from a CSV instead of Schwab")
    p.add_argument("--targets", default="", help="SYM:weight,... (default = the restructure plan)")
    p.add_argument("--band", type=float, default=0.0, help="Relative drift band; 0 = exact targets (initial build)")
    p.add_argument("--live", action="store_true", help="Place orders (still requires per-trade approval)")
    p.add_argument("--max-order-value", type=float, default=50000.0)
    p.add_argument("--max-orders", type=int, default=25)
    p.add_argument("--min-dollars", type=float, default=200.0)
    # Credentials come from the environment ONLY (SCHWAB_API_KEY /
    # SCHWAB_APP_SECRET). They used to be accepted as --api-key/--app-secret
    # flags, but argv is visible to every process on the machine (ps) and
    # persists in shell history, so the flags were removed.
    p.add_argument("--callback-url", default=os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182"))
    p.add_argument("--token-path", default=os.getenv(
        "SCHWAB_TOKEN_PATH", os.path.expanduser("~/.config/schwab/token.json")))
    p.add_argument("--account-index", type=int, default=0)
    a = p.parse_args()
    a.api_key = os.getenv("SCHWAB_API_KEY")
    a.app_secret = os.getenv("SCHWAB_APP_SECRET")

    targets = parse_targets(a.targets) if a.targets else default_targets()
    client = None

    if a.positions_csv:
        positions, cash = load_positions_csv(a.positions_csv)
        prices = {s: positions[s]["price"] for s in positions}
        print(f"Loaded {len(positions)} positions + ${cash:,.0f} cash from CSV (offline, read-only).")
        print("Note: BUYs of ETFs you don't yet hold can't be sized offline (no price); run --live for those.")
    else:
        if not a.api_key or not a.app_secret:
            print("Set SCHWAB_API_KEY/SCHWAB_APP_SECRET in the environment, or use --positions-csv.")
            return
        # The OAuth refresh token is a long-lived credential: keep it out of
        # the repo, in an owner-only directory.
        token_dir = os.path.dirname(os.path.abspath(a.token_path))
        os.makedirs(token_dir, mode=0o700, exist_ok=True)
        from .schwab_client import SchwabClient
        client = SchwabClient.connect(a.api_key, a.app_secret, a.callback_url, a.token_path, a.account_index)
        positions = client.get_positions()
        cash = client.get_cash()
        prices = {s: positions[s]["price"] for s in positions}
        for s in targets:                          # live-quote any buy-only targets
            if prices.get(s, 0) <= 0:
                try:
                    prices[s] = client.get_quote(s)
                except Exception:                  # noqa: BLE001
                    prices[s] = 0.0
        print(f"Live: {len(positions)} positions + ${cash:,.0f} cash.")

    if a.live and client is None:
        print("Refusing --live without a Schwab connection.")
        return

    trades = suggest_trades(positions, cash, targets, prices,
                            band=a.band, min_dollars=a.min_dollars)
    if not trades:
        print("No trades suggested (already within target and bands).")
        return
    review_and_execute(trades, client, live=a.live,
                       max_order_value=a.max_order_value, max_orders=a.max_orders)


if __name__ == "__main__":
    main()
