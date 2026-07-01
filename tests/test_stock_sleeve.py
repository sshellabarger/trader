"""
Stock-sleeve coverage (stocks-only, scanner-driven ORB).

Guards the June 2026 sleeve work:
  - default OFF: with no env, the engine still trades the TQQQ/SQQQ index legs
    and never touches the scanner.
  - env wiring: STOCK_SLEEVE_* / risk caps come through Config from the
    environment, and the scan universe resolves from an explicit list or the
    named universe categories.
  - ORB daily-entry cap is a counter (orb_max_entries_per_day), so the index
    profile's single breakout/day is preserved while the sleeve can take N.
  - the engine scans the high-growth universe, picks the top-N names, trades
    only those, and respects the sleeve's concurrency cap.
  - the QQQ overnight-alignment gate is inert for stock names (never blocks a
    stock on the index's overnight move).
"""
import datetime as _dt

from trader.config import Config
from trader.engine import Engine
from trader.strategies import SignalAction
from trader.strategies.orb import ORBStrategy
from trader.scanner import Candidate


# --------------------------------------------------------------------------
# Fixtures: a fake broker with snapshots (scanner) + bars (ORB)
# --------------------------------------------------------------------------

def _bar(ts, o, h, l, c, v=10_000):
    return {"t": ts, "o": o, "h": h, "l": l, "c": c, "v": v}


# A bullish 5-min opening range + entry bar at 09:30 ET (14:30 UTC, winter),
# identical in shape to the index ORB fixtures so the breakout fires.
BULL = [
    (70.0, 70.2, 69.9, 70.1), (70.1, 70.3, 70.0, 70.2),
    (70.2, 70.4, 70.1, 70.3), (70.3, 70.5, 70.2, 70.4),
    (70.4, 70.6, 70.3, 70.5), (70.5, 70.8, 70.5, 70.7),  # entry bar @ 70.7
]


def bull_bars():
    out = []
    for i, (o, h, l, c) in enumerate(BULL):
        total = 30 + i
        ts = f"2025-01-15T{14 + total // 60:02d}:{total % 60:02d}:00Z"
        out.append(_bar(ts, o, h, l, c))
    return out


def snap(price, prev_close, day_vol, prev_vol, open_):
    """A scanner snapshot that passes the price/volume/rvol filters."""
    return {
        "latestTrade": {"p": price},
        "dailyBar": {"o": open_, "h": price * 1.01, "l": open_ * 0.99,
                     "c": price, "v": day_vol},
        "prevDailyBar": {"c": prev_close, "v": prev_vol},
        "minuteBar": {"c": price},
    }


class FakeBroker:
    def __init__(self, snapshots=None, bars=None):
        self.snapshots = snapshots or {}
        self.bars = bars or {}
        self.account = {"equity": "100000", "buying_power": "200000"}
        self.positions = []
        self.brackets = []
        self.open_orders = []

    # account / clock
    def get_account(self):
        return self.account

    def get_equity(self):
        return float(self.account["equity"])

    def is_market_open(self):
        return True

    def minutes_until_close(self):
        return 120.0

    # data
    def get_snapshots(self, symbols):
        return {s: self.snapshots[s] for s in symbols if s in self.snapshots}

    def get_bars(self, symbol, **kw):
        return list(self.bars.get(symbol, []))

    # positions / orders
    def get_positions(self):
        return list(self.positions)

    def get_orders(self, status="open", symbols=None, **kw):
        return list(self.open_orders) if status == "open" else []

    def submit_bracket_order(self, **kw):
        self.brackets.append(kw)
        return {"id": f"ord{len(self.brackets)}"}


def make_engine(broker=None, **strategy_overrides):
    config = Config()
    for k, v in strategy_overrides.items():
        setattr(config.strategy, k, v)
    engine = Engine(config)
    if broker is not None:
        engine.broker = broker
    return engine


# --------------------------------------------------------------------------
# Default OFF — index behavior is untouched
# --------------------------------------------------------------------------

def test_sleeve_off_by_default():
    engine = make_engine()
    assert engine.sleeve_enabled is False
    # Index mode, unchanged. Bull leg only: the SQQQ bear leg defaults OFF as
    # of 2026-07-01 (negative expectancy in every coherent slice).
    assert engine.symbols == ["TQQQ"]
    assert engine.config.strategy.orb_max_entries_per_day == 1


def test_sleeve_off_never_scans():
    # With the sleeve off, a tick must not consult the scanner at all.
    called = {"scan": False}
    broker = FakeBroker()
    broker.get_snapshots = lambda syms: (called.__setitem__("scan", True) or {})
    engine = make_engine(broker)
    engine._ensure_overnight_gap = lambda: None         # avoid QQQ fetch
    engine._generate_signals = lambda: []               # short-circuit
    engine._tick()
    assert called["scan"] is False


# --------------------------------------------------------------------------
# Config / env wiring
# --------------------------------------------------------------------------

def test_sleeve_env_config(monkeypatch):
    monkeypatch.setenv("STOCK_SLEEVE_ENABLED", "true")
    monkeypatch.setenv("STOCK_SLEEVE_SYMBOLS", "NVDA, AMD ,PLTR")
    monkeypatch.setenv("STOCK_SLEEVE_MAX_POSITIONS", "4")
    monkeypatch.setenv("STOCK_SLEEVE_MAX_POSITION_PCT", "20")
    monkeypatch.setenv("ORB_MAX_ENTRIES_PER_DAY", "2")
    monkeypatch.setenv("MAX_POSITION_PCT", "12.5")       # risk cap from env

    c = Config()
    assert c.strategy.stock_sleeve_enabled is True
    assert c.strategy.stock_sleeve_max_positions == 4
    assert c.strategy.stock_sleeve_max_position_pct == 20.0
    assert c.strategy.orb_max_entries_per_day == 2
    assert c.risk.max_position_pct == 12.5
    assert c.stock_sleeve_scan_universe() == ["NVDA", "AMD", "PLTR"]


def test_sleeve_universe_from_categories():
    c = Config()
    c.strategy.stock_sleeve_symbols = ""
    c.strategy.stock_sleeve_universe = "tech_volatile"
    uni = c.stock_sleeve_scan_universe()
    assert "PLTR" in uni and "CRWD" in uni              # high-growth names
    assert "TQQQ" not in uni                            # not the leveraged ETFs


# --------------------------------------------------------------------------
# ORB daily-entry cap is a counter
# --------------------------------------------------------------------------

def _orb_signal(strat):
    return strat.evaluate(
        Candidate(symbol="AAA", price=70.7, prev_close=70.0, gap_pct=0,
                  change_pct=0, volume=1, avg_volume=1, relative_volume=1,
                  high=70.8, low=69.9, open_price=70.0),
        bull_bars(), {})


def test_orb_counter_default_one_per_day():
    cfg = Config()
    strat = ORBStrategy(cfg)
    s1 = _orb_signal(strat)
    assert s1 is not None
    strat.on_fill("AAA", s1)
    assert _orb_signal(strat) is None                  # 2nd blocked at cap 1
    assert strat.can_open("BBB") is False
    strat.reset_daily()
    assert _orb_signal(strat) is not None               # new day resets


def test_orb_counter_allows_n_when_raised():
    cfg = Config()
    cfg.strategy.orb_max_entries_per_day = 3
    strat = ORBStrategy(cfg)
    for _ in range(3):
        sig = _orb_signal(strat)
        assert sig is not None
        strat.on_fill(sig.symbol, sig)
    assert _orb_signal(strat) is None                  # 4th blocked at cap 3
    assert strat.can_open("AAA") is False


# --------------------------------------------------------------------------
# Sleeve risk reconfiguration
# --------------------------------------------------------------------------

def test_sleeve_applies_risk_caps():
    engine = make_engine(stock_sleeve_enabled=True,
                         stock_sleeve_max_positions=2,
                         stock_sleeve_max_position_pct=25.0)
    assert engine.risk.config.max_positions == 2        # sleeve concurrency cap
    assert engine.risk.config.max_position_pct == 25.0  # per-name size cap
    assert engine.config.strategy.orb_max_entries_per_day == 2  # widened from 1
    assert engine.risk.config.max_daily_trades >= 4


# --------------------------------------------------------------------------
# End-to-end: scan, pick, trade — capped
# --------------------------------------------------------------------------

def _sleeve_broker():
    snaps = {
        "AAA": snap(150.0, 145.0, 600_000, 400_000, 148.0),  # gap +2.1%, rvol 1.5
        "BBB": snap(90.0, 88.0, 300_000, 200_000, 89.0),     # gap +1.1%, rvol 1.5
        "CCC": snap(50.0, 49.0, 250_000, 250_000, 49.5),     # gap +1.0%, rvol 1.0
    }
    bars = {s: bull_bars() for s in snaps}
    return FakeBroker(snapshots=snaps, bars=bars)


def test_sleeve_scan_populates_symbols():
    broker = _sleeve_broker()
    engine = make_engine(broker, stock_sleeve_enabled=True,
                         stock_sleeve_symbols="AAA,BBB,CCC",
                         stock_sleeve_max_candidates=3)
    engine._ensure_sleeve_symbols()
    # Sorted by |gap| × rvol: AAA (3.1) > BBB (1.7) > CCC (1.0).
    assert engine.symbols == ["AAA", "BBB", "CCC"]


def test_sleeve_trades_multiple_names_capped():
    broker = _sleeve_broker()
    engine = make_engine(broker, stock_sleeve_enabled=True,
                         stock_sleeve_symbols="AAA,BBB,CCC",
                         stock_sleeve_max_candidates=3,
                         stock_sleeve_max_positions=2)
    engine._ensure_sleeve_symbols()
    engine._get_bars = lambda sym: broker.bars.get(sym, [])

    signals = engine._generate_signals()
    orb = [s for s in signals if s.strategy == "orb"]
    assert len(orb) == 3                               # all three break out

    # Execute with the same can_open gate the live tick uses.
    for signal in signals:
        if signal.action != SignalAction.ENTER:
            continue
        strat = engine._strategy_named(signal.strategy)
        if strat is not None and not strat.can_open(signal.symbol):
            continue
        engine._execute_entry(signal)

    assert len(broker.brackets) == 2                   # concurrency cap holds
    assert engine.risk.daily_trade_count == 2
    traded = {b["symbol"] for b in broker.brackets}
    assert traded == {"AAA", "BBB"}                    # the two strongest


def test_sleeve_overnight_gate_inert_for_stocks():
    # Even with the QQQ overnight-alignment gate ON, stock names must still
    # trade: the gate describes the index, not individual stocks.
    broker = _sleeve_broker()
    engine = make_engine(broker, stock_sleeve_enabled=True,
                         stock_sleeve_symbols="AAA",
                         stock_sleeve_max_candidates=1,
                         orb_require_overnight_alignment=True,
                         orb_overnight_gap_min_pct=0.0)
    engine._overnight_gap_pct = -5.0                   # a big DOWN index gap
    engine._ensure_sleeve_symbols()
    engine._get_bars = lambda sym: broker.bars.get(sym, [])

    orb = [s for s in engine._generate_signals() if s.strategy == "orb"]
    assert len(orb) == 1 and orb[0].symbol == "AAA"    # not blocked by QQQ's gap
