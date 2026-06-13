"""
Regression tests for the backtester (June 2026):
  - session_window / minutes_to_close use correct ET offsets across DST (the
    old code hardcoded -05:00, shifting every backtest day to 10:30-17:00 ET
    from mid-March through October).
  - LONG stop and take-profit fills compute P&L and slippage with the right
    signs (mirror of the short-side math, which was fixed earlier).
  - SHORT fills (kept for generality) profit on a fall and lose on a rise.
  - DOWN-Nasdaq days are profitable by going LONG SQQQ — the core "make money
    on both up and down days" behavior — in a multi-symbol simulation.
  - the live EOD rules are mirrored: no new entries inside no_trade_last_minutes
    and a forced flatten at eod_minutes_before_close.
"""
from trader.backtest import Backtester, session_window, minutes_to_close
from trader.config import Config
from trader.risk import RiskManager
from trader.scanner import Candidate
from trader.strategies import (
    BaseStrategy, Signal, SignalAction, SignalDirection,
)
from trader.strategies.orb import ORBStrategy


def test_margin_multiple_sizing_matches_live_buying_power():
    # The backtest sizes with capital * margin_multiple as buying power; the live
    # engine passes the real account buying power (~2x equity on a RegT margin
    # account). With capital == equity the two must size identically, and the
    # bump from the old 0.5x must produce a strictly larger position.
    config = Config()
    risk = RiskManager(config.risk)
    sig = Signal(symbol="TQQQ", strategy="orb", action=SignalAction.ENTER,
                 direction=SignalDirection.LONG, strength=0.7,
                 entry_price=70.7, stop_loss=69.9, take_profit=78.7)
    equity = config.backtest.initial_capital

    bt_bp = equity * config.backtest.margin_multiple   # backtester
    live_bp = equity * 2.0                             # live RegT account
    s_bt = risk.calculate_position_size(sig, equity, bt_bp, 0)
    s_live = risk.calculate_position_size(sig, equity, live_bp, 0)
    s_old = risk.calculate_position_size(sig, equity, equity * 0.5, 0)

    assert s_bt.shares == s_live.shares                # backtest mirrors live
    assert s_bt.limited_by != "buying_power"           # max_position now binds
    assert s_bt.shares > s_old.shares                  # bigger than the old 0.5x cap


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


# 09:30 ET (winter) = 14:30 UTC
BULL_RANGE = [
    (70.0, 70.2, 69.9, 70.1), (70.1, 70.3, 70.0, 70.2),
    (70.2, 70.4, 70.1, 70.3), (70.3, 70.5, 70.2, 70.4),
    (70.4, 70.6, 70.3, 70.5),                              # closes up; low 69.9
]
BEAR_RANGE = [
    (70.0, 70.1, 69.8, 69.9), (69.9, 70.0, 69.7, 69.8),
    (69.8, 69.9, 69.6, 69.7), (69.7, 69.8, 69.5, 69.6),
    (69.6, 69.7, 69.4, 69.5),                              # closes down
]


def run_day(day_bars, strategies, capital=100_000.0, regime="bullish"):
    if isinstance(day_bars, list):
        day_bars = {"TQQQ": day_bars}
    config = Config()
    bt = Backtester(config)
    risk = RiskManager(config.risk)
    risk.reset_daily(capital)
    for s in strategies:
        s.reset_daily()
        s.set_market_regime(regime)
    return bt._simulate_day(day_bars, strategies, risk, capital, {})


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------

def test_session_window_handles_dst():
    assert session_window("2025-01-15") == (
        "2025-01-15T09:30:00-05:00", "2025-01-15T16:00:00-05:00")   # EST
    assert session_window("2025-07-15") == (
        "2025-07-15T09:30:00-04:00", "2025-07-15T16:00:00-04:00")   # EDT


def test_minutes_to_close_across_dst():
    # 14:30 UTC = 09:30 EST -> 390 min to a 21:00 UTC (16:00 EST) close
    assert abs(minutes_to_close("2025-01-15T14:30:00Z") - 390) < 1e-6
    # 13:30 UTC = 09:30 EDT -> 390 min to a 20:00 UTC (16:00 EDT) close
    assert abs(minutes_to_close("2025-07-15T13:30:00Z") - 390) < 1e-6


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

class OneShotShort(BaseStrategy):
    name = "stub_short"

    def __init__(self, config):
        super().__init__(config)
        self._fired = False

    def evaluate(self, candidate, bars, indicators, position=None):
        if self._fired or position is not None:
            return None
        self._fired = True
        price = float(bars[-1]["c"])
        return Signal(
            symbol=candidate.symbol, strategy=self.name,
            action=SignalAction.ENTER, direction=SignalDirection.SHORT,
            strength=0.9, entry_price=price,
            stop_loss=price + 0.7, take_profit=price - 7.0,
        )


class AlwaysEnterLong(BaseStrategy):
    """Enters long whenever flat, with stop/target far away so only EOD or the
    time gate ends the trade. Used to probe the late-session rules."""
    name = "stub_long"

    def evaluate(self, candidate, bars, indicators, position=None):
        if position is not None:
            return None
        price = float(bars[-1]["c"])
        # Stop within the 5% hard limit but below the flat-bar low, target above
        # the flat-bar high — so only EOD/the time gate ends the trade.
        return Signal(
            symbol=candidate.symbol, strategy=self.name,
            action=SignalAction.ENTER, direction=SignalDirection.LONG,
            strength=0.8, entry_price=price,
            stop_loss=price * 0.97, take_profit=price * 1.05,
        )


# --------------------------------------------------------------------------
# Short side (generality)
# --------------------------------------------------------------------------

def test_short_profits_when_price_falls_to_target():
    flat = [(70.0, 70.0, 70.0, 70.0)] * 6           # entry at idx 5 @ 70
    falling = [(69.0, 69.0, 68.0, 68.0), (66.0, 66.0, 64.0, 64.0),
               (64.0, 64.0, 62.5, 63.0)]            # low 62.5 <= tp 63 -> cover
    bars = utc_minute_bars("2025-01-15", 14, 30, flat + falling)

    capital, trades = run_day(bars, [OneShotShort(Config())])
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].exit_reason == "take_profit"
    assert trades[0].pnl > 0 and capital > 100_000.0


def test_short_loses_when_price_rises_to_stop():
    flat = [(70.0, 70.0, 70.0, 70.0)] * 6
    rising = [(70.2, 70.4, 70.1, 70.3), (70.4, 71.0, 70.3, 70.9)]  # high 71 >= stop 70.7
    bars = utc_minute_bars("2025-01-15", 14, 30, flat + rising)

    strat = OneShotShort(Config())
    capital, trades = run_day(bars, [strat])
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].pnl < 0 and capital < 100_000.0
    assert "TQQQ" in strat._stopped_out


# --------------------------------------------------------------------------
# Long side (ORB) — up days
# --------------------------------------------------------------------------

def test_orb_long_take_profit():
    entry = [(70.5, 70.6, 70.5, 70.7)]
    spike = [(70.7, 79.0, 70.7, 78.9)]              # high 79 >= tp 78.7
    bars = utc_minute_bars("2025-01-15", 14, 30, BULL_RANGE + entry + spike)

    capital, trades = run_day(bars, [ORBStrategy(Config())])
    assert len(trades) == 1
    assert trades[0].direction == "long"
    assert trades[0].exit_reason == "take_profit"
    assert trades[0].pnl > 0 and capital > 100_000.0


def test_orb_long_stop_loss():
    entry = [(70.5, 70.6, 70.5, 70.7)]
    plunge = [(70.5, 70.5, 69.0, 69.2)]             # low 69.0 <= stop 69.9
    bars = utc_minute_bars("2025-01-15", 14, 30, BULL_RANGE + entry + plunge)

    strat = ORBStrategy(Config())
    capital, trades = run_day(bars, [strat])
    assert len(trades) == 1
    assert trades[0].direction == "long"
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].pnl < 0 and capital < 100_000.0
    assert "TQQQ" in strat._stopped_out


def test_orb_long_eod_close_profit():
    entry = [(70.5, 70.6, 70.5, 70.7)]
    drift = [(70.7 + 0.02 * i, 70.8 + 0.02 * i, 70.6 + 0.02 * i, 70.75 + 0.02 * i)
             for i in range(25)]                     # never hits 78.7 tp, never 69.9 stop
    bars = utc_minute_bars("2025-01-15", 14, 30, BULL_RANGE + entry + drift)

    capital, trades = run_day(bars, [ORBStrategy(Config())])
    assert len(trades) == 1
    assert trades[0].direction == "long"
    assert trades[0].exit_reason == "eod_close"
    assert trades[0].pnl > 0 and capital > 100_000.0


# --------------------------------------------------------------------------
# The headline behavior: profit on a DOWN day by going long SQQQ
# --------------------------------------------------------------------------

def test_down_day_profits_via_sqqq():
    # Nasdaq down: TQQQ opens and falls (bearish range -> no TQQQ trade),
    # SQQQ opens and rises (bullish range -> long SQQQ, rides the move up).
    tqqq_entry = [(69.5, 69.6, 69.3, 69.4)]
    tqqq_fall = [(69.4 - 0.05 * i, 69.5 - 0.05 * i, 69.3 - 0.05 * i, 69.35 - 0.05 * i)
                 for i in range(20)]
    sqqq_entry = [(40.5, 40.6, 40.5, 40.7)]
    sqqq_rise = [(40.7 + 0.05 * i, 40.85 + 0.05 * i, 40.6 + 0.05 * i, 40.8 + 0.05 * i)
                 for i in range(20)]

    tqqq_bars = utc_minute_bars("2025-01-15", 14, 30, BEAR_RANGE + tqqq_entry + tqqq_fall)
    # SQQQ bullish range (mirror of BEAR): open 40.0 -> close 40.5, low 39.9
    sqqq_range = [(40.0, 40.2, 39.9, 40.1), (40.1, 40.3, 40.0, 40.2),
                  (40.2, 40.4, 40.1, 40.3), (40.3, 40.5, 40.2, 40.4),
                  (40.4, 40.6, 40.3, 40.5)]
    sqqq_bars = utc_minute_bars("2025-01-15", 14, 30, sqqq_range + sqqq_entry + sqqq_rise)

    capital, trades = run_day({"TQQQ": tqqq_bars, "SQQQ": sqqq_bars},
                              [ORBStrategy(Config())])

    assert len(trades) == 1                  # one ORB trade for the day
    assert trades[0].symbol == "SQQQ"        # bought the inverse ETF
    assert trades[0].direction == "long"
    assert trades[0].pnl > 0 and capital > 100_000.0


def test_both_bullish_open_takes_one_orb_trade():
    # Choppy open where BOTH symbols print a bullish range. The backtest must
    # take exactly ONE ORB trade (matching the live engine's can_open cap) and
    # pick TQQQ (first in the traded order), not hold both.
    tq_range = [(70.0, 70.2, 69.9, 70.1), (70.1, 70.3, 70.0, 70.2),
                (70.2, 70.4, 70.1, 70.3), (70.3, 70.5, 70.2, 70.4),
                (70.4, 70.6, 70.3, 70.5)]
    sq_range = [(40.0, 40.2, 39.9, 40.1), (40.1, 40.3, 40.0, 40.2),
                (40.2, 40.4, 40.1, 40.3), (40.3, 40.5, 40.2, 40.4),
                (40.4, 40.6, 40.3, 40.5)]
    tq = utc_minute_bars("2025-01-15", 14, 30,
                         tq_range + [(70.5, 70.7, 70.5, 70.6)] + [(70.6,) * 4] * 10)
    sq = utc_minute_bars("2025-01-15", 14, 30,
                         sq_range + [(40.5, 40.7, 40.5, 40.6)] + [(40.6,) * 4] * 10)
    # day_bars insertion order = traded order (TQQQ first)
    _, trades = run_day({"TQQQ": tq, "SQQQ": sq}, [ORBStrategy(Config())])

    assert len(trades) == 1
    assert trades[0].symbol == "TQQQ"        # tiebreak matches the engine's pick


# --------------------------------------------------------------------------
# Live EOD rules mirrored in the backtest
# --------------------------------------------------------------------------

def test_no_new_entry_within_no_trade_window():
    # All bars sit in the last 15 minutes (15:46-15:54 ET = 20:46-20:54 UTC).
    late = [(70.0, 70.1, 69.9, 70.0)] * 9
    bars = utc_minute_bars("2025-01-15", 20, 46, late)
    _, trades = run_day(bars, [AlwaysEnterLong(Config())])
    assert trades == []                       # gate blocked every entry

    # Same pattern early in the session does enter.
    early = utc_minute_bars("2025-01-15", 15, 0, late)   # 10:00 ET
    _, trades = run_day(early, [AlwaysEnterLong(Config())])
    assert len(trades) >= 1


def test_force_close_at_eod_minutes_before_close():
    early = [(70.0, 70.0, 70.0, 70.0)] * 6              # enter ~09:35
    early_bars = utc_minute_bars("2025-01-15", 14, 30, early)
    # Distinct late prices: 15:56 ET (20:56 UTC, 4 min to close) then 15:59.
    late_bars = [
        bar("2025-01-15T20:56:00Z", 72.0, 72.0, 72.0, 72.0),
        bar("2025-01-15T20:59:00Z", 80.0, 80.0, 80.0, 80.0),
    ]
    capital, trades = run_day(early_bars + late_bars, [AlwaysEnterLong(Config())])

    assert len(trades) == 1
    assert trades[0].exit_reason == "eod_close"
    # Flattened at the 15:56 bar (72), not carried to the 15:59 bar (80).
    assert trades[0].exit_price < 73.0
