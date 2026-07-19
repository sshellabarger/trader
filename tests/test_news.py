"""
News module coverage (all offline — no network, no extra deps required):
  - the Alpaca payload parser expands one article per requested symbol only;
  - NewsFeed.as_of is lookahead-safe (excludes future and out-of-window news);
  - sentiment polarity has the right sign (VADER if present, else lexicon);
  - the flag-gated entry filter blocks trades that fight strong sentiment and
    abstains when coverage is thin.
"""
from datetime import datetime, timedelta, timezone

from trader.news import (
    NewsArticle, NewsFeed, allow_entry, analyze_sentiment, _parse_news_items,
)

T0 = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)


def _article(minutes_from_t0, score, symbol="QQQ"):
    ts = (T0 + timedelta(minutes=minutes_from_t0)).isoformat()
    return NewsArticle(symbol, "h", "s", "src", "u", ts, sentiment_score=score)


def test_parse_items_expands_only_requested_symbols():
    items = [{
        "headline": "H", "summary": "S", "symbols": ["QQQ", "AAPL"],
        "created_at": "2026-06-22T13:30:00Z", "source": "x", "url": "u",
    }]
    arts = _parse_news_items(items, requested=["QQQ"])
    assert len(arts) == 1
    assert arts[0].symbol == "QQQ"
    assert arts[0].headline == "H"
    assert arts[0].published_at == "2026-06-22T13:30:00Z"


def test_as_of_is_lookahead_safe():
    feed = NewsFeed([
        _article(-150, -0.2),   # before the 120-min window -> excluded
        _article(-30, -0.5),    # inside the window -> included
        _article(+30, +0.9),    # AFTER the query time -> must be excluded
    ])
    got = feed.as_of(T0, window_min=120)
    assert len(got) == 1
    assert got[0].published_dt == T0 - timedelta(minutes=30)


def test_sentiment_sign(monkeypatch):
    # Force the lexicon fallback so the assertion is deterministic across
    # environments: VADER's own lexicon lacks plunge/downgrade/lawsuit and
    # scores "Shares ..." slightly POSITIVE ('share' is a positive word
    # there), so this test only passed on machines without VADER installed.
    import trader.news as news_mod
    monkeypatch.setattr(news_mod, "_VADER", None)
    assert analyze_sentiment("Stocks surge to record high on strong earnings beat") > 0
    assert analyze_sentiment("Shares plunge after downgrade and lawsuit") < 0


def test_allow_entry_blocks_longs_on_strong_negative():
    feed = NewsFeed([_article(-20, -0.6), _article(-10, -0.6), _article(-5, -0.6)])
    ok_long, _ = allow_entry(feed, T0, bullish=True, min_articles=2, block_below=-0.35)
    ok_short, _ = allow_entry(feed, T0, bullish=False, min_articles=2, block_below=-0.35)
    assert ok_long is False        # don't buy the bull ETF into strongly negative news
    assert ok_short is True         # bearish trade is fine on negative news


def test_allow_entry_abstains_on_thin_coverage():
    feed = NewsFeed([_article(-10, -0.9)])   # only 1 article
    ok, reason = allow_entry(feed, T0, bullish=True, min_articles=2)
    assert ok is True
    assert "thin" in reason
