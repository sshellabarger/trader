"""
Long-bias sleeve picks — the 07-06→07-17 lesson.

The sleeve trades LONG opening-range breakouts only, but the scanner ranked
movers by |gap|×rvol in BOTH directions, so weeks dominated by gap-downs
produced 45 name-days and a single entry. These tests pin the fix:
  - scan_candidates(long_bias=True) ranks every gap-up ahead of every
    gap-down (score order within groups) BEFORE the max_candidates trim, so
    a tradeable gap-up can't be pushed out by higher-scoring gap-downs;
  - the engine trades only non-negative gaps when the bias is on, and
    diary-notes displaced gap-downs as gap_down_long_only;
  - STOCK_SLEEVE_LONG_BIAS=false restores the old direction-agnostic picks.
"""
from trader.config import ScannerConfig
from trader.scanner import Candidate, scan_candidates

from test_engine import FakeBroker, make_engine


# ---------------------------------------------------------------------------
# Scanner ordering
# ---------------------------------------------------------------------------

def _snap(open_, prev_close, rvol=1.0):
    prev_vol = 1_000_000
    return {
        "latestTrade": {"p": open_},
        "dailyBar": {"o": open_, "h": open_ * 1.01, "l": open_ * 0.99,
                     "v": prev_vol * rvol},
        "prevDailyBar": {"c": prev_close, "v": prev_vol},
    }


class SnapBroker:
    def __init__(self, snaps):
        self.snaps = snaps

    def get_snapshots(self, symbols):
        return self.snaps


SNAPS = {
    # gap-ups: scores 2.0 and 1.0
    "UP_BIG": _snap(102.0, 100.0, rvol=1.0),    # +2.0% × 1.0 → 2.0
    "UP_SM": _snap(101.0, 100.0, rvol=1.0),     # +1.0% × 1.0 → 1.0
    # gap-downs: scores 10.0 and 4.5 (out-score both ups)
    "DN_BIG": _snap(95.0, 100.0, rvol=2.0),     # -5.0% × 2.0 → 10.0
    "DN_SM": _snap(97.0, 100.0, rvol=1.5),      # -3.0% × 1.5 → 4.5
}


def test_long_bias_ranks_all_ups_before_all_downs():
    got = scan_candidates(SnapBroker(SNAPS), list(SNAPS), ScannerConfig(),
                          long_bias=True)
    assert [c.symbol for c in got] == ["UP_BIG", "UP_SM", "DN_BIG", "DN_SM"]


def test_default_order_is_direction_agnostic():
    got = scan_candidates(SnapBroker(SNAPS), list(SNAPS), ScannerConfig())
    assert [c.symbol for c in got] == ["DN_BIG", "DN_SM", "UP_BIG", "UP_SM"]


def test_bias_applies_before_the_trim():
    # With max_candidates=2 the old sort would return two gap-downs and the
    # tradeable gap-up would never reach the engine at all.
    cfg = ScannerConfig(max_candidates=2)
    biased = scan_candidates(SnapBroker(SNAPS), list(SNAPS), cfg, long_bias=True)
    assert [c.symbol for c in biased] == ["UP_BIG", "UP_SM"]
    plain = scan_candidates(SnapBroker(SNAPS), list(SNAPS), cfg)
    assert [c.symbol for c in plain] == ["DN_BIG", "DN_SM"]


# ---------------------------------------------------------------------------
# Engine pick filtering + diary notes
# ---------------------------------------------------------------------------

def _cand(symbol, gap, rvol):
    price = 100.0 * (1 + gap / 100)
    return Candidate(symbol=symbol, price=price, prev_close=100.0, gap_pct=gap,
                     change_pct=gap, volume=1e6 * rvol, avg_volume=1e6,
                     relative_volume=rvol, high=price, low=price,
                     open_price=price)


MIXED = [_cand("UP_BIG", 2.0, 1.0), _cand("UP_SM", 1.0, 1.0),
         _cand("DN_BIG", -5.0, 2.0), _cand("DN_SM", -3.0, 1.5)]


def test_engine_trades_gap_ups_only_and_notes_displaced_downs(monkeypatch):
    engine = make_engine(FakeBroker(), stock_sleeve_enabled=True)
    monkeypatch.setattr("trader.engine.scan_candidates",
                        lambda *a, **k: list(MIXED))

    engine._ensure_sleeve_symbols()

    assert engine.symbols == ["UP_BIG", "UP_SM"]          # longs only
    noted = {(s["symbol"], s["stage"]) for s in engine.journal.skips}
    assert ("DN_BIG", "gap_down_long_only") in noted      # displaced, visible
    assert ("DN_SM", "gap_down_long_only") in noted


def test_engine_kill_switch_restores_direction_agnostic_picks(monkeypatch):
    engine = make_engine(FakeBroker(), stock_sleeve_enabled=True,
                         stock_sleeve_long_bias=False)
    # Old behavior consumed the scanner's own (score-sorted) order.
    ordered = [MIXED[2], MIXED[3], MIXED[0], MIXED[1]]
    monkeypatch.setattr("trader.engine.scan_candidates",
                        lambda *a, **k: list(ordered))

    engine._ensure_sleeve_symbols()

    assert engine.symbols == ["DN_BIG", "DN_SM", "UP_BIG", "UP_SM"]
    assert not engine.journal.skips
