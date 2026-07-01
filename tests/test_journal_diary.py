"""
Session-diary journaling (July 2026 fix).

Before this, a no-trade day persisted only ``{"trades": 0, "pnl": 0}`` — so when
the live sleeve sat idle for a week there was nothing on disk explaining why.
The daily summary now always carries a "context" block: config snapshot +
equity, the day's picks, per-symbol data status (so "no bars for BITF" is
visible), and a tally of skip reasons. Trade-day stats must be unchanged.
"""
import tempfile

from trader.journal import TradeJournal


def _fresh() -> TradeJournal:
    return TradeJournal(log_dir=tempfile.mkdtemp())


def test_no_trade_day_still_records_context():
    j = _fresh()
    j.note_session(mode="stock_sleeve", data_feed="iex", equity_open=100_000.0)
    j.note_picks(["NVDA", "AMD", "BITF"], hotlist=["BITF"])
    j.note_symbol("NVDA", "ok", bars=30, price=123.45)
    j.note_symbol("AMD", "ok", bars=28, price=95.0)
    j.note_symbol("BITF", "no_bars")
    j.note_skip("NVDA", "risk", "max_positions")
    j.note_skip("NVDA", "risk", "max_positions")
    j.note_equity_close(100_000.0)

    s = j.daily_summary()

    # Bottom line still present and correct on a no-trade day.
    assert s["trades"] == 0
    assert s["pnl"] == 0

    ctx = s["context"]
    assert ctx["mode"] == "stock_sleeve"
    assert ctx["data_feed"] == "iex"
    assert ctx["picks"] == ["NVDA", "AMD", "BITF"]
    assert ctx["news_hotlist"] == ["BITF"]
    assert ctx["equity_open"] == 100_000.0
    assert ctx["equity_close"] == 100_000.0

    # Per-symbol data status — the crux of the BITF diagnosis.
    assert ctx["symbols_with_bars"] == ["AMD", "NVDA"]
    assert ctx["symbols_no_bars"] == ["BITF"]
    assert ctx["symbols"]["BITF"]["status"] == "no_bars"
    assert ctx["symbols"]["NVDA"]["bars"] == 30

    # Skip reasons are tallied (deduped with counts).
    assert ctx["skips"]["total"] == 2
    assert ctx["skips"]["by_reason"][0] == {
        "stage": "risk", "reason": "max_positions", "count": 2}


def test_empty_day_has_minimal_context():
    j = _fresh()
    s = j.daily_summary()
    assert s["trades"] == 0 and s["pnl"] == 0
    assert s["context"] == {}          # nothing recorded yet, but still legible


def test_trade_day_keeps_stats_and_adds_context():
    j = _fresh()
    j.note_session(mode="index")
    j.open_trade("SPY", "orb", "long", 10, 100.0, 99.0, 103.0)
    j.close_trade("SPY", 103.0, "take_profit")

    s = j.daily_summary()
    assert s["trades"] == 1
    assert s["winners"] == 1
    assert s["by_strategy"]["orb"]["trades"] == 1
    assert s["total_pnl"] == 30.0            # (103-100)*10
    assert s["context"]["mode"] == "index"   # diary rides alongside the stats
