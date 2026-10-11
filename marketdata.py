"""
Shared market context for the daily reports: per-symbol trend/return stats
from Alpaca daily bars, and a per-symbol news digest from Alpaca's free
(Benzinga-sourced) news feed. Used by heartbeat.py (paper account) and
advisor/daily_review.py (IRA) so both reports describe holdings the same way.

Everything degrades gracefully: a symbol with no bars gets an empty stats
row, a news fetch failure yields no articles. These reports inform a human;
they never gate an order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from .core import TrendStatus, sma_from_bars
from .news import NewsArticle, fetch_alpaca_news, score_articles

logger = logging.getLogger(__name__)


@dataclass
class SymbolStats:
    symbol: str
    close: Optional[float] = None       # last completed daily close
    as_of: str = ""
    sma: Optional[float] = None
    above_trend: Optional[bool] = None
    ret_1d: Optional[float] = None      # % change, last close vs prior close
    ret_1w: Optional[float] = None      # ~5 sessions
    ret_1m: Optional[float] = None      # ~21 sessions
    dist_to_sma_pct: Optional[float] = None
    bars: int = 0

    @property
    def trend_label(self) -> str:
        if self.above_trend is None:
            return "n/a"
        return "above" if self.above_trend else "BELOW"


def _pct(a: float, b: float) -> Optional[float]:
    return ((a / b) - 1.0) * 100.0 if a and b else None


def stats_from_bars(symbol: str, bars: List[Dict], sma_n: int,
                    before: Optional[str] = None) -> SymbolStats:
    t: TrendStatus = sma_from_bars(bars, sma_n, before=before)
    closes: List[float] = []
    cutoff = before or datetime.now(timezone.utc).date().isoformat()
    for b in bars:
        d = str(b.get("t", ""))[:10]
        if d and d < cutoff:
            try:
                c = float(b.get("c", 0) or 0)
            except (TypeError, ValueError):
                continue
            if c > 0:
                closes.append(c)
    st = SymbolStats(symbol=symbol, close=t.close, as_of=t.as_of, sma=t.sma,
                     above_trend=t.above, bars=len(closes))
    if closes:
        last = closes[-1]
        if len(closes) >= 2:
            st.ret_1d = _pct(last, closes[-2])
        if len(closes) >= 6:
            st.ret_1w = _pct(last, closes[-6])
        if len(closes) >= 22:
            st.ret_1m = _pct(last, closes[-22])
        if t.sma:
            st.dist_to_sma_pct = _pct(last, t.sma)
    return st


def fetch_stats(broker, symbol: str, sma_n: int = 200) -> SymbolStats:
    start = (datetime.now(timezone.utc) - timedelta(days=int(sma_n * 1.6) + 30)).strftime("%Y-%m-%d")
    try:
        bars = broker.get_bars(symbol, timeframe="1Day", start=start, limit=sma_n + 40)
    except Exception as exc:  # noqa: BLE001
        logger.warning("marketdata: daily bars failed for %s: %s", symbol, exc)
        bars = []
    return stats_from_bars(symbol, bars or [], sma_n)


def fetch_stats_many(broker, symbols: Sequence[str], sma_n: int = 200) -> Dict[str, SymbolStats]:
    return {s: fetch_stats(broker, s, sma_n) for s in symbols}


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

@dataclass
class NewsDigest:
    symbol: str
    articles: List[NewsArticle] = field(default_factory=list)
    mean_sentiment: Optional[float] = None

    @property
    def flag(self) -> str:
        """'negative' / 'positive' when 2+ articles lean clearly one way."""
        if self.mean_sentiment is None or len(self.articles) < 2:
            return ""
        if self.mean_sentiment <= -0.3:
            return "negative"
        if self.mean_sentiment >= 0.4:
            return "positive"
        return ""


def fetch_news_digest(api_key: str, api_secret: str, symbols: Sequence[str],
                      *, hours: int = 36, per_symbol: int = 3,
                      session=None) -> Dict[str, NewsDigest]:
    """Newest `per_symbol` headlines per name from the last `hours`, scored
    with the same lexicon the engine's news gate uses."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    out: Dict[str, NewsDigest] = {}
    if not api_key or not api_secret:
        return {s: NewsDigest(s) for s in symbols}
    for s in symbols:
        try:
            arts = fetch_alpaca_news([s], start, end, api_key=api_key, api_secret=api_secret,
                                     limit=10, max_pages=1, session=session, sort="desc")
            arts = score_articles(arts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("marketdata: news failed for %s: %s", s, exc)
            arts = []
        arts.sort(key=lambda a: a.published_at or "", reverse=True)
        scores = [a.sentiment_score for a in arts if a.sentiment_score is not None]
        out[s] = NewsDigest(s, arts[:per_symbol],
                            (sum(scores) / len(scores)) if scores else None)
    return out
