"""
Stock-sleeve news / catalyst layer (default OFF).

  - market-wide parse keeps every tagged symbol (catalyst sourcing);
  - rank_catalysts scores by volume + recency, honors the window and min count;
  - allow_entry_long blocks a long into strongly-negative news, abstains on thin;
  - the engine gate is a no-op when off, blocks a negative name when on, and the
    daily hot-list augments the scan universe.
"""
from datetime import datetime, timedelta, timezone

from trader.config import Config
from trader.engine import Engine
from trader.news import (
    NewsArticle, NewsFeed, _parse_news_items, rank_catalysts, hotlist_symbols,
    allow_entry_long,
)

T0 = datetime(2026, 6, 29, 14, 0, tzinfo=timezone.utc)


def _art(symbol, minutes_from_t0, sentiment=0.0):
    ts = (T0 + timedelta(minutes=minutes_from_t0)).isoformat()
    return NewsArticle(symbol, "h", "s", "src", "u", ts, sentiment_score=sentiment)


# --------------------------------------------------------------------------
# Sourcing primitives
# --------------------------------------------------------------------------

def test_parse_market_wide_keeps_all_symbols():
    items = [{"headline": "H", "symbols": ["AAA", "ZZZ"], "created_at": "2026-06-29T13:30:00Z"}]
    syms = sorted(a.symbol for a in _parse_news_items(items))      # requested=None
    assert syms == ["AAA", "ZZZ"]


def test_rank_catalysts_volume_recency_and_window():
    arts = [
        _art("AAA", -10), _art("AAA", -20), _art("AAA", -300),   # 2 in a 120m window
        _art("BBB", -5),                                          # 1 -> below min
        _art("CCC", -90), _art("CCC", -100),                     # 2, older than AAA
    ]
    cats = rank_catalysts(arts, T0, window_min=120, min_articles=2)
    syms = [c.symbol for c in cats]
    assert syms == ["AAA", "CCC"]              # AAA more recent -> higher score; BBB dropped
    assert cats[0].count == 2
    assert hotlist_symbols(cats, 1) == ["AAA"]


# --------------------------------------------------------------------------
# Long-only entry gate
# --------------------------------------------------------------------------

def test_allow_entry_long_blocks_strong_negative():
    feed = NewsFeed([_art("CCC", -10, -0.6), _art("CCC", -20, -0.6)])
    ok, _ = allow_entry_long(feed, T0, "CCC", block_below=-0.4, min_articles=2)
    assert ok is False


def test_allow_entry_long_allows_positive_and_thin():
    pos = NewsFeed([_art("CCC", -10, 0.5), _art("CCC", -20, 0.4)])
    assert allow_entry_long(pos, T0, "CCC", block_below=-0.4, min_articles=2)[0] is True
    thin = NewsFeed([_art("CCC", -10, -0.9)])                     # only 1 article
    ok, why = allow_entry_long(thin, T0, "CCC", min_articles=2)
    assert ok is True and "thin" in why


# --------------------------------------------------------------------------
# Engine wiring
# --------------------------------------------------------------------------

def _snap(price, prev_close, day_vol, prev_vol, open_):
    return {
        "latestTrade": {"p": price},
        "dailyBar": {"o": open_, "h": price * 1.01, "l": open_ * 0.99, "c": price, "v": day_vol},
        "prevDailyBar": {"c": prev_close, "v": prev_vol},
        "minuteBar": {"c": price},
    }


class FakeBroker:
    def __init__(self, snapshots=None):
        self.snapshots = snapshots or {}

    def get_snapshots(self, symbols):
        return {s: self.snapshots[s] for s in symbols if s in self.snapshots}


def _engine(**strat):
    config = Config()
    for k, v in strat.items():
        setattr(config.strategy, k, v)
    return Engine(config)


def test_news_gate_off_by_default():
    engine = _engine(stock_sleeve_enabled=True)          # news layer off
    assert engine._news_gate("AAA") == (True, "")


def test_news_gate_blocks_negative_name():
    engine = _engine(stock_sleeve_enabled=True, stock_sleeve_news_enabled=True,
                     stock_sleeve_news_block_below=-0.4, stock_sleeve_news_gate_min_articles=2)
    now = datetime.now(timezone.utc)
    engine._news_feed = NewsFeed([
        NewsArticle("CCC", "h", "s", "src", "u",
                    (now - timedelta(minutes=10)).isoformat(), sentiment_score=-0.6),
        NewsArticle("CCC", "h", "s", "src", "u",
                    (now - timedelta(minutes=20)).isoformat(), sentiment_score=-0.6),
    ])
    assert engine._news_gate("CCC")[0] is False          # blocked: strong negative
    assert engine._news_gate("ZZZ")[0] is True           # no news -> fail-open


def test_news_hotlist_augments_scan_universe():
    snaps = {
        "AAA": _snap(100, 98, 600_000, 400_000, 99),
        "BBB": _snap(50, 49, 300_000, 200_000, 49.3),
        "CCC": _snap(80, 76, 800_000, 400_000, 78),      # catalyst name, not in base
    }
    engine = _engine(stock_sleeve_enabled=True, stock_sleeve_news_enabled=True,
                     stock_sleeve_symbols="AAA,BBB", stock_sleeve_max_candidates=5)
    engine.broker = FakeBroker(snapshots=snaps)
    # Stub the network refresh: supply a catalyst hot-list directly.
    engine._refresh_news = lambda: setattr(engine, "_news_hotlist", ["CCC"])

    engine._ensure_sleeve_symbols()
    assert "CCC" in engine.symbols                        # pulled in from the news hot-list
    assert set(engine.symbols) <= {"AAA", "BBB", "CCC"}
