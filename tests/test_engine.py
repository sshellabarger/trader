"""
Engine coverage with a fake broker (previously entirely untested). Guards the
June 2026 fixes:
  - _get_bars fetches only the regular session (09:30 ET on) and returns []
    before the open (old code fetched from 4:00 AM, so ORB never fired live).
  - _generate_signals builds each signal from the traded symbol's OWN bars
    (no cross-symbol price substitution) and the VWAP-only-on-bull filter holds.
  - a vanished position is reconciled at the REAL fill price/reason (not entry
    price → $0), records P&L, and fires on_stop_loss for a stop fill.
  - exits cancel the bracket's child orders before liquidating, and only
    journal the close when the broker actually closed the position.
"""
import datetime as _dt

from trader.config import Config
from trader.engine import Engine
from trader.strategies import Signal, SignalAction, SignalDirection


def bar(ts, o, h, l, c, v=10_000):
    return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


def winter_bars(prices, start_mm=30):
    out = []
    for i, (o, h, l, c) in enumerate(prices):
        total = start_mm + i
        ts = f"2025-01-15T{14 + total // 60:02d}:{total % 60:02d}:00Z"
        out.append(bar(ts, o, h, l, c))
    return out


BULL = [
    (70.0, 70.2, 69.9, 70.1), (70.1, 70.3, 70.0, 70.2),
    (70.2, 70.4, 70.1, 70.3), (70.3, 70.5, 70.2, 70.4),
    (70.4, 70.6, 70.3, 70.5), (70.5, 70.8, 70.5, 70.7),   # entry bar @ 70.7
]


class FakeBroker:
    def __init__(self, bars_by_symbol=None, positions=None, account=None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.positions = positions if positions is not None else []
        self.account = account or {"equity": "100000", "buying_power": "100000"}
        self.cancelled = []          # symbols whose orders were cancelled
        self.closed = []             # symbols liquidated via close_position
        self.close_all_called = []   # cancel_orders flags
        self.close_position_result = {"id": "ok"}
        self.bracket_result = {"id": "order1"}   # None simulates a rejected entry
        self.exit_fill = None        # what last_filled_exit returns
        self.open_orders = []        # open orders (for cancel-confirm polling)
        self.order_calls = []

    # account / clock
    def get_account(self):
        return self.account

    def get_equity(self):
        return float(self.account.get("equity", 0))

    def is_market_open(self):
        return True

    def minutes_until_close(self):
        return 120.0

    # positions
    def get_positions(self):
        return list(self.positions)

    # bars
    def get_bars(self, symbol, **kwargs):
        self.order_calls.append(("get_bars", symbol, kwargs))
        return list(self.bars_by_symbol.get(symbol, []))

    # orders
    def submit_bracket_order(self, **kwargs):
        self.order_calls.append(("bracket", kwargs))
        return self.bracket_result

    def get_orders(self, status="open", symbols=None, **kwargs):
        return list(self.open_orders) if status == "open" else []

    def cancel_orders_for_symbol(self, symbol):
        self.cancelled.append(symbol)
        self.open_orders = []        # cancellation clears the legs
        return 1

    def close_position(self, symbol, qty=None):
        self.closed.append(symbol)
        return self.close_position_result

    def close_all_positions(self, cancel_orders=False):
        # Alpaca liquidation is ASYNC: positions do NOT vanish synchronously.
        # The test simulates the fill by clearing self.positions itself.
        self.close_all_called.append(cancel_orders)
        return {}

    def last_filled_exit(self, symbol, entry_side):
        return self.exit_fill


def make_engine(broker, **strategy_overrides):
    config = Config()
    for k, v in strategy_overrides.items():
        setattr(config.strategy, k, v)
    engine = Engine(config)
    engine.broker = broker
    return engine


# --------------------------------------------------------------------------
# _get_bars session window / pre-open guard
# --------------------------------------------------------------------------

def test_get_bars_empty_before_open(monkeypatch):
    broker = FakeBroker(bars_by_symbol={"TQQQ": winter_bars(BULL)})
    engine = make_engine(broker)

    class FrozenPreOpen(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 15, 9, 0, tzinfo=tz)   # 09:00 ET, before open

    monkeypatch.setattr("trader.engine.datetime", FrozenPreOpen)
    assert engine._get_bars("TQQQ") == []
    assert not any(c[0] == "get_bars" for c in broker.order_calls)


def test_get_bars_fetches_from_open(monkeypatch):
    broker = FakeBroker(bars_by_symbol={"TQQQ": winter_bars(BULL)})
    engine = make_engine(broker)

    class FrozenMidday(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 15, 11, 0, tzinfo=tz)  # 11:00 ET

    monkeypatch.setattr("trader.engine.datetime", FrozenMidday)
    bars = engine._get_bars("TQQQ")
    assert len(bars) == len(BULL)
    call = next(c for c in broker.order_calls if c[0] == "get_bars")
    assert "09:30:00" in call[2]["start"]               # session open, not 4am


# --------------------------------------------------------------------------
# _generate_signals — per-symbol bars, no substitution
# --------------------------------------------------------------------------

def test_signals_use_each_symbols_own_bars(monkeypatch):
    # TQQQ has a bullish ORB; SQQQ flat (no signal).
    flat = [(40.0, 40.05, 39.95, 40.0)] * 6
    broker = FakeBroker(bars_by_symbol={
        "TQQQ": winter_bars(BULL),
        "SQQQ": winter_bars(flat),
    })
    engine = make_engine(broker)
    # Bypass the live-clock pre-open guard by feeding bars directly.
    engine._get_bars = lambda sym: broker.bars_by_symbol.get(sym, [])

    signals = engine._generate_signals()
    orb = [s for s in signals if s.strategy == "orb"]
    assert len(orb) == 1
    assert orb[0].symbol == "TQQQ"               # signal on the bullish symbol
    assert orb[0].entry_price == 70.7            # priced from TQQQ's own bars


# --------------------------------------------------------------------------
# Vanished-position reconciliation
# --------------------------------------------------------------------------

def test_vanished_position_journals_real_stop_fill():
    broker = FakeBroker(positions=[])            # position already gone
    broker.exit_fill = {"price": 69.90, "reason": "stop_loss", "filled_at": "x"}
    engine = make_engine(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100,
                              entry_price=70.70, stop_loss=69.90, take_profit=78.70)

    engine._check_exits()

    assert "TQQQ" not in engine.journal.open_trades
    closed = engine.journal.closed_trades[-1]
    assert closed.exit_price == 69.90            # real fill, not the 70.70 entry
    assert closed.exit_reason == "stop_loss"
    assert closed.pnl < 0                         # P&L is directionally correct
    assert engine.risk.daily_pnl < 0              # record_pnl was called
    orb = next(s for s in engine.strategies if s.name == "orb")
    assert "TQQQ" in orb._stopped_out             # on_stop_loss fired


# --------------------------------------------------------------------------
# Exit cancels bracket legs before liquidating; journals only on success
# --------------------------------------------------------------------------

def _exit_signal():
    return Signal(symbol="TQQQ", strategy="orb", action=SignalAction.EXIT,
                  direction=SignalDirection.FLAT, strength=0.9, reason="test exit")


def test_execute_exit_cancels_orders_before_close():
    broker = FakeBroker()
    engine = make_engine(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.0, 69.0, 78.0)

    engine._execute_exit("TQQQ", 71.0, _exit_signal())

    assert broker.cancelled == ["TQQQ"]          # legs cancelled first
    assert broker.closed == ["TQQQ"]             # then liquidated
    assert "TQQQ" not in engine.journal.open_trades


def test_execute_exit_keeps_trade_open_if_close_fails():
    broker = FakeBroker()
    broker.close_position_result = None          # broker rejects the flatten
    engine = make_engine(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.0, 69.0, 78.0)

    engine._execute_exit("TQQQ", 71.0, _exit_signal())

    assert broker.cancelled == ["TQQQ"]
    assert "TQQQ" in engine.journal.open_trades  # not journaled as closed


def test_flatten_all_issues_cancel_and_liquidate_once():
    broker = FakeBroker(positions=[{"symbol": "TQQQ", "current_price": "71.0",
                                    "avg_entry_price": "70.0"}])
    engine = make_engine(broker)

    engine._flatten_all("end_of_day")
    engine._flatten_all("end_of_day")            # idempotent within the day

    assert broker.close_all_called == [True]     # issued once, cancel_orders=True
    assert engine._flatten_requested is True


def test_eod_close_journaled_on_next_tick_at_real_fill():
    # Async liquidation: the position is still present the instant we flatten,
    # then vanishes and is booked via reconciliation at the real fill price.
    broker = FakeBroker(positions=[{"symbol": "TQQQ", "current_price": "71.0",
                                    "avg_entry_price": "70.0"}])
    engine = make_engine(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.0, 69.0, 78.0)

    engine._flatten_all("end_of_day")
    # Position still present this tick -> reconcile-only finds nothing closed.
    engine._check_exits(reconcile_only=True)
    assert "TQQQ" in engine.journal.open_trades

    # Liquidation fills: position gone, real fill available.
    broker.positions = []
    broker.exit_fill = {"price": 71.05, "reason": "close", "filled_at": "x"}
    engine._check_exits(reconcile_only=True)

    assert "TQQQ" not in engine.journal.open_trades
    closed = engine.journal.closed_trades[-1]
    assert closed.exit_price == 71.05
    assert closed.pnl > 0                         # booked, not dropped


def test_one_orb_entry_per_day_across_symbols(monkeypatch):
    # Choppy open: BOTH TQQQ and SQQQ print a bullish opening range. The engine
    # must take only ONE ORB entry (no delta-neutral 3x straddle).
    bull_tqqq = winter_bars(BULL)
    bull_sqqq = winter_bars([(40.0, 40.2, 39.9, 40.1), (40.1, 40.3, 40.0, 40.2),
                             (40.2, 40.4, 40.1, 40.3), (40.3, 40.5, 40.2, 40.4),
                             (40.4, 40.6, 40.3, 40.5), (40.5, 40.8, 40.5, 40.7)])
    broker = FakeBroker(bars_by_symbol={"TQQQ": bull_tqqq, "SQQQ": bull_sqqq})
    # SQQQ's fixture range is ~1.7% of price; disable the %-band cap so the test
    # isolates the one-entry-per-day cross-symbol guard, not range sizing.
    # The bear leg is off by default now; opt it back in so both symbols trade.
    engine = make_engine(broker, orb_max_range_pct=0, orb_trade_both_directions=True)
    engine._get_bars = lambda sym: broker.bars_by_symbol.get(sym, [])

    signals = engine._generate_signals()
    assert len([s for s in signals if s.strategy == "orb"]) == 2  # both want in

    # The execution loop (with can_open) must let only one through.
    for signal in signals:
        if signal.action != SignalAction.ENTER:
            continue
        strat = engine._strategy_named(signal.strategy)
        if strat is not None and not strat.can_open(signal.symbol):
            continue
        engine._execute_entry(signal)

    brackets = [c for c in broker.order_calls if c[0] == "bracket"]
    assert len(brackets) == 1                     # exactly one ORB position
    assert brackets[0][1]["symbol"] == "TQQQ"     # same pick as the backtest
    assert engine.risk.daily_trade_count == 1


def test_execute_exit_retries_until_close_confirms(monkeypatch):
    # If the liquidation is rejected (e.g. legs not yet cancelled), the exit is
    # committed to _pending_close and retried — never silently abandoned, so the
    # position cannot sit unprotected indefinitely.
    broker = FakeBroker()
    broker.close_position_result = None           # first close attempt rejected
    broker.positions = [{"symbol": "TQQQ", "current_price": "71.0",
                         "avg_entry_price": "70.0"}]
    engine = make_engine(broker)
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.0, 69.0, 78.0)
    monkeypatch.setattr("trader.engine.time.sleep", lambda *_: None)

    engine._execute_exit("TQQQ", 71.0, _exit_signal())
    assert broker.cancelled == ["TQQQ"]           # legs cancelled
    assert "TQQQ" in engine._pending_close         # committed to retry
    assert "TQQQ" in engine.journal.open_trades    # not yet booked

    # Next tick: the close now succeeds → booked and cleared.
    broker.close_position_result = {"id": "ok"}
    engine._retry_pending_closes()
    assert "TQQQ" not in engine._pending_close
    assert "TQQQ" not in engine.journal.open_trades


def test_retry_pending_close_drops_symbol_once_position_gone():
    # If a protective leg fills during the retry window, the position vanishes;
    # _retry_pending_closes hands it to reconciliation rather than re-closing.
    broker = FakeBroker(positions=[])             # already gone
    engine = make_engine(broker)
    engine._pending_close.add("TQQQ")
    engine._retry_pending_closes()
    assert "TQQQ" not in engine._pending_close
    assert broker.closed == []                    # no redundant close issued


def test_rejected_entry_does_not_consume_the_day():
    # A rejected bracket entry must NOT burn the day's single ORB signal:
    # can_open stays True, no trade counted, nothing journaled.
    broker = FakeBroker()
    broker.bracket_result = None                  # Alpaca rejects the entry
    engine = make_engine(broker)
    signal = Signal(symbol="TQQQ", strategy="orb", action=SignalAction.ENTER,
                    direction=SignalDirection.LONG, strength=0.7,
                    entry_price=70.7, stop_loss=69.9, take_profit=78.7)

    engine._execute_entry(signal)

    orb = engine._strategy_named("orb")
    assert orb.can_open("TQQQ") is True
    assert engine.risk.daily_trade_count == 0
    assert "TQQQ" not in engine.journal.open_trades

    # A subsequent successful submit consumes the day as normal.
    broker.bracket_result = {"id": "order1"}
    engine._execute_entry(signal)
    assert orb.can_open("TQQQ") is False
    assert engine.risk.daily_trade_count == 1


def test_reconcile_fallback_uses_last_price_not_entry(monkeypatch):
    broker = FakeBroker(positions=[])
    broker.exit_fill = None                        # no fill found
    engine = make_engine(broker)
    engine._get_bars = lambda sym: winter_bars([(70.0, 71.6, 70.0, 71.5)])
    engine.journal.open_trade("TQQQ", "orb", "long", 100, 70.0, 69.0, 78.0)

    engine._check_exits()

    closed = engine.journal.closed_trades[-1]
    assert closed.exit_price == 71.5              # last bar close, not the 70.0 entry
    assert closed.pnl > 0


# --------------------------------------------------------------------------
# broker.last_filled_exit parsing (real logic, not stubbed)
# --------------------------------------------------------------------------

def _broker():
    from trader.broker import AlpacaBroker
    from trader.config import BrokerConfig
    return AlpacaBroker(BrokerConfig())


def test_last_filled_exit_picks_stop_leg_not_entry(monkeypatch):
    broker = _broker()
    orders = [
        {"id": "1", "side": "buy", "status": "filled", "filled_at": "2025-01-15T14:35:00Z",
         "filled_avg_price": "70.70", "type": "market"},          # entry — must be ignored
        {"id": "2", "side": "sell", "status": "filled", "filled_at": "2025-01-15T15:10:00Z",
         "filled_avg_price": "69.90", "type": "stop"},            # stop fill
        {"id": "3", "side": "sell", "status": "canceled", "filled_at": None,
         "filled_avg_price": None, "type": "limit"},              # OCO sibling canceled
    ]
    monkeypatch.setattr(broker, "get_orders", lambda **kw: orders)
    fill = broker.last_filled_exit("TQQQ", "buy")
    assert fill == {"price": 69.90, "reason": "stop_loss", "filled_at": "2025-01-15T15:10:00Z"}


def test_last_filled_exit_classifies_take_profit_and_picks_latest(monkeypatch):
    broker = _broker()
    orders = [
        {"id": "1", "side": "buy", "status": "filled", "filled_at": "2025-01-15T14:35:00Z",
         "filled_avg_price": "70.70", "type": "market"},
        {"id": "2", "side": "sell", "status": "filled", "filled_at": "2025-01-15T15:55:00Z",
         "filled_avg_price": "78.70", "type": "limit"},           # TP, most recent
        {"id": "3", "side": "sell", "status": "filled", "filled_at": "2025-01-15T15:10:00Z",
         "filled_avg_price": "69.90", "type": "stop"},            # earlier
    ]
    monkeypatch.setattr(broker, "get_orders", lambda **kw: orders)
    fill = broker.last_filled_exit("TQQQ", "buy")
    assert fill["reason"] == "take_profit"
    assert fill["price"] == 78.70


def test_last_filled_exit_none_when_no_closing_fill(monkeypatch):
    broker = _broker()
    monkeypatch.setattr(broker, "get_orders", lambda **kw: [
        {"id": "1", "side": "buy", "status": "filled", "filled_at": "t",
         "filled_avg_price": "70.0", "type": "market"},           # only the entry
    ])
    assert broker.last_filled_exit("TQQQ", "buy") is None


# --------------------------------------------------------------------------
# Trading-symbol set — the bear (SQQQ) leg is OFF by default (2026-06-13
# diagnosis: SQQQ ORB had negative expectancy in every coherent slice; the
# TQQQ-only profile was +29%/PF 1.64/Sharpe 2.75). It must stay re-enableable.
# --------------------------------------------------------------------------

def test_default_trading_symbols_are_tqqq_only():
    cfg = Config()
    assert cfg.get_trading_symbols() == ["TQQQ"]   # bear leg off by default


def test_bear_leg_reenables_when_both_directions_on():
    cfg = Config()
    cfg.strategy.orb_trade_both_directions = True
    assert cfg.get_trading_symbols() == ["TQQQ", "SQQQ"]


def test_engine_trades_tqqq_only_by_default():
    engine = make_engine(_broker())
    assert engine.symbols == ["TQQQ"]
