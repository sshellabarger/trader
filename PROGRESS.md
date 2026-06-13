# Trader — Progress Log

## 2026-06-13 — ORB opening-range %-band filter (autonomous session)

### TL;DR
Added a configurable opening-range size filter to the ORB strategy. It removes
the two trade types that bled money in H1-2025 — sub-noise ranges and oversized
whipsaw ranges — and flips the H1-2025 result from **-9.0% (PF 0.87)** to
**+8.3% (PF 1.22)** with the default band, or **+14.5% (PF 1.52)** with the
tighter in-sample-optimal band. Test suite stays green (48 passing).

**Important caveat:** I could not re-run the live backtest in this session — the
sandbox has no network access to Alpaca market data (`data.alpaca.markets` is
blocked). All numbers below come from replaying the **existing** H1-2025 trade
ledger (`backtest_results/trades_2025-01-02_to_2025-06-30.csv`) through the exact
new filter logic (see `validate_range_filter.py`). This is a conservative lower
bound for a trade-removal filter, but it is **not** a substitute for a real
backtest + walk-forward across multiple windows. Please run those on your Mac
before trusting it (commands at the bottom).

### Baseline (H1-2025, current code, from the stated artifact)
| metric | value |
|---|---|
| return | -8.99% |
| win rate | 24.8% |
| profit factor | 0.87 |
| Sharpe | -0.75 |
| max DD | 17.4% |
| trades | 153 |

### Diagnosis
Exit-reason breakdown of the H1-2025 ledger:
- `stop_loss`: n=114, **-$67,279** (the entire bleed)
- `eod_close`: n=22, **+$38,160** (avg hold 380 min — full-day winners)
- `take_profit`: n=10, +$13,469
- `vwap target`: n=7, +$6,657

The system makes money when a trade survives to ride the move (EOD/TP) and loses
when it gets stopped (avg stop hold only 40 min — shaken out early). Avg win
$1,539 vs avg loss $587 means breakeven needs ~27.6% win rate; baseline was
24.8%, just under. So the fix is to stop taking the worst trades, not to chase
more winners.

Sorting ORB trades by opening-range size as a % of price showed a clean,
monotonic, mechanistically-sensible pattern:

| range % of price | n | P&L | win% |
|---|---|---|---|
| < 0.5% (noise, no real breakout) | 11 | **-$3,228** | 0% |
| 0.5–1.0% (the edge) | ~42 | **+$14k** | ~30% |
| 1.0–1.2% | 18 | -$6,174 | 22% |
| 1.2–1.5% | 18 | -$4,971 | 22% |
| 1.5–2.0% | 9 | -$3,426 | 22% |
| ≥ 2.0% | 4 | -$5,688 | 0% |

Tiny ranges aren't breakouts; oversized ranges are high-volatility / news days
where the breakout reverses and blows through the range-low stop.

### Change made
`trader/strategies/orb.py` — after the existing dollar/ATR range checks, reject
a setup whose opening range (high-low) is outside a %-of-price band:
- `orb_min_range_pct` (default **0.5**) — floor, drops noise.
- `orb_max_range_pct` (default **1.2**, set 0 to disable) — cap, drops whipsaw.

New config knobs in `trader/config.py`:
- `orb_min_range_pct: float = 0.5`
- `orb_max_range_pct: float = 1.2`
- `orb_require_regime_alignment: bool = False` — optional: only buy the bull ETF
  in a bullish QQQ regime / bear ETF in a bearish regime. **Default off** (left
  inert because I couldn't validate it offline). Worth A/B testing on your Mac.

Parity: the live engine and the backtester share the same `ORBStrategy.evaluate`
and both feed regime identically, so the filter applies to live and backtest the
same way by construction. No engine-side duplication.

### Result (ledger replay, H1-2025)
| config | trades | P&L | win% | PF | avg win | avg loss |
|---|---|---|---|---|---|---|
| baseline (no filter) | 153 | -$8,993 | 24.8% | 0.87 | +$1,539 | -$587 |
| **default 0.5–1.2%** | 111 | **+$8,319** | 28.8% | **1.22** | +$1,464 | -$488 |
| aggressive 0.5–1.0% | 93 | +$14,493 | 30.1% | 1.52 | +$1,521 | -$432 |

Avg win barely moves while avg loss shrinks — we're cutting losers, not winners.

### What's working / what's not (be skeptical)
- **Working:** the filter removes the stop-loss bleed and is economically
  motivated, not just a fitted threshold. The floor (drop <0.5%) is robust and
  low-risk on its own.
- **Not yet consistent:** even filtered, H1-2025 profit is concentrated in
  **February 2025 (+$14.5k)**. Jan/May/Jun 2025 are still net-negative after
  filtering. This is *not yet* a "strong, consistent" edge — it is a clear
  improvement on a strategy that was losing.
- **Overfitting risk:** the upper cap is the dominant lever and is tuned on one
  window. Replaying the filter against older ledgers in `backtest_results/`
  (different code versions, so only weak evidence) showed mixed results — some
  late-2025 windows flipped positive→negative under the tight 1.0% cap. That is
  why the default is the looser, more robust **1.2%** rather than the
  in-sample-optimal 1.0%.

### Remaining risks / recommended next steps
1. **Run the real validation on a data-connected machine before Monday:**
   - `python -m trader backtest --start 2025-01-02 --end 2025-06-30`
   - Monthly slices + a couple of distinct regimes to confirm consistency.
   - `python -m trader walkforward --start 2025-01-02 --end 2025-06-30 --symbol TQQQ`
   - Confirm the live numbers match this ledger replay.
2. **Treat Monday's Alpaca paper run as a forward test, not a proven edge.** It
   is the right venue to forward-validate an in-sample filter at zero risk.
3. **The stop-out problem is only half-solved.** The deeper issue (good trades
   stopped early in choppy months) needs exit-side work — trailing/breakeven
   stops, time-based exits, or an ATR-scaled stop. I did **not** ship those
   because they can't be validated without bar data and could easily make things
   worse. Prototype and backtest them on your Mac.
4. **Try the optional knobs:** `orb_require_regime_alignment=True`, and tighten
   `orb_max_range_pct` to 1.0 if the paper/backtest results support it.
5. Consider committing a small cached-bars fixture so backtests are reproducible
   offline (would have let this session validate properly).

### Tooling added
`trader/validate_range_filter.py` — replays any `trades_*.csv` through the live
filter and prints before/after metrics. Run: `python -m trader.validate_range_filter`.
