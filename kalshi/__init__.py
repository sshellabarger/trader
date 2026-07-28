"""
Kalshi sleeve — phase 0 (measurement only, no live orders).

Records prediction-market data so a fair-value model can be scored against
the market BEFORE any capital is committed. The phase-0 gate (see project
notes, 2026-07-28): a sleeve is funded only if, over >=30 settled paper
trades, PF >= 1.3 after fees under conservative fills AND the model's Brier
score beats the market price's own Brier score.

Components:
  config    → KalshiConfig (env-driven, mirrors the Alpaca BrokerConfig style)
  client    → REST client for api.elections.kalshi.com/trade-api/v2.
              Market data is public (no auth). Trading/portfolio endpoints
              use RSA-PSS request signing and are scaffolded for later phases;
              they are exercised against the demo exchange first.
  recorder  → 24/7 JSONL snapshot recorder (markets + top-of-book + order
              books near close + daily settlement sweep). Runs as its own
              container next to the stock bot: `python -m trader kalshi-record`.

Data lands in data/kalshi/ (bind-mounted on the droplet):
  snapshots-YYYYMMDD.jsonl  one line per market per poll ("md"), plus "book"
                            lines for markets near close
  settlements.jsonl         one line per settled market ("settle"), deduped

Prices are integer CENTS (Kalshi native). Nothing here places orders.
"""

__all__ = ["config", "client", "recorder"]
