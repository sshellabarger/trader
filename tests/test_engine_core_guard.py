"""
Engine + held core coexistence (2026-10-11):
  - the EOD flatten closes only the engine's positions and leaves core
    holdings on the book; with no core holdings the account-wide path is kept;
  - the sweep never re-closes a core holding;
  - core holdings are invisible to the engine's position views;
  - intraday sizing uses equity minus the core, capped at the core's complement;
  - sleeve scanning drops core names from the universe;
  - a failed startup account read pages (and the old fail-fast is opt-in).
"""
import datetime as _dt

import pytest
from test_engine import FakeBroker, make_engine

from trader.core import save_state


def _pos(sym, qty, price):
    return {"symbol": sym, "qty": str(qty), "avg_entry_price": str(price),
            "current_price": str(price), "market_value": str(qty * price),
            "unrealized_pl": "0", "unrealized_plpc": "0"}


class _Notes:
    def __init__(self):
        self.sent = []

    def alert(self, subject, body):
        self.sent.append(("alert", subject))
        return {}

    def send(self, subject, body, **kw):
        self.sent.append(("send", subject))
        return {}


def _core_engine(broker, tmp_path, enabled=True, holdings=None):
    engine = make_engine(broker)
    engine.config.core.enabled = enabled
    engine.config.core.alloc_pct = 70
    engine.config.core.state_file = str(tmp_path / "core_state.json")
    save_state(engine.config.core.state_file, {"holdings": holdings or {}})
    engine.core.cfg = engine.config.core
    engine.core.reload_state()
    engine.notifier = _Notes()
    engine._today = _dt.date.today().isoformat()      # skip _new_day
    return engine


def test_flatten_leaves_core_holdings(tmp_path):
    broker = FakeBroker(positions=[_pos("SPY", 50, 500.0), _pos("TQQQ", 100, 70.0)])
    broker.minutes_until_close = lambda: 3.0
    engine = _core_engine(broker, tmp_path, holdings={"SPY": 50})
    engine.journal.open_trade("TQQQ", "orb", "long", 100, entry_price=70.0,
                              stop_loss=69.0, take_profit=72.0)

    engine._tick()

    assert broker.close_all_called == []          # no account-wide liquidation
    assert broker.closed == ["TQQQ"]              # only the engine's position
    assert broker.cancelled == ["TQQQ"]
    assert engine._flatten_requested is True


def test_sweep_ignores_core_holdings(tmp_path):
    broker = FakeBroker(positions=[_pos("SPY", 50, 500.0)])
    broker.minutes_until_close = lambda: 3.0
    engine = _core_engine(broker, tmp_path, holdings={"SPY": 50})
    engine._flatten_requested = True

    engine._sweep_unflattened()

    assert broker.closed == [] and broker.cancelled == []


def test_flatten_without_core_uses_close_all(tmp_path):
    broker = FakeBroker(positions=[_pos("TQQQ", 100, 70.0)])
    broker.minutes_until_close = lambda: 3.0
    engine = _core_engine(broker, tmp_path, holdings={})
    engine.config.core.enabled = False           # model names not protected either

    engine._tick()

    assert broker.close_all_called == [True]
    assert broker.closed == ["TQQQ"]             # the existing same-tick survivor sweep


def test_core_positions_invisible_to_engine_views(tmp_path):
    broker = FakeBroker(positions=[_pos("SPY", 50, 500.0), _pos("TQQQ", 100, 70.0)])
    engine = _core_engine(broker, tmp_path, holdings={"SPY": 50})

    assert [p["symbol"] for p in engine._engine_positions()] == ["TQQQ"]
    assert [p.symbol for p in engine._get_position_infos()] == ["TQQQ"]


def test_intraday_equity_is_total_minus_core_capped(tmp_path):
    broker = FakeBroker(positions=[_pos("SPY", 100, 500.0)])      # core $50k
    engine = _core_engine(broker, tmp_path, holdings={"SPY": 100})
    # total 100k, core 50k -> 50k, capped at 30% complement -> 30k
    assert engine._intraday_equity(100_000.0) == pytest.approx(30_000.0)
    # core 80k -> 20k (below the cap)
    broker.positions = [_pos("SPY", 160, 500.0)]
    assert engine._intraday_equity(100_000.0) == pytest.approx(20_000.0)
    # core disabled -> untouched
    engine.config.core.enabled = False
    assert engine._intraday_equity(100_000.0) == 100_000.0


def test_sleeve_universe_excludes_core_names(tmp_path, monkeypatch):
    broker = FakeBroker()
    engine = _core_engine(broker, tmp_path)
    engine.sleeve_enabled = True
    engine.config.strategy.stock_sleeve_enabled = True
    engine.config.strategy.stock_sleeve_news_enabled = False
    monkeypatch.setattr(engine.config, "stock_sleeve_scan_universe",
                        lambda: ["NVDA", "PLTR", "AAPL", "SMCI"])
    seen = {}

    def fake_scan(broker_, universe, cfg, long_bias=True):
        seen["universe"] = list(universe)
        return []
    monkeypatch.setattr("trader.engine.scan_candidates", fake_scan)

    engine._sleeve_scanned_day = None
    engine._ensure_sleeve_symbols()

    assert seen["universe"] == ["PLTR", "SMCI"]      # NVDA/AAPL are core names


def test_startup_failure_pages_and_can_fail_fast(tmp_path):
    broker = FakeBroker()
    broker.account = None
    engine = _core_engine(broker, tmp_path)
    engine._startup_retry_limit = 1
    engine._running = True

    engine._initialize()

    assert engine._running is False
    assert ("alert", "[trader] engine CANNOT reach the account") in engine.notifier.sent


def test_day_roll_with_zero_equity_pages_once(tmp_path):
    broker = FakeBroker()
    broker.account = {"equity": "0", "buying_power": "0"}
    engine = _core_engine(broker, tmp_path)
    for _ in range(2):
        engine._today = "2025-01-01"
        engine.journal.save_daily_csv = lambda: None       # keep tests off disk
        engine.journal.save_daily_summary = lambda: None
        engine._new_day("2025-01-02")
    alerts = [s for s in engine.notifier.sent if s[0] == "alert"]
    assert len(alerts) == 1
