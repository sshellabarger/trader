"""Tests for the sleeve replay backtester (sleeve_backtest.py) and the two
strategy/config changes that ship with it (ATR-scaled ORB band, env knobs,
extra_indicators pass-through in Backtester._simulate_day).

Everything here is OFFLINE: caches are pre-written JSON in tmp dirs, the
broker is never called (offline=True), and no test needs network or keys.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trader.backtest import Backtester
from trader.config import Config
from trader.risk import RiskManager
from trader.scanner import Candidate
from trader.sleeve_backtest import (
    BarCache, SleeveBacktester, daily_atr_pct, prior_daily, run_sweep,
)
from trader.strategies.orb import ORBStrategy

_ET = ZoneInfo("America/New_York")

DAY = "2026-07-06"  # a Monday


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _weekdays_before(day_str: str, n: int):
    d = datetime.strptime(day_str, "%Y-%m-%d").date()
    out = []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return list(reversed(out))


def make_dailies(day_str: str, prev_close: float, today_open: float,
                 n_prior: int = 20, prev_vol: float = 1_000_000,
                 spread: float = 1.0):
    """n_prior flat-ish daily bars ending at prev_close, then day_str's bar
    opening at today_open. spread widens prior H-L to control daily ATR%."""
    bars = []
    for d in _weekdays_before(day_str, n_prior):
        bars.append({"t": f"{d}T04:00:00Z", "o": prev_close,
                     "h": prev_close + spread, "l": prev_close - spread,
                     "c": prev_close, "v": prev_vol})
    bars.append({"t": f"{day_str}T04:00:00Z", "o": today_open,
                 "h": max(today_open, prev_close) + spread,
                 "l": min(today_open, prev_close) - spread,
                 "c": today_open, "v": prev_vol})
    return bars


def make_rth_bars(day_str: str, base: float = 100.0):
    """Full 9:30-15:59 session engineered so the live ORB config fires:
    opening range 9:30-9:34 closes UP with size 0.8 (~0.79% of price),
    entry bar at 9:35, then a gentle drift up to ~base+5 with no stop/TP touch.
    """
    d = datetime.strptime(day_str, "%Y-%m-%d")
    t0 = d.replace(hour=9, minute=30, tzinfo=_ET)

    def bar(i, o, h, l, c):
        t = (t0 + timedelta(minutes=i)).isoformat()
        return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": 50_000}

    bars = [
        bar(0, base, base + 0.40, base, base + 0.30),
        bar(1, base + 0.30, base + 0.50, base + 0.20, base + 0.45),
        bar(2, base + 0.45, base + 0.60, base + 0.35, base + 0.55),
        bar(3, base + 0.55, base + 0.70, base + 0.45, base + 0.65),
        bar(4, base + 0.65, base + 0.80, base + 0.55, base + 0.80),  # range: [base, base+0.8], closes up
        bar(5, base + 0.85, base + 0.95, base + 0.80, base + 0.90),  # 9:35 entry bar
    ]
    # 9:36 onward: drift from base+1.0 to ~base+5, lows well above the
    # range-low stop, highs far below the 10R take profit.
    total = 389  # through 15:59
    for i in range(6, total + 1):
        frac = (i - 6) / (total - 6)
        px = base + 1.0 + 4.0 * frac
        bars.append(bar(i, px, px + 0.05, px - 0.05, px))
    return bars


def write_cache(cache_dir, name, obj):
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, name), "w") as f:
        json.dump(obj, f)


def seed_cache(cache_dir, day_str=DAY, aaa_open=103.0, bbb_open=97.0):
    """AAA gaps +3%, BBB gaps -3%, QQQ flat (regime + market-open marker)."""
    meta = {"meta": {"start": "2026-01-01", "end": "2026-12-31"}}
    write_cache(cache_dir, "daily_AAA.json",
                {**meta, "bars": make_dailies(day_str, 100.0, aaa_open)})
    write_cache(cache_dir, "daily_BBB.json",
                {**meta, "bars": make_dailies(day_str, 100.0, bbb_open)})
    write_cache(cache_dir, "daily_QQQ.json",
                {**meta, "bars": make_dailies(day_str, 500.0, 500.0)})
    write_cache(cache_dir, f"rth_AAA_{day_str}.json", make_rth_bars(day_str))


def sleeve_config(**overrides) -> Config:
    cfg = Config()
    cfg.strategy.stock_sleeve_symbols = "AAA,BBB"
    cfg.strategy.stock_sleeve_long_bias = True
    cfg.backtest.entry_fill_next_open = True
    cfg.backtest.slippage_bps = 10.0
    for k, v in overrides.items():
        setattr(cfg.strategy, k, v)
    return cfg


# ---------------------------------------------------------------------------
# daily ATR%
# ---------------------------------------------------------------------------

def test_daily_atr_pct_uses_prior_days_only():
    bars = make_dailies(DAY, 100.0, 130.0, n_prior=20, spread=1.0)
    val = daily_atr_pct(bars, DAY)
    # Flat prior closes with H-L = 2.0 → TR = 2.0 → ATR% = 2.0
    assert val == pytest.approx(2.0, abs=0.05)
    # The sim day's own (huge-gap) bar must not leak in: computing "as of" the
    # NEXT day, whose window now includes the +30% gap day, raises the value
    # (one ~31-point TR joins thirteen 2-point TRs → ~3.1% of the new close).
    next_day = "2026-07-07"
    assert daily_atr_pct(bars, next_day) > val * 1.4


def test_daily_atr_pct_insufficient_data_is_none():
    bars = make_dailies(DAY, 100.0, 103.0, n_prior=3)
    assert daily_atr_pct(bars, DAY) is None
    assert prior_daily([], DAY) is None


# ---------------------------------------------------------------------------
# Scanner simulation
# ---------------------------------------------------------------------------

def test_simulate_scan_filters_ranking_and_long_bias(tmp_path):
    cache = str(tmp_path)
    meta = {"meta": {"start": "2026-01-01", "end": "2026-12-31"}}
    write_cache(cache, "daily_AAA.json", {**meta, "bars": make_dailies(DAY, 100, 104.0)})   # +4%
    write_cache(cache, "daily_BBB.json", {**meta, "bars": make_dailies(DAY, 100, 94.0)})    # -6%, biggest |gap|
    write_cache(cache, "daily_CCC.json", {**meta, "bars": make_dailies(DAY, 100, 108.0, prev_vol=50_000)})  # volume-filtered
    write_cache(cache, "daily_DDD.json", {**meta, "bars": make_dailies(DAY, 100, 101.0)})   # +1%
    write_cache(cache, "daily_QQQ.json", {**meta, "bars": make_dailies(DAY, 500, 500)})

    cfg = sleeve_config()
    cfg.strategy.stock_sleeve_symbols = "AAA,BBB,CCC,DDD"
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="off", offline=True)

    cands = bt.simulate_scan(DAY, ["AAA", "BBB", "CCC", "DDD"])
    # CCC dropped (prev volume < 100k); long-bias puts gap-ups ahead of the
    # bigger-|gap| BBB; within gap-ups, larger score first.
    assert [c.symbol for c in cands] == ["AAA", "DDD", "BBB"]

    picks, displaced = bt.picks_for_day(DAY, ["AAA", "BBB", "CCC", "DDD"])
    assert [c.symbol for c in picks] == ["AAA", "DDD"]
    assert [c.symbol for c in displaced] == ["BBB"]

    # Without long bias the biggest |gap| wins the ranking outright.
    cfg2 = sleeve_config(stock_sleeve_long_bias=False)
    cfg2.strategy.stock_sleeve_symbols = "AAA,BBB,CCC,DDD"
    bt2 = SleeveBacktester(cfg2, cache_dir=cache, rvol_mode="off", offline=True)
    picks2, displaced2 = bt2.picks_for_day(DAY, ["AAA", "BBB", "CCC", "DDD"])
    assert [c.symbol for c in picks2][0] == "BBB"
    assert displaced2 == []


def test_simulate_scan_rvol_premarket_is_rank_weight_not_floor(tmp_path):
    cache = str(tmp_path)
    meta = {"meta": {"start": "2026-01-01", "end": "2026-12-31"}}
    write_cache(cache, "daily_AAA.json", {**meta, "bars": make_dailies(DAY, 100, 102.0)})
    write_cache(cache, "daily_DDD.json", {**meta, "bars": make_dailies(DAY, 100, 103.0)})
    write_cache(cache, "daily_EEE.json", {**meta, "bars": make_dailies(DAY, 100, 105.0)})
    # AAA: 2% gap, hot premarket (0.6x yesterday) → score 1.2
    # DDD: 3% gap, thin premarket (0.1x)         → score 0.3
    # EEE: 5% gap, ZERO premarket (the median IEX morning) → score 0
    write_cache(cache, "scan_AAA.json",
                {"meta": {"start": "2026-01-01", "end": "2026-12-31"},
                 "days": {DAY: {"pm_vol": 600_000, "open": 102.0}}})
    write_cache(cache, "scan_DDD.json",
                {"meta": {"start": "2026-01-01", "end": "2026-12-31"},
                 "days": {DAY: {"pm_vol": 100_000, "open": 103.0}}})
    write_cache(cache, "scan_EEE.json",
                {"meta": {"start": "2026-01-01", "end": "2026-12-31"},
                 "days": {DAY: {"pm_vol": 0, "open": 105.0}}})

    cfg = sleeve_config()
    cfg.strategy.stock_sleeve_symbols = "AAA,DDD,EEE"
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="premarket", offline=True)
    cands = bt.simulate_scan(DAY, ["AAA", "DDD", "EEE"])
    # Nobody is FILTERED for low premarket volume (the live 0.5 floor is not
    # reproducible from IEX history — max measured ratio 0.095); rvol only
    # weights the ranking.
    assert [c.symbol for c in cands] == ["AAA", "DDD", "EEE"]
    assert cands[0].rvol == pytest.approx(0.6)
    assert bt._rvol_fallbacks == 0

    # Missing scan table → rvol falls back to 1.0 and is counted, not fatal.
    os.remove(os.path.join(cache, "scan_DDD.json"))
    bt2 = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="premarket", offline=True)
    cands2 = bt2.simulate_scan(DAY, ["AAA", "DDD", "EEE"])
    assert bt2._rvol_fallbacks == 1
    # DDD's fallback rvol=1.0 makes its score 3.0 → it now outranks AAA.
    assert [c.symbol for c in cands2] == ["DDD", "AAA", "EEE"]


def test_simulate_scan_zero_premarket_ties_break_by_gap(tmp_path):
    """The median IEX morning has ZERO premarket volume for most names —
    scores collapse to 0 and the ranking must degrade to gap size, not
    universe order."""
    cache = str(tmp_path)
    meta = {"meta": {"start": "2026-01-01", "end": "2026-12-31"}}
    write_cache(cache, "daily_AAA.json", {**meta, "bars": make_dailies(DAY, 100, 101.0)})  # +1%
    write_cache(cache, "daily_BBB.json", {**meta, "bars": make_dailies(DAY, 100, 104.0)})  # +4%
    for sym in ("AAA", "BBB"):
        write_cache(cache, f"scan_{sym}.json",
                    {"meta": {"start": "2026-01-01", "end": "2026-12-31"},
                     "days": {DAY: {"pm_vol": 0, "open": None}}})
    cfg = sleeve_config()
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="premarket", offline=True)
    cands = bt.simulate_scan(DAY, ["AAA", "BBB"])
    assert [c.symbol for c in cands] == ["BBB", "AAA"]


# ---------------------------------------------------------------------------
# End-to-end replay
# ---------------------------------------------------------------------------

def test_run_sleeve_replays_one_gap_up_trade(tmp_path):
    cache = str(tmp_path / "cache")
    seed_cache(cache)

    cfg = sleeve_config()
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="off", offline=True)
    result = bt.run_sleeve(DAY, DAY, save=False)

    # Sleeve risk mapping mirrored from the engine.
    assert cfg.risk.max_positions == cfg.strategy.stock_sleeve_max_positions
    assert cfg.strategy.orb_max_entries_per_day >= cfg.strategy.stock_sleeve_max_positions

    assert result.total_trades == 1
    trade = result.trades[0]
    assert trade.symbol == "AAA"
    # entry_fill_next_open: signal on the 9:35 bar, fill at the 9:36 OPEN
    # (base+1.0 = 101.0) plus 10bps slippage.
    assert trade.entry_price == pytest.approx(101.0 * 1.001, rel=1e-6)
    assert trade.exit_reason == "eod_close"
    assert trade.pnl > 0

    day = bt.day_log[0]
    assert [p["symbol"] for p in day["picks"]] == ["AAA"]
    assert [d["symbol"] for d in day["displaced_gap_downs"]] == ["BBB"]
    assert day["trades"] == 1
    assert "qqq_gap_pct" in day
    assert bt.cache.misses == []  # everything came from cache


def test_run_sleeve_red_morning_sits_out(tmp_path):
    cache = str(tmp_path / "cache")
    seed_cache(cache, aaa_open=96.0, bbb_open=97.0)  # both gap DOWN

    cfg = sleeve_config()
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="off", offline=True)
    result = bt.run_sleeve(DAY, DAY, save=False)

    assert result.total_trades == 0
    day = bt.day_log[0]
    assert day["picks"] == []
    assert {d["symbol"] for d in day["displaced_gap_downs"]} == {"AAA", "BBB"}


def test_run_sleeve_range_band_blocks_the_same_trade(tmp_path):
    """The same engineered day produces NO trade when the fixed band floor is
    raised above the day's 0.79% opening range — the gate the live sleeve is
    suspected of over-applying, now measurable."""
    cache = str(tmp_path / "cache")
    seed_cache(cache)

    cfg = sleeve_config()
    cfg.strategy.orb_min_range_pct = 1.0   # floor above the 0.79% range
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="off", offline=True)
    result = bt.run_sleeve(DAY, DAY, save=False)
    assert result.total_trades == 0


def test_run_sleeve_atr_band_rescues_out_of_band_range(tmp_path):
    """With the fixed band the 0.79% range is blocked by a 1.0 floor, but the
    ATR band judges it against THIS symbol's daily ATR (2%): 0.2-0.6 x ATR
    → 0.4-1.2%, so the entry fires. This exercises orb.py's ATR branch AND
    the extra_indicators pass-through end to end."""
    cache = str(tmp_path / "cache")
    seed_cache(cache)

    cfg = sleeve_config()
    cfg.strategy.orb_min_range_pct = 1.0        # fixed band would block
    cfg.strategy.orb_range_band_atr = True
    cfg.strategy.orb_range_atr_lo = 0.2
    cfg.strategy.orb_range_atr_hi = 0.6
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="off", offline=True)
    result = bt.run_sleeve(DAY, DAY, save=False)
    assert result.total_trades == 1
    assert result.trades[0].symbol == "AAA"


# ---------------------------------------------------------------------------
# ORB ATR band unit behavior (strategy level)
# ---------------------------------------------------------------------------

def _orb_eval(cfg: Config, indicators: dict):
    d = datetime.strptime(DAY, "%Y-%m-%d")
    t0 = d.replace(hour=9, minute=30, tzinfo=_ET)

    def bar(i, o, h, l, c):
        return {"t": (t0 + timedelta(minutes=i)).isoformat(),
                "o": o, "h": h, "l": l, "c": c, "v": 10_000}

    # Opening range [100, 102] closing up (2% of price), entry bar at 9:35.
    bars = [
        bar(0, 100.0, 100.8, 100.0, 100.6),
        bar(1, 100.6, 101.2, 100.4, 101.0),
        bar(2, 101.0, 101.6, 100.8, 101.4),
        bar(3, 101.4, 101.9, 101.2, 101.7),
        bar(4, 101.7, 102.0, 101.5, 101.9),
        bar(5, 101.9, 102.2, 101.8, 102.0),
    ]
    strat = ORBStrategy(cfg)
    cand = Candidate(symbol="AAA", price=102.0, prev_close=100.0, gap_pct=0,
                     change_pct=0, volume=10_000, avg_volume=10_000,
                     relative_volume=1.0, high=102.2, low=100.0,
                     open_price=100.0)
    return strat.evaluate(cand, bars, indicators, None)


def test_orb_fixed_band_rejects_two_percent_range():
    cfg = Config()  # fixed band 0.5-1.2; range here is ~1.96%
    assert _orb_eval(cfg, {}) is None


def test_orb_atr_band_accepts_when_scaled_to_volatile_daily_atr():
    cfg = Config()
    cfg.strategy.orb_range_band_atr = True
    cfg.strategy.orb_range_atr_lo = 0.2
    cfg.strategy.orb_range_atr_hi = 0.6
    sig = _orb_eval(cfg, {"daily_atr_pct": 5.0})  # band 1.0%-3.0%
    assert sig is not None and sig.symbol == "AAA"
    assert sig.stop_loss == pytest.approx(100.0)


def test_orb_atr_band_falls_back_to_fixed_when_atr_missing():
    cfg = Config()
    cfg.strategy.orb_range_band_atr = True  # enabled but no daily_atr_pct fed
    assert _orb_eval(cfg, {}) is None       # fixed 0.5-1.2 still rejects 2%


# ---------------------------------------------------------------------------
# Config env knobs
# ---------------------------------------------------------------------------

def test_orb_gate_env_knobs(monkeypatch):
    monkeypatch.setenv("ORB_MIN_RANGE_PCT", "0.3")
    monkeypatch.setenv("ORB_MAX_RANGE_PCT", "2.5")
    monkeypatch.setenv("ORB_ENTRY_WINDOW_MINUTES", "10")
    monkeypatch.setenv("ORB_RANGE_BAND_ATR", "true")
    monkeypatch.setenv("ORB_RANGE_ATR_LO", "0.12")
    monkeypatch.setenv("ORB_RANGE_ATR_HI", "0.9")
    cfg = Config()
    assert cfg.strategy.orb_min_range_pct == 0.3
    assert cfg.strategy.orb_max_range_pct == 2.5
    assert cfg.strategy.orb_entry_window_minutes == 10
    assert cfg.strategy.orb_range_band_atr is True
    assert cfg.strategy.orb_range_atr_lo == 0.12
    assert cfg.strategy.orb_range_atr_hi == 0.9


def test_orb_gate_env_defaults_unchanged(monkeypatch):
    for var in ("ORB_MIN_RANGE_PCT", "ORB_MAX_RANGE_PCT",
                "ORB_ENTRY_WINDOW_MINUTES", "ORB_RANGE_BAND_ATR"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    assert cfg.strategy.orb_min_range_pct == 0.5
    assert cfg.strategy.orb_max_range_pct == 1.2
    assert cfg.strategy.orb_entry_window_minutes == 3
    assert cfg.strategy.orb_range_band_atr is False


# ---------------------------------------------------------------------------
# Backtester extra_indicators pass-through (direct)
# ---------------------------------------------------------------------------

def test_simulate_day_merges_extra_indicators():
    cfg = Config()
    cfg.backtest.entry_fill_next_open = False
    cfg.strategy.orb_min_range_pct = 1.0       # fixed band blocks the 0.79% range
    cfg.strategy.orb_range_band_atr = True
    cfg.strategy.orb_range_atr_lo = 0.2
    cfg.strategy.orb_range_atr_hi = 0.6

    day_bars = {"AAA": make_rth_bars(DAY)}
    bt = Backtester(cfg)

    # Without the extra indicator the ATR band has nothing to scale against →
    # fixed-band fallback blocks the entry.
    risk = RiskManager(cfg.risk)
    risk.reset_daily(100_000.0)
    _, trades = bt._simulate_day(day_bars, [ORBStrategy(cfg)], risk,
                                 100_000.0, {})
    assert trades == []

    risk = RiskManager(cfg.risk)
    risk.reset_daily(100_000.0)
    _, trades = bt._simulate_day(day_bars, [ORBStrategy(cfg)], risk,
                                 100_000.0, {},
                                 extra_indicators={"AAA": {"daily_atr_pct": 2.0}})
    assert len(trades) == 1
    assert trades[0].symbol == "AAA"


# ---------------------------------------------------------------------------
# Calibration + sweep plumbing
# ---------------------------------------------------------------------------

def test_calibrate_against_live_journal_summaries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = str(tmp_path / "cache")
    seed_cache(cache)

    logs = tmp_path / "trade_logs"
    logs.mkdir()
    (logs / f"summary_{DAY}.json").write_text(json.dumps({
        "date": DAY, "trades": 0, "pnl": 0.0,
        "context": {"mode": "stock_sleeve", "picks": ["AAA", "LEVI"]},
    }))
    (logs / "summary_2026-07-05.json").write_text(json.dumps({
        "date": "2026-07-05", "trades": 0, "pnl": 0.0,
        "context": {"mode": "index", "picks": []},   # index day → ignored
    }))

    cfg = sleeve_config()
    bt = SleeveBacktester(cfg, cache_dir=cache, rvol_mode="off", offline=True)
    report = bt.calibrate(str(logs))

    assert report["days_compared"] == 1
    row = report["days"][0]
    assert row["overlap"] == ["AAA"]
    assert row["live_only_outside_universe"] == ["LEVI"]  # the news hot-list gap
    assert report["match_rate_pct"] == 100.0
    assert (tmp_path / "backtest_results" / "sleeve_calibration.json").exists()


def test_run_sweep_reports_train_and_test(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = str(tmp_path / "cache")
    seed_cache(cache)
    monkeypatch.setenv("STOCK_SLEEVE_SYMBOLS", "AAA,BBB")

    import trader.sleeve_backtest as sb
    monkeypatch.setattr(sb, "SWEEP_GRID",
                        [{"name": "fixed_0.5-1.2_(live)", "atr": False,
                          "lo": 0.5, "hi": 1.2}])
    monkeypatch.setattr(sb, "SWEEP_WINDOWS", [3])

    rows = run_sweep(DAY, DAY, train_end=DAY, cache_dir=cache,
                     rvol_mode="off", offline=True)
    assert len(rows) == 1
    assert rows[0]["train"]["trades"] == 1      # the engineered AAA trade
    assert rows[0]["test"]["trades"] == 0       # empty test segment
    saved = list((tmp_path / "backtest_results").glob("sleeve_sweep_*.json"))
    assert len(saved) == 1
