"""
Fill-realism coverage for the 2026-07-01 measurement fixes.

  - Backtest gap-aware exits: a bar that OPENS beyond the stop fills at the
    open (a touched stop is a market order), and a bar that opens beyond the
    take-profit limit fills at the (better) open.
  - Backtest entry_fill_next_open: a signal fills at the NEXT bar's open —
    the earliest price the live 30s poll-loop bot can actually get — instead
    of the signal bar's close.
  - Live engine entry-fill reconciliation: the journal's entry price is
    updated to the broker's filled_avg_price, and a rejected entry closes as
    a zero-P&L record instead of lingering as a phantom open trade.
"""
from trader.backtest import Backtester
from trader.config import Config
from trader.engine import Engine
from trader.risk import RiskManager
from trader.strategies import BaseStrategy, Signal, SignalAction, SignalDirection


def bar(ts, o, h, l, c, v=10_000):
    return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def utc_minute_bars(date_str, start_hh, start_mm, prices):
    """Consecutive 1-min UTC bars."""
    out = []
    for i, (o, h, l, c) in enumerate(prices):
        total = start_mm + i
        ts = f"{date_str}T{start_hh + total // 60:02d}:{total % 60:02d}:00Z"
        out.append(bar(ts, o, h, l, c))
    return out


FLAT = [(70.0, 70.05, 69.95, 70.0)] * 6            # entry at idx 5 @ 70.0


class OneShotLong(BaseStrategy):
    """Enter long once at the current close with a configurable stop/target."""
    name = "stub_long"

    def __init__(self, config, stop_pct=0.97, tp_pct=1.05):
        super().__init__(config)
        self._fired = False
        self._stop_pct = stop_pct
        self._tp_pct = tp_pct

    def evaluate(self, candidate, bars, indicators, position=None):
        if self._fired or position is not None:
            return None
        self._fired = True
        price = float(bars[-1]["c"])
        return Signal(
            symbol=candidate.symbol, strategy=self.name,
            action=SignalAction.ENTER, direction=SignalDirection.LONG,
            strength=0.8, entry_price=price,
            stop_loss=price * self._stop_pct, take_profit=price * self._tp_pct,
        )


def run_day(day_bars, strategies, config=None, capital=100_000.0):
    if isinstance(day_bars, list):
        day_bars = {"TQQQ": day_bars}
    config = config or Config()
    bt = Backtester(config)
    risk = RiskManager(config.risk)
    risk.reset_daily(capital)
    for s in strategies:
        s.reset_daily()
        s.set_market_regime("bullish")
    return bt._simulate_day(day_bars, strategies, risk, capital, {})


# --------------------------------------------------------------------------
# Gap-aware exit fills
# --------------------------------------------------------------------------

def test_stop_gap_through_fills_at_open_not_stop_price():
    # Entry idx 5 @ 70.0, stop 67.90 (3%). The next bar GAPS to a 67.00 open —
    # a touched stop is a market order, so the realistic fill is the open.
    slip = Config().backtest.slippage_bps / 10_000.0
    gap_bar = [(67.0, 67.2, 66.8, 67.0)]
    bars = utc_minute_bars("2025-01-15", 14, 30, FLAT + gap_bar)
    config = Config()
    _, trades = run_day(bars, [OneShotLong(config.strategy)], config=config)

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "stop_loss"
    assert abs(t.exit_price - 67.0 * (1 - slip)) < 1e-6   # open, not 67.90


def test_take_profit_gap_fills_at_better_open():
    # Entry idx 5 @ 70.0, TP 73.50 (5%). The next bar opens at 74.00 — a
    # resting limit sell fills at the (better) open, not the limit price.
    slip = Config().backtest.slippage_bps / 10_000.0
    tp_gap = [(74.0, 74.2, 73.9, 74.0)]
    bars = utc_minute_bars("2025-01-15", 14, 30, FLAT + tp_gap)
    config = Config()
    _, trades = run_day(bars, [OneShotLong(config.strategy)], config=config)

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "take_profit"
    assert abs(t.exit_price - 74.0 * (1 - slip)) < 1e-6   # open, not 73.50


# --------------------------------------------------------------------------
# entry_fill_next_open
# --------------------------------------------------------------------------

def test_entry_fill_next_open_uses_next_bars_open():
    # Signal fires at idx 5 (close 70.0); the next bar opens at 70.50. With
    # the honesty flag ON the fill is the NEXT open, not the signal close.
    config = Config()
    config.backtest.entry_fill_next_open = True
    slip = config.backtest.slippage_bps / 10_000.0
    after = [(70.5, 70.6, 70.4, 70.55), (70.55, 70.65, 70.45, 70.6)]
    bars = utc_minute_bars("2025-01-15", 14, 30, FLAT + after)
    _, trades = run_day(bars, [OneShotLong(config.strategy)], config=config)

    assert len(trades) == 1                                # EOD close books it
    t = trades[0]
    assert abs(t.entry_price - 70.5 * (1 + slip)) < 1e-6   # next open, not 70.0


def test_entry_fill_same_bar_close_when_flag_off():
    config = Config()
    assert config.backtest.entry_fill_next_open is False   # default preserved
    slip = config.backtest.slippage_bps / 10_000.0
    after = [(70.5, 70.6, 70.4, 70.55)]
    bars = utc_minute_bars("2025-01-15", 14, 30, FLAT + after)
    _, trades = run_day(bars, [OneShotLong(config.strategy)], config=config)

    assert len(trades) == 1
    assert abs(trades[0].entry_price - 70.0 * (1 + slip)) < 1e-6


# --------------------------------------------------------------------------
# Live-engine entry-fill reconciliation
# --------------------------------------------------------------------------

class OrderOnlyBroker:
    """Minimal broker double: just enough for _reconcile_entry_fills."""

    def __init__(self, order):
        self._order = order
        self.requested = []

    def get_order(self, order_id):
        self.requested.append(order_id)
        return self._order


def _engine_with(broker):
    engine = Engine(Config())
    engine.broker = broker
    return engine


def test_entry_fill_reconciles_journal_to_real_price():
    broker = OrderOnlyBroker({"status": "filled", "filled_avg_price": "70.85"})
    engine = _engine_with(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.70, 69.90, 77.70)
    engine._pending_entry_orders["TQQQ"] = "oid1"

    engine._reconcile_entry_fills()

    rec = engine.journal.open_trades["TQQQ"]
    assert rec.entry_price == 70.85                        # real fill, not signal
    expected_rr = (77.70 - 70.85) / (70.85 - 69.90)
    assert abs(rec.risk_reward_target - expected_rr) < 1e-9
    assert engine._pending_entry_orders == {}              # settled
    assert broker.requested == ["oid1"]


def test_rejected_entry_closes_journal_record_at_zero_pnl():
    broker = OrderOnlyBroker({"status": "rejected", "filled_avg_price": None})
    engine = _engine_with(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.70, 69.90, 77.70)
    engine._pending_entry_orders["TQQQ"] = "oid1"

    engine._reconcile_entry_fills()

    assert "TQQQ" not in engine.journal.open_trades        # no phantom position
    closed = engine.journal.closed_trades[-1]
    assert closed.exit_reason == "entry_rejected"
    assert closed.pnl == 0.0
    assert engine._pending_entry_orders == {}


def test_pending_entries_clear_when_broker_lacks_get_order():
    class NoGetOrder:
        pass

    engine = _engine_with(NoGetOrder())
    engine._pending_entry_orders["TQQQ"] = "oid1"
    engine._reconcile_entry_fills()                        # must not raise
    assert engine._pending_entry_orders == {}
