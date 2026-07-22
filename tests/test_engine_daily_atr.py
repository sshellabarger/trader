"""Live-engine wiring for the ATR-scaled ORB range band.

The sweep validated the band in the REPLAY; these tests pin that the LIVE
engine feeds the same daily_atr_pct number to the same strategy gate:
  - after the morning scan, each pick's daily ATR% is fetched and computed
    with the shared indicators.daily_atr_pct (no lookahead);
  - _generate_signals injects it per symbol;
  - fetch failures and the flag being off are fail-open (fixed-band fallback,
    zero extra API calls when disabled).
"""
import datetime as _dt

import pytest

from test_engine import FakeBroker, make_engine
from test_sleeve_backtest import make_dailies, make_rth_bars, DAY

from trader.indicators import daily_atr_pct


TODAY = _dt.date.today().isoformat()


class SleeveBroker(FakeBroker):
    """Routes get_bars by timeframe and answers the morning snapshot scan."""

    def __init__(self, daily_by_symbol=None, snapshots=None, **kw):
        super().__init__(**kw)
        self.daily_by_symbol = daily_by_symbol or {}
        self.snapshots = snapshots or {}
        self.daily_calls = []
        self.fail_daily = False

    def get_bars(self, symbol, **kwargs):
        if kwargs.get("timeframe") == "1Day":
            self.daily_calls.append(symbol)
            if self.fail_daily:
                raise RuntimeError("daily fetch down")
            return list(self.daily_by_symbol.get(symbol, []))
        return super().get_bars(symbol, **kwargs)

    def get_snapshots(self, symbols):
        return {s: self.snapshots[s] for s in symbols if s in self.snapshots}


def _gap_up_snapshot(price=103.0, prev_close=100.0):
    return {
        "latestTrade": {"p": price},
        "dailyBar": {"o": price, "h": price + 0.5, "l": price - 0.5,
                     "c": price, "v": 600_000},
        "prevDailyBar": {"c": prev_close, "v": 1_000_000},
        "minuteBar": {"c": price},
    }


class RecorderStrategy:
    """Minimal strategy double that captures the indicators it is handed."""
    name = "recorder"

    def __init__(self):
        self.seen = {}

    def applies_to(self, symbol):
        return True

    def evaluate(self, candidate, bars, indicators, position=None):
        self.seen[candidate.symbol] = dict(indicators)
        return None

    def reset_daily(self):
        pass

    def set_market_regime(self, regime):
        pass


def _sleeve_engine(broker, **overrides):
    engine = make_engine(
        broker,
        stock_sleeve_enabled=True,
        stock_sleeve_symbols="AAA",
        orb_range_band_atr=True,
        **overrides,
    )
    return engine


def test_scan_fetches_and_injects_daily_atr():
    dailies = make_dailies(DAY, 100.0, 103.0)   # 20 flat days + a gap day
    broker = SleeveBroker(
        daily_by_symbol={"AAA": dailies},
        snapshots={"AAA": _gap_up_snapshot()},
        bars_by_symbol={"AAA": make_rth_bars(DAY)[:30]},
    )
    engine = _sleeve_engine(broker)

    engine._ensure_sleeve_symbols()
    assert engine.symbols == ["AAA"]
    assert broker.daily_calls == ["AAA"]

    # Same number the shared helper computes for the scan day — the engine
    # must not invent its own ATR math.
    expected = daily_atr_pct(dailies, TODAY)
    assert engine._daily_atr_pct["AAA"] == pytest.approx(expected)

    rec = RecorderStrategy()
    engine.strategies = [rec]
    # Seed the bar cache directly: _get_bars gates on the wall clock (returns
    # [] before 09:30 ET), which would make this test pass or fail by time of
    # day. The injection path under test starts after bars exist.
    engine._bars_cache["AAA"] = make_rth_bars(DAY)[:30]
    engine._generate_signals()
    assert rec.seen["AAA"]["daily_atr_pct"] == pytest.approx(expected)
    # Sleeve names never get the QQQ overnight gap (gate stays inert).
    assert rec.seen["AAA"]["overnight_gap_pct"] is None


def test_daily_atr_fetch_failure_is_fail_open():
    broker = SleeveBroker(
        daily_by_symbol={},
        snapshots={"AAA": _gap_up_snapshot()},
        bars_by_symbol={"AAA": make_rth_bars(DAY)[:30]},
    )
    broker.fail_daily = True
    engine = _sleeve_engine(broker)

    engine._ensure_sleeve_symbols()          # must not raise
    assert engine.symbols == ["AAA"]
    assert engine._daily_atr_pct == {}

    rec = RecorderStrategy()
    engine.strategies = [rec]
    engine._bars_cache["AAA"] = make_rth_bars(DAY)[:30]   # clock-independent
    engine._generate_signals()
    assert rec.seen["AAA"]["daily_atr_pct"] is None   # fixed-band fallback


def test_no_daily_fetch_when_band_disabled():
    broker = SleeveBroker(
        daily_by_symbol={"AAA": make_dailies(DAY, 100.0, 103.0)},
        snapshots={"AAA": _gap_up_snapshot()},
    )
    engine = make_engine(broker, stock_sleeve_enabled=True,
                         stock_sleeve_symbols="AAA",
                         orb_range_band_atr=False)
    engine._ensure_sleeve_symbols()
    assert engine.symbols == ["AAA"]
    assert broker.daily_calls == []          # zero extra API traffic
    assert engine._daily_atr_pct == {}


def test_index_mode_untouched():
    broker = SleeveBroker(daily_by_symbol={"TQQQ": make_dailies(DAY, 70.0, 70.5)})
    engine = make_engine(broker, orb_range_band_atr=True)   # sleeve OFF
    assert engine.sleeve_enabled is False
    engine._refresh_daily_atr(TODAY)
    assert broker.daily_calls == []
    assert engine._daily_atr_pct == {}
