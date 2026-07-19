"""
EOD flatten verification — the 2026-07-09 COIN incident.

close_all_positions(cancel_orders=True) cancelled COIN's bracket legs at
15:55 ET but the per-position liquidation never filled, and _flatten_all's
once-per-day guard meant nothing retried: 159 shares rode overnight with no
stop and were only closed by the NEXT day's flatten. These tests pin the fix:
  - during the closing window the engine verifies the flatten and re-closes
    any position that survived close_all_positions;
  - a rejected re-close is retried on the next closing tick;
  - a completed flatten triggers no re-close, and nothing sweeps outside the
    closing window;
  - the broker logs per-position liquidation failures from the 207 body.
"""
import datetime as _dt
import logging

from test_engine import FakeBroker, make_engine


COIN_POS = {
    "symbol": "COIN", "qty": "159", "avg_entry_price": "156.38",
    "current_price": "158.10", "market_value": "25137.90",
    "unrealized_pl": "273.48", "unrealized_plpc": "0.011",
}


def _closing_engine(broker):
    """Engine mid-day-state: journal already rolled, COIN tracked, 3 min left."""
    broker.minutes_until_close = lambda: 3.0
    engine = make_engine(broker)
    engine._today = _dt.date.today().isoformat()   # skip _new_day
    engine.journal.open_trade("COIN", "orb", "long", 159,
                              entry_price=156.38, stop_loss=155.49,
                              take_profit=169.02)
    return engine


class CompletingBroker(FakeBroker):
    """close_all_positions whose liquidations all fill (the happy path)."""
    def close_all_positions(self, cancel_orders=False):
        result = super().close_all_positions(cancel_orders)
        self.positions = []
        return result


def test_survivor_of_close_all_is_reclosed_same_window():
    broker = FakeBroker(positions=[dict(COIN_POS)])
    engine = _closing_engine(broker)

    engine._tick()

    assert broker.close_all_called == [True]       # flatten was issued
    assert broker.cancelled == ["COIN"]            # stray legs re-cancelled
    assert broker.closed == ["COIN"]               # survivor re-closed NOW
    # Booking waits for the real fill via reconciliation — not fabricated here.
    assert "COIN" in engine.journal.open_trades
    assert engine.journal.skips[-1]["stage"] == "eod_flatten_retry"


def test_rejected_reclose_retries_next_tick():
    broker = FakeBroker(positions=[dict(COIN_POS)])
    broker.close_position_result = None            # broker rejects the close
    engine = _closing_engine(broker)

    engine._tick()
    engine._tick()

    assert broker.close_all_called == [True]       # flatten still once per day
    assert broker.closed == ["COIN", "COIN"]       # re-close attempted each tick


def test_completed_flatten_is_not_reclosed():
    broker = CompletingBroker(positions=[dict(COIN_POS)])
    engine = _closing_engine(broker)

    engine._tick()

    assert broker.close_all_called == [True]
    assert broker.closed == []                     # nothing survived, no sweep action
    assert "COIN" not in engine.journal.open_trades   # reconciliation booked it


def test_no_sweep_outside_closing_window():
    broker = FakeBroker(positions=[dict(COIN_POS)])
    engine = _closing_engine(broker)
    broker.minutes_until_close = lambda: 120.0     # mid-session

    engine._tick()

    assert broker.close_all_called == []
    assert broker.closed == []


def test_close_all_logs_multistatus_failures(caplog):
    from trader.broker import AlpacaBroker

    broker = object.__new__(AlpacaBroker)          # skip credential wiring
    broker.base_url = "http://test"
    broker._request = lambda *a, **k: [
        {"symbol": "COIN", "status": 403, "body": {"available": "0"}},
        {"symbol": "NVDA", "status": 200, "body": {"id": "ok"}},
    ]

    with caplog.at_level(logging.ERROR):
        broker.close_all_positions(cancel_orders=True)

    assert "COIN" in caplog.text and "FAILED" in caplog.text
    assert "NVDA" not in caplog.text               # successes stay quiet
