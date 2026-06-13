"""
Coverage for VWAP reversion (previously untested; can only fire with >=30 bars):
  - applies_to restricts it to the bull instrument (the regime filter is keyed
    to QQQ, so running it on the inverse ETF would invert the protection).
  - a genuine oversold-below-VWAP setup yields a coherent LONG (stop < entry <= tp).
  - the bearish-regime filter blocks that same setup (falling-knife protection).
  - exits trigger when price reverts to/above VWAP.
"""
from trader.config import Config
from trader.indicators import compute_indicators
from trader.scanner import Candidate
from trader.strategies import SignalAction, SignalDirection
from trader.strategies.vwap_reversion import VWAPReversionStrategy


def bars_from_closes(closes, v=10_000):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(o, c) + 0.05
        lo = min(o, c) - 0.05
        out.append({"t": f"2025-01-15T{14 + i // 60:02d}:{(30 + i) % 60:02d}:00Z",
                    "o": o, "h": hi, "l": lo, "c": c, "v": v})
    return out


# Gentle rise (small gains) then a sustained drop → oversold, below VWAP, near
# the lower band. Small prior gains keep RSI from being propped up.
OVERSOLD_CLOSES = (
    [70.0 + 0.01 * i for i in range(25)]                      # 70.00 -> 70.24
    + [70.20, 70.05, 69.85, 69.60, 69.30, 69.00,
       68.70, 68.45, 68.25, 68.10, 68.00, 67.95]             # sharp decline
)


def make_candidate(bars, symbol="TQQQ"):
    return Candidate(
        symbol=symbol, price=float(bars[-1]["c"]),
        prev_close=float(bars[0]["o"]), gap_pct=0, change_pct=0,
        volume=float(bars[-1]["v"]), avg_volume=float(bars[0]["v"]),
        relative_volume=1.5,
        high=max(float(b["h"]) for b in bars),
        low=min(float(b["l"]) for b in bars),
        open_price=float(bars[0]["o"]),
    )


def make_strategy():
    return VWAPReversionStrategy(Config())


def test_applies_only_to_bull_instrument():
    strat = make_strategy()
    assert strat.applies_to("TQQQ") is True      # leveraged_bull
    assert strat.applies_to("SQQQ") is False     # inverse ETF excluded


def test_needs_thirty_bars():
    bars = bars_from_closes([70.0 - 0.05 * i for i in range(20)])
    assert make_strategy().evaluate(make_candidate(bars), bars, compute_indicators(bars)) is None


def test_oversold_setup_produces_coherent_long():
    bars = bars_from_closes(OVERSOLD_CLOSES)
    ind = compute_indicators(bars)
    strat = make_strategy()
    strat.set_market_regime("bullish")
    signal = strat.evaluate(make_candidate(bars), bars, ind)

    assert signal is not None, f"expected entry; rsi={ind['rsi_14']}, dev={ind['vwap_deviation_pct']}, %b={ind['bb_pct_b']}"
    assert signal.direction == SignalDirection.LONG
    assert signal.action == SignalAction.ENTER
    assert signal.stop_loss < signal.entry_price <= signal.take_profit
    assert signal.risk_reward is not None and signal.risk_reward >= 1.0


def test_bearish_regime_blocks_entry():
    bars = bars_from_closes(OVERSOLD_CLOSES)
    ind = compute_indicators(bars)
    strat = make_strategy()
    strat.set_market_regime("bearish")           # don't catch falling knives
    assert strat.evaluate(make_candidate(bars), bars, ind) is None


def test_exit_when_price_reverts_to_vwap():
    bars = bars_from_closes(OVERSOLD_CLOSES)
    ind = compute_indicators(bars)
    strat = make_strategy()
    strat.set_market_regime("bullish")
    entry = strat.evaluate(make_candidate(bars), bars, ind)
    assert entry is not None
    strat.on_fill(entry.symbol, entry)

    # Price snaps back above VWAP → reversion target reached → EXIT.
    vwap_val = ind["vwap"]
    reverted = make_candidate(bars)
    reverted.price = vwap_val + 0.5
    position = {"symbol": "TQQQ", "current_price": reverted.price,
                "avg_entry_price": entry.entry_price}
    exit_sig = strat.evaluate(reverted, bars, ind, position)

    assert exit_sig is not None
    assert exit_sig.action == SignalAction.EXIT
