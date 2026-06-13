"""
Regression tests for the ORB strategy (June 2026 rewrite):
  - LONG-ONLY: a bullish opening range → long; a bearish/flat range → no trade
    on that instrument (down days are captured by going long SQQQ, whose own
    opening range breaks UP — exercised in the backtest tests).
  - opening range located by timestamp, so premarket bars and DST cannot skew
    it (previously bars[:5], i.e. 4:00 AM premarket bars live).
  - the day's single ORB signal is consumed on_fill, NOT at generation, so a
    rejected/unfilled order does not forfeit the day.
  - a sparse (<min_range_bars) opening range produces no signal.
  - signals never substitute a different symbol than the bars analyzed.
"""
from trader.config import Config
from trader.scanner import Candidate
from trader.strategies import SignalDirection
from trader.strategies.orb import ORBStrategy


def make_bar(ts, o, h, l, c, v=10_000):
    return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def minute_bars(date_str, start_hh, start_mm, prices):
    """Consecutive 1-min UTC bars starting at start_hh:start_mm."""
    bars = []
    for i, (o, h, l, c) in enumerate(prices):
        total = start_mm + i
        ts = f"{date_str}T{start_hh + total // 60:02d}:{total % 60:02d}:00Z"
        bars.append(make_bar(ts, o, h, l, c))
    return bars


# 9:30 ET on 2025-01-15 is 14:30 UTC (EST)
WINTER = ("2025-01-15", 14, 30)
# 9:30 ET on 2025-07-15 is 13:30 UTC (EDT)
SUMMER = ("2025-07-15", 13, 30)

BULL_RANGE = [
    (70.0, 70.2, 69.9, 70.1),
    (70.1, 70.3, 70.0, 70.2),
    (70.2, 70.4, 70.1, 70.3),
    (70.3, 70.5, 70.2, 70.4),
    (70.4, 70.6, 70.3, 70.5),  # range: open 70.0 -> close 70.5, high 70.6, low 69.9
]
BULL_ENTRY = (70.5, 70.8, 70.5, 70.7)  # entry bar close 70.7

BEAR_RANGE = [
    (70.0, 70.1, 69.8, 69.9),
    (69.9, 70.0, 69.7, 69.8),
    (69.8, 69.9, 69.6, 69.7),
    (69.7, 69.8, 69.5, 69.6),
    (69.6, 69.7, 69.4, 69.5),  # range: open 70.0 -> close 69.5 (down)
]
BEAR_ENTRY = (69.5, 69.6, 69.2, 69.3)


def build_bars(session, prices):
    date_str, hh, mm = session
    return minute_bars(date_str, hh, mm, prices)


def make_candidate(bars, symbol="TQQQ"):
    return Candidate(
        symbol=symbol,
        price=float(bars[-1]["c"]),
        prev_close=float(bars[0]["o"]),
        gap_pct=0, change_pct=0,
        volume=float(bars[-1]["v"]), avg_volume=float(bars[0]["v"]),
        relative_volume=1,
        high=max(float(b["h"]) for b in bars),
        low=min(float(b["l"]) for b in bars),
        open_price=float(bars[0]["o"]),
    )


def make_strategy(**overrides):
    config = Config()
    for k, v in overrides.items():
        setattr(config.strategy, k, v)
    return ORBStrategy(config)


def test_bullish_range_goes_long():
    bars = build_bars(WINTER, BULL_RANGE + [BULL_ENTRY])
    signal = make_strategy().evaluate(make_candidate(bars), bars, {})

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.entry_price == 70.7
    assert signal.stop_loss == 69.9            # range low
    assert signal.stop_loss < signal.entry_price < signal.take_profit
    # tp = entry + 10 * (entry - stop) = 70.7 + 8.0
    assert abs(signal.take_profit - 78.7) < 1e-9


def test_bearish_range_produces_no_signal():
    # ORB is long-only; a down-open is handled by going long the inverse ETF,
    # not by shorting this symbol.
    bars = build_bars(WINTER, BEAR_RANGE + [BEAR_ENTRY])
    assert make_strategy().evaluate(make_candidate(bars), bars, {}) is None


def test_inverse_etf_bullish_open_goes_long():
    # On a down-Nasdaq day SQQQ's own opening range breaks UP -> long SQQQ.
    bars = build_bars(WINTER, BULL_RANGE + [BULL_ENTRY])
    signal = make_strategy().evaluate(make_candidate(bars, symbol="SQQQ"), bars, {})

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.symbol == "SQQQ"             # prices in SQQQ's own terms


def test_signal_symbol_matches_analyzed_bars():
    bars = build_bars(WINTER, BULL_RANGE + [BULL_ENTRY])
    signal = make_strategy(use_leveraged=True).evaluate(
        make_candidate(bars, symbol="TQQQ"), bars, {}
    )
    assert signal is not None
    assert signal.symbol == "TQQQ"             # no silent substitution


def test_premarket_bars_do_not_skew_opening_range():
    # 4:00-4:09 AM ET premarket bars (09:00 UTC in winter) with wild prices
    premarket = minute_bars("2025-01-15", 9, 0, [(50.0, 100.0, 40.0, 60.0)] * 10)
    session = build_bars(WINTER, BULL_RANGE + [BULL_ENTRY])

    clean = make_strategy().evaluate(make_candidate(session), session, {})
    with_pm = make_strategy().evaluate(
        make_candidate(premarket + session), premarket + session, {}
    )

    assert clean is not None and with_pm is not None
    assert with_pm.stop_loss == clean.stop_loss
    assert with_pm.take_profit == clean.take_profit


def test_summer_timestamps_use_edt_session_open():
    # Same pattern stamped 13:30 UTC = 9:30 EDT; under the old hardcoded
    # -05:00 assumption this day would be misaligned.
    bars = build_bars(SUMMER, BULL_RANGE + [BULL_ENTRY])
    signal = make_strategy().evaluate(make_candidate(bars), bars, {})
    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.stop_loss == 69.9


def test_signal_not_consumed_until_on_fill():
    bars = build_bars(WINTER, BULL_RANGE + [BULL_ENTRY])
    strat = make_strategy()

    # Re-evaluating without a fill keeps offering the signal (so a rejected
    # order can retry on the next tick within the entry window).
    first = strat.evaluate(make_candidate(bars), bars, {})
    second = strat.evaluate(make_candidate(bars), bars, {})
    assert first is not None and second is not None

    # Once an order fills, the day's ORB signal is consumed.
    strat.on_fill(first.symbol, first)
    assert strat.evaluate(make_candidate(bars), bars, {}) is None

    # New day resets it.
    strat.reset_daily()
    assert strat.evaluate(make_candidate(bars), bars, {}) is not None


def test_sparse_range_below_min_bars_no_signal():
    # Only two bars fall inside the 09:30-09:35 window (09:30 and 09:33),
    # then a 09:35 entry bar. With orb_min_range_bars=3 this is too sparse.
    bars = [
        make_bar("2025-01-15T14:30:00Z", 70.0, 70.6, 69.9, 70.5),
        make_bar("2025-01-15T14:33:00Z", 70.5, 70.7, 70.4, 70.6),
        make_bar("2025-01-15T14:35:00Z", 70.6, 70.9, 70.6, 70.8),  # entry bar
    ]
    assert make_strategy().evaluate(make_candidate(bars), bars, {}) is None

    # Lowering the requirement to 2 lets the same data through.
    assert make_strategy(orb_min_range_bars=2).evaluate(
        make_candidate(bars), bars, {}
    ) is not None


def test_no_entry_before_range_completes():
    bars = build_bars(WINTER, BULL_RANGE)  # last bar still inside the range
    assert make_strategy().evaluate(make_candidate(bars), bars, {}) is None


def test_no_entry_after_window_closes():
    # Latest bar at 9:40 ET, past the 9:35 + 3min entry window
    filler = [(70.5, 70.6, 70.4, 70.5)] * 5
    bars = build_bars(WINTER, BULL_RANGE + filler)
    assert make_strategy().evaluate(make_candidate(bars), bars, {}) is None


def test_naive_timestamps_fail_closed():
    # No timezone info -> the strategy must not guess; produce no signal.
    bars = [make_bar("2025-01-15 14:3%d:00" % i, 70.0, 70.6, 69.9, 70.5) for i in range(6)]
    assert make_strategy().evaluate(make_candidate(bars), bars, {}) is None
