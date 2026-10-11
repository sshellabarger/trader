"""Offline tests for the advisor: suggestion engine + the human approval gate.
The critical guarantee under test: no order is ever placed without an explicit
approval AND a matching ticker confirmation, and guardrails block oversized orders.
"""
from trader.advisor.engine import Trade, suggest_trades
from trader.advisor.review import review_and_execute


# ----------------------------- engine -----------------------------

def test_exit_trim_and_buy():
    positions = {"AMZN": {"qty": 100, "value": 100000, "price": 1000},
                 "JUNK": {"qty": 50, "value": 5000, "price": 100}}
    targets = {"AMZN": 0.05, "SPY": 0.5}        # trim AMZN, exit JUNK, buy SPY
    prices = {"AMZN": 1000, "JUNK": 100, "SPY": 500}
    tr = suggest_trades(positions, 0.0, targets, prices, band=0.0, min_dollars=100)
    d = {t.symbol: t for t in tr}
    assert d["JUNK"].side == "SELL" and d["JUNK"].quantity == 50      # full exit
    assert d["AMZN"].side == "SELL"                                    # trim
    assert d["SPY"].side == "BUY"
    assert tr[0].side == "SELL" and tr[-1].side == "BUY"              # sells first


def test_band_suppresses_small_drift():
    positions = {"SPY": {"qty": 100, "value": 50000, "price": 500}}
    tr = suggest_trades(positions, 50000.0, {"SPY": 0.5}, {"SPY": 500},
                        band=0.25, min_dollars=100)
    assert tr == []                                                   # at target within band


def test_sell_caps_at_held_quantity():
    positions = {"X": {"qty": 3, "value": 300, "price": 100}}
    tr = suggest_trades(positions, 0.0, {"X": 0.0}, {"X": 100}, min_dollars=50)
    assert tr[0].side == "SELL" and tr[0].quantity == 3


# ----------------------------- approval gate -----------------------------

class FakeClient:
    def __init__(self):
        self.placed = []

    def place_market_order(self, t):
        self.placed.append(t)
        return f"OID{len(self.placed)}"


def _answers(seq):
    it = iter(seq)
    return lambda prompt="": next(it)


def test_dry_run_never_places():
    fc = FakeClient()
    review_and_execute([Trade("SPY", "BUY", 10, "x", 500)], fc, live=False,
                       prompt=_answers(["a"]), out=lambda *_: None)
    assert fc.placed == []


def test_live_requires_correct_ticker_confirmation():
    fc = FakeClient()                                  # approve, then WRONG ticker
    review_and_execute([Trade("SPY", "BUY", 10, "x", 5000)], fc, live=True,
                       prompt=_answers(["a", "WRONG"]), out=lambda *_: None)
    assert fc.placed == []

    fc2 = FakeClient()                                 # approve, correct ticker, Enter
    review_and_execute([Trade("SPY", "BUY", 10, "x", 5000)], fc2, live=True,
                       prompt=_answers(["a", "SPY", ""]), out=lambda *_: None)
    assert len(fc2.placed) == 1


def test_skip_and_quit_place_nothing():
    fc = FakeClient()
    review_and_execute([Trade("SPY", "BUY", 1, "x", 100), Trade("IEF", "BUY", 1, "x", 100)],
                       fc, live=True, prompt=_answers(["s", "q"]), out=lambda *_: None)
    assert fc.placed == []


def test_guardrail_blocks_oversized_order():
    fc = FakeClient()                                  # $1,000,000 > default 50k cap
    review_and_execute([Trade("SPY", "BUY", 1000, "x", 1000)], fc, live=True,
                       prompt=_answers(["a", "SPY", ""]), out=lambda *_: None)
    assert fc.placed == []                             # skipped before any prompt
