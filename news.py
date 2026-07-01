"""
News + sentiment for the ETF engine.

Salvaged from the old src/trading_bot/news.py and reshaped for the index bot.
This module is three things: (1) an Alpaca news fetch that takes start/end, so
the SAME call powers live (now-window .. now) and backtest (a full date range);
(2) a point-in-time NewsFeed whose `as_of` query never returns an article
published after the query time (the lookahead guard the whole backtest hinges
on); (3) a flag-gated entry filter you can A/B.

It is OFF by default (config.strategy.news_enabled) and changes no live
behavior until enabled. Live-engine and backtester wiring are left as documented
hook points (see INTEGRATION at the bottom) so the running bot isn't touched
blind.

Sentiment uses VADER if installed, else a small built-in lexicon, so the module
imports and runs with no extra dependency. `pip install vaderSentiment` upgrades
the quality without any code change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from .universe import valid_symbol

logger = logging.getLogger(__name__)

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

# Optional, better sentiment model. Absence is fine — we fall back to a lexicon.
try:  # pragma: no cover - depends on the environment
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _VADER = SentimentIntensityAnalyzer()
except Exception:  # pragma: no cover
    _VADER = None


def _to_dt(ts) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (Alpaca uses '...Z') to an aware UTC datetime.
    Accepts a datetime (coerced to UTC) or None."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(ts) -> str:
    dt = _to_dt(ts)
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


@dataclass
class NewsArticle:
    symbol: str
    headline: str
    summary: str
    source: str
    url: str
    published_at: str                       # ISO-8601
    sentiment_score: Optional[float] = None  # -1 (neg) .. +1 (pos)

    @property
    def published_dt(self) -> Optional[datetime]:
        return _to_dt(self.published_at)


# ---------------------------------------------------------------------------
# Fetch (historical-capable: the same call serves live and backtest)
# ---------------------------------------------------------------------------

def _parse_news_items(items: List[Dict],
                      requested: Optional[Sequence[str]] = None) -> List[NewsArticle]:
    """Pure parser for Alpaca news payload items → one NewsArticle per tagged
    symbol. With `requested` set, keep only those symbols (per-name fetch); with
    requested=None keep EVERY tagged symbol (market-wide catalyst sourcing).
    Separated from the HTTP call so it is unit testable without network."""
    want = {s.upper() for s in requested} if requested else None
    out: List[NewsArticle] = []
    for it in items or []:
        headline = it.get("headline") or it.get("title") or ""
        summary = it.get("summary") or it.get("content") or ""
        source = it.get("source") or it.get("author") or "Alpaca"
        url = it.get("url") or ""
        published = it.get("created_at") or it.get("updated_at") or ""
        for sym in (it.get("symbols") or []):
            su = str(sym).strip().upper()
            if not valid_symbol(su):
                # Trust boundary: feed-supplied tags flow into the scan
                # universe, REST URL paths and journal CSVs — drop anything
                # that isn't shaped like a ticker.
                logger.debug(f"news: dropping invalid symbol tag {sym!r}")
                continue
            if want is None or su in want:
                out.append(NewsArticle(su, headline, summary, source, url, published))
    return out


def fetch_alpaca_news(
    symbols: Optional[Sequence[str]],
    start,
    end,
    *,
    api_key: str,
    api_secret: str,
    limit: int = 50,
    max_pages: int = 100,
    session: Optional[requests.Session] = None,
    sort: str = "asc",
) -> List[NewsArticle]:
    """Fetch articles between `start` and `end` (ISO strings or datetimes).
    Pass a `symbols` list for a per-name fetch, or None/[] for MARKET-WIDE news
    (catalyst sourcing — every tagged symbol is returned). `sort="desc"` returns
    newest-first, so a page cap keeps the most recent articles. Reuses the
    Alpaca keys you already have; paginates via next_page_token. Returns UNscored
    articles; call score_articles() after."""
    sess = session or requests.Session()
    headers = {
        "Apca-Api-Key-Id": api_key,
        "Apca-Api-Secret-Key": api_secret,
        "User-Agent": "trader/news",
    }
    articles: List[NewsArticle] = []
    page_token: Optional[str] = None
    for _ in range(max_pages):
        params = {
            "start": _iso(start),
            "end": _iso(end),
            "limit": limit,
            "sort": sort,
        }
        if symbols:
            params["symbols"] = ",".join(s.upper() for s in symbols)
        if page_token:
            params["page_token"] = page_token
        try:
            r = sess.get(ALPACA_NEWS_URL, headers=headers, params=params, timeout=10)
            if r.status_code == 429:
                logger.warning("Alpaca news rate-limited; stopping this fetch")
                break
            r.raise_for_status()
            data = r.json() or {}
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Alpaca news fetch error: {exc}")
            break
        articles.extend(_parse_news_items(data.get("news") or [], symbols))
        page_token = data.get("next_page_token")
        if not page_token:
            break
    return articles


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------

_POS = {"beat", "beats", "surge", "surges", "soar", "soars", "rally", "rallies",
        "jump", "jumps", "gain", "gains", "record", "strong", "upgrade",
        "upgraded", "tops", "outperform", "bullish", "rebound", "rises", "rose"}
_NEG = {"miss", "misses", "plunge", "plunges", "slump", "slumps", "fall", "falls",
        "drop", "drops", "downgrade", "downgraded", "cut", "cuts", "weak",
        "warns", "warning", "lawsuit", "probe", "recall", "selloff", "sinks",
        "bearish", "tumble", "tumbles", "slashes", "fears"}


def analyze_sentiment(text: str) -> float:
    """Return polarity in [-1, 1]. VADER when available, else a lexicon fallback
    so this works with no extra dependency."""
    if not text:
        return 0.0
    if _VADER is not None:
        return float(_VADER.polarity_scores(text)["compound"])
    words = [w.strip(".,!?:;'\"()").lower() for w in text.split()]
    pos = sum(w in _POS for w in words)
    neg = sum(w in _NEG for w in words)
    if pos + neg == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / (pos + neg)))


def score_articles(articles: List[NewsArticle]) -> List[NewsArticle]:
    for a in articles:
        a.sentiment_score = analyze_sentiment(f"{a.headline}. {a.summary}")
    return articles


# ---------------------------------------------------------------------------
# Point-in-time feed (NO lookahead)
# ---------------------------------------------------------------------------

class NewsFeed:
    """Holds scored articles and answers as-of queries. `as_of(ts)` returns only
    articles published at or before ts, so it is safe to call bar-by-bar in a
    backtest without leaking the future."""

    def __init__(self, articles: List[NewsArticle]):
        self._articles = sorted(
            (a for a in articles if a.published_dt is not None),
            key=lambda a: a.published_dt,
        )

    def as_of(self, ts, *, symbols: Optional[Sequence[str]] = None,
              window_min: int = 120) -> List[NewsArticle]:
        now = _to_dt(ts)
        if now is None:
            return []
        lo = now - timedelta(minutes=window_min)
        want = {s.upper() for s in symbols} if symbols else None
        return [
            a for a in self._articles
            if lo < a.published_dt <= now and (want is None or a.symbol in want)
        ]

    def sentiment_as_of(self, ts, *, symbols: Optional[Sequence[str]] = None,
                        window_min: int = 120) -> Tuple[int, Optional[float]]:
        """(article_count, mean_sentiment) over the as-of window. mean is None
        when there are no scored articles."""
        arts = self.as_of(ts, symbols=symbols, window_min=window_min)
        scored = [a.sentiment_score for a in arts if a.sentiment_score is not None]
        if not scored:
            return len(arts), None
        return len(arts), sum(scored) / len(scored)


# ---------------------------------------------------------------------------
# Flag-gated entry filter (v1 rule — this is the A/B knob)
# ---------------------------------------------------------------------------

def allow_entry(
    feed: NewsFeed,
    ts,
    bullish: bool,
    *,
    window_min: int = 120,
    block_below: float = -0.35,
    min_articles: int = 2,
    symbols: Optional[Sequence[str]] = None,
) -> Tuple[bool, str]:
    """Decide whether to allow an entry given recent news sentiment.

    `bullish` is the trade's QQQ-equivalent direction: long TQQQ is bullish,
    long SQQQ is bearish. v1 rule: with enough coverage, block trades that fight
    strong sentiment — block bullish entries when mean sentiment <= block_below,
    and block bearish entries when mean sentiment >= -block_below. Returns
    (allow, reason); allows by default when coverage is thin.
    """
    count, mean = feed.sentiment_as_of(ts, symbols=symbols, window_min=window_min)
    if mean is None or count < min_articles:
        return True, f"news: thin coverage (n={count})"
    if bullish and mean <= block_below:
        return False, f"news: blocked long, sentiment {mean:+.2f} over {count}"
    if (not bullish) and mean >= -block_below:
        return False, f"news: blocked short, sentiment {mean:+.2f} over {count}"
    return True, f"news: ok (sentiment {mean:+.2f}, n={count})"


# ---------------------------------------------------------------------------
# Catalyst sourcing (which names have a fresh news burst today)
# ---------------------------------------------------------------------------

@dataclass
class CatalystScore:
    symbol: str
    count: int                       # articles in the window
    latest: str                      # ISO of the most recent article
    mean_sentiment: Optional[float]  # mean polarity (None if unscored)
    score: float                     # volume + recency, higher = stronger


def rank_catalysts(
    articles: List[NewsArticle], as_of, *,
    window_min: int = 1080, min_articles: int = 2,
) -> List["CatalystScore"]:
    """Tally per-symbol news in (as_of - window, as_of], score each by volume
    plus a recency bonus, and return CatalystScores best-first. Pure (no
    network). Use the symbols of the top results as the day's catalyst hot-list.
    """
    now = _to_dt(as_of)
    if now is None:
        return []
    lo = now - timedelta(minutes=window_min)
    span = (now - lo).total_seconds() or 1.0
    by_sym: Dict[str, List[NewsArticle]] = {}
    for a in articles:
        pdt = a.published_dt
        if pdt is None or not (lo < pdt <= now):
            continue
        by_sym.setdefault(a.symbol, []).append(a)

    out: List[CatalystScore] = []
    for sym, arts in by_sym.items():
        if len(arts) < min_articles:
            continue
        latest = max(a.published_dt for a in arts)
        recency = (latest - lo).total_seconds() / span      # 0..1 (newer -> 1)
        scored = [a.sentiment_score for a in arts if a.sentiment_score is not None]
        mean = sum(scored) / len(scored) if scored else None
        out.append(CatalystScore(sym, len(arts), latest.isoformat(), mean,
                                  round(len(arts) + recency, 3)))
    out.sort(key=lambda c: (c.score, c.latest), reverse=True)
    return out


def hotlist_symbols(catalysts: Sequence["CatalystScore"], n: int) -> List[str]:
    """Top-n catalyst symbols (the day's news hot-list)."""
    return [c.symbol for c in catalysts[:n]]


def allow_entry_long(
    feed: NewsFeed, ts, symbol: str, *,
    window_min: int = 120, block_below: float = -0.4, min_articles: int = 2,
) -> Tuple[bool, str]:
    """Long-only per-stock news gate: block a breakout long into strongly
    negative recent news, allow when coverage is thin or sentiment is not clearly
    bad. Returns (allow, reason). The stock-sleeve analogue of allow_entry."""
    count, mean = feed.sentiment_as_of(ts, symbols=[symbol], window_min=window_min)
    if mean is None or count < min_articles:
        return True, f"news thin ({symbol} n={count})"
    if mean <= block_below:
        return False, f"news blocked {symbol} long: sentiment {mean:+.2f} over {count}"
    return True, f"news ok {symbol} ({mean:+.2f}, n={count})"


# ---------------------------------------------------------------------------
# INTEGRATION (left as hooks; default OFF, so nothing changes until wired)
# ---------------------------------------------------------------------------
# Live engine (engine.py): when config.strategy.news_enabled, keep a NewsFeed
#   refreshed on an interval, e.g. every 15-30 min:
#       arts = score_articles(fetch_alpaca_news(
#           cfg.news_symbols, now - timedelta(minutes=cfg.news_window_min), now,
#           api_key=broker.config.api_key, api_secret=broker.config.api_secret))
#       self._news_feed = NewsFeed(arts)
#   then gate _open_position: bullish = (signal long on TQQQ) or (signal short
#   on SQQQ); ok, why = allow_entry(self._news_feed, now, bullish,
#       window_min=cfg.news_window_min, block_below=cfg.news_block_below,
#       min_articles=cfg.news_min_articles, symbols=cfg.news_symbols)
#   if not ok: log why and skip the entry.
#
# Backtest (backtest.py): pre-fetch ONCE for the whole window —
#       feed = NewsFeed(score_articles(fetch_alpaca_news(cfg.news_symbols,
#               start_date, end_date, api_key=..., api_secret=...)))
#   then in the per-bar loop, before accepting an entry, call allow_entry(feed,
#   bar_time, bullish, ...). as_of guarantees no lookahead. A/B by toggling
#   NEWS_ENABLED, exactly like the falling-knife guard.
