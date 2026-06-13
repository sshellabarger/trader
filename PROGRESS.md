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

---

## 2026-06-13 (session 2) — Disable the SQQQ bear leg + selection-regression diagnosis

### TL;DR
Same data blocker as session 1 (no Alpaca access — `data.alpaca.markets` returns
403 from the sandbox; no API keys; no local bar cache), so again I diagnosed by
replaying the **existing** trade ledgers across all 28 `backtest_results/`
windows rather than running new backtests. Two findings beyond session 1:

1. **The SQQQ bear leg is a structural loser and is now OFF by default**
   (`orb_trade_both_directions=False`). This is the one change shipped this
   session, and it is backed by a *real* coherent backtest, not an estimate.
2. **There is a trade-selection regression in the current ORB code** that is the
   single biggest driver of the −9% H1 result — bigger than the band or the bear
   leg. It needs a data-connected machine to fix. Flagged below, not shipped.

Test suite: **51 passing** (was 48; +3 new). Live/backtest parity preserved — the
change is a config default that flows through `Config.get_trading_symbols()`,
which both the engine and the backtester use.

### Finding 1 — SQQQ bear leg has negative expectancy everywhere
`python -m trader.diagnose_ledger` (new tool) on each coherent slice:

| slice | SQQQ ORB | TQQQ ORB |
|---|---|---|
| full-history union (Jan'25–Mar'26) | n=56, **−$868**, 21% win, PF 0.97 | n=318, **+$24,620**, 53% win, PF 1.46 |
| H1-2025 ledger | n=56, **−$1,135**, 21% win, PF 0.96 | n=56, −$7,600, 21% win, PF 0.72\* |

The bear leg is an **unhedged counter-trend long** — it only wins on down-Nasdaq
days, and 2025–26 drifted up, so it bled. The coherent **TQQQ-only** run
(`trades_2025-01-02_to_2026-03-10.csv`) was **+29.0%, PF 1.64, Sharpe 2.75, max
DD 2.6%** — the strong profile the project is aiming for. So the default trading
set is now TQQQ-only. The capability is preserved/re-enableable
(`orb_trade_both_directions=True`); the right long-term form is to re-enable it
**behind `orb_require_regime_alignment`** so SQQQ trades only in confirmed
bearish regimes — but that combo is unvalidated OOS, so it stays off for now.

\*TQQQ looks bad *in the H1 ledger specifically* because of Finding 2 — that
ledger was produced by the regressed code. In the coherent ledger TQQQ wins.

### Finding 2 — the current ORB code skips the most profitable breakout days
Same TQQQ-ORB strategy, same Jan–Jun 2025 dates, March build vs current build:

- On dates **both** trade, entry/stop/exit are **identical** — only position
  size differs (current ≈1.6× larger, from `margin_multiple` 0.5→2.0).
- But the current build trades only **56** of the 114 TQQQ-ORB days the March
  build took. The **60 days it drops** were **+$15,022, 75% win, PF 5.13** (the
  big winners). The **54 it keeps** were **−$2,914, 35% win, PF 0.75** (losers).
- Range-% does **not** explain the split (dropped vs kept both ~0.7% mean), so
  it is **not** the band filter. The −8.99% ledger predates the band entirely
  (its trades span 0.32–2.46%, outside the 0.5–1.2% band).

So an entry-gate added between March and June (most likely `orb_min_range_bars=3`
interacting with sparse IEX opens, or the `_slice_session` entry-window timing)
is systematically excluding the winning days. This is the top priority and
**cannot be fixed safely without re-running backtests** (need bar data to confirm
which knob recovers winners vs noise).

### Finding 3 — the %-band filter is genuinely good (confirms session 1)
Within the single coherent long file, applying the 0.5–1.2% band raises ORB
**PF 1.87 → 2.07** and win 57.3% → 60.7%, improving *both* halves (H1 1.80→1.82,
H2 1.92→2.30). Keep it. Sizing/leverage (`margin_multiple`) only scales magnitude
and drawdown — it does **not** change PF/win-rate/consistency, which are driven
by trade selection. Left at 2.0 to preserve live RegT-2x parity.

### What shipped this session
- `config.py`: `orb_trade_both_directions = False` (TQQQ-only default) + rationale.
- `tests/test_engine.py`: 3 new tests (default symbols TQQQ-only; bear leg
  re-enables with the flag; engine trades TQQQ-only by default) and the existing
  cross-symbol single-entry test now opts the bear leg back in explicitly.
- `diagnose_ledger.py`: stdlib ledger diagnostic (per-symbol + range buckets).

### Before / after (H1-2025, ledger-replay estimate — NOT a re-run)
| config | est. H1 ORB pnl | note |
|---|---|---|
| both legs (current artifact) | −$8,734 | −8.99% total return |
| TQQQ-only (this change) | −$7,600 on the H1 ledger | removes the −$1,135 SQQQ drag; full upside needs Finding 2 fixed |

The bear-leg removal is a real improvement (coherent TQQQ-only is +29%/PF 1.64),
but H1 will **not** look strong until Finding 2 is fixed — the H1 ledger itself
was generated by the regressed code.

### Remaining risks / next steps (do before Monday, on a data-connected machine)
1. **Root-cause Finding 2 (highest value).** Re-run `python -m trader backtest
   --start 2025-01-02 --end 2025-06-30 --symbol TQQQ` while sweeping
   `orb_min_range_bars` (try 1 and 2) and the `_slice_session` entry window;
   compare trade count and recovered days vs the March ledger. Git history has
   the working March build (commit `4687707` and earlier) to diff against.
2. **Validate this change for real:** backtest + `walkforward` TQQQ-only across
   monthly slices and distinct regimes; confirm +29%/PF 1.64 holds.
3. **Then consider the regime-gated bear leg** (`orb_trade_both_directions=True`
   + `orb_require_regime_alignment=True`) for bear-market robustness — a
   TQQQ-only bot is long-biased and would suffer in a sustained downturn.
4. **Commit a small cached-bars fixture** so backtests run offline/reproducibly
   (would have unblocked both autonomous sessions).
5. Treat Monday's Alpaca paper run as a forward test, not a proven edge.

### Repo / git note
The real repo lives at `trader/.git` (branch **`master`**; the package dir is the
working tree), so this work was committed there. An accidental `git init` at the
project root created a stray `.git` the sandbox mount would not let me delete
(unlink not permitted). It is **not** the real repo — please `rm -rf` the outer
`<project-root>/.git` and `<project-root>/.gitignore` on your Mac if they appear;
the canonical history is under `trader/.git`.
