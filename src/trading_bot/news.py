# src/trading_bot/news.py
from __future__ import annotations
import os, time, logging, requests
from typing import Dict, List, Optional
from dataclasses import dataclass

from .state import get_kv, set_kv

log = logging.getLogger("news")

@dataclass
class NewsArticle:
    """Represents a single news article with sentiment"""
    symbol: str
    headline: str
    summary: Optional[str]
    source: Optional[str]
    url: Optional[str]
    published_at: Optional[str]
    sentiment_score: Optional[float] = None  # -1 (negative) to +1 (positive)

# ---------- small utils ----------
def _utc_iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def _ymd(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))

def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

def _cooldown_active(key: str) -> bool:
    cd = get_kv(key)
    return isinstance(cd, dict) and float(cd.get("until", 0)) > time.time()

def _set_cooldown(key: str, minutes: int, why: str):
    until = time.time() + minutes*60
    set_kv(key, {"until": until, "why": why})
    log.warning("%s cooldown %d min: %s", key, minutes, why)

# ---------- Alpaca News ----------
def fetch_alpaca_news_articles(symbols: List[str], window_hours: int=6) -> List[NewsArticle]:
    key = os.environ.get("ALPACA_API_KEY_ID"); sec = os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        return []
    base = "https://data.alpaca.markets/v1beta1/news"
    headers = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec, "User-Agent": "trading-bot/2.0"}
    end = time.time(); start = end - window_hours*3600
    articles: List[NewsArticle] = []

    # query in chunks
    for group in _chunk([s.upper() for s in symbols[:200]], 50):
        params = {
            "symbols": ",".join(group),
            "start": _utc_iso(start),
            "end": _utc_iso(end),
            "limit": 50,
        }
        try:
            r = requests.get(base, headers=headers, params=params, timeout=6)
            if r.status_code == 429:
                log.warning("Alpaca News 429; backing off this cycle")
                break
            r.raise_for_status()
            data = r.json() or {}
            items = data.get("news") or data.get("news_list") or []
            # create article objects for each symbol
            for it in items:
                syms = it.get("symbols") or it.get("symbols_list") or []
                headline = it.get("headline") or it.get("title") or ""
                summary = it.get("summary") or it.get("content") or ""
                source = it.get("source") or it.get("author") or "Alpaca"
                url = it.get("url") or ""
                published = it.get("created_at") or it.get("updated_at") or ""

                for sym in syms:
                    if sym in group:  # Only include symbols we requested
                        articles.append(NewsArticle(
                            symbol=sym,
                            headline=headline,
                            summary=summary,
                            source=source,
                            url=url,
                            published_at=published
                        ))
        except Exception as e:
            log.warning("Alpaca News error: %s", e)
    return articles

# ---------- Finnhub company news ----------
def fetch_finnhub_articles(symbols: List[str], window_hours: int=6) -> List[NewsArticle]:
    token = os.environ.get("FINNHUB_API_KEY")
    if not token:
        return []
    end = time.time(); start = end - window_hours*3600
    start_d, end_d = _ymd(start), _ymd(end)
    articles: List[NewsArticle] = []
    for s in [x.upper() for x in symbols[:80]]:
        try:
            url = "https://finnhub.io/api/v1/company-news"
            params = {"symbol": s, "from": start_d, "to": end_d, "token": token}
            r = requests.get(url, params=params, timeout=6)
            if r.status_code == 429:
                log.warning("Finnhub 429; backing off this cycle")
                break
            r.raise_for_status()
            arr = r.json() or []
            for item in arr:
                headline = item.get("headline") or ""
                summary = item.get("summary") or ""
                source = item.get("source") or "Finnhub"
                url_link = item.get("url") or ""
                published = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(item.get("datetime", time.time())))

                articles.append(NewsArticle(
                    symbol=s,
                    headline=headline,
                    summary=summary,
                    source=source,
                    url=url_link,
                    published_at=published
                ))
        except Exception as e:
            log.warning("Finnhub error for %s: %s", s, e)
    return articles

# ---------- NewsAPI (as last resort) ----------
def fetch_newsapi_articles(symbols: List[str], window_hours: int=6, batch_size: int=20,
                           cooldown_min: int=120) -> List[NewsArticle]:
    api_key = os.environ.get("NEWSAPI_KEY")
    if not api_key or _cooldown_active("newsapi_cooldown"):
        return []
    end = time.time(); start = end - window_hours*3600
    headers = {"User-Agent": "trading-bot/2.0"}
    articles: List[NewsArticle] = []
    for s in [x.upper() for x in symbols[:100]]:
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                "q": s, "language": "en", "sortBy": "publishedAt", "pageSize": 20,
                "from": _utc_iso(start), "to": _utc_iso(end), "apiKey": api_key,
            }
            r = requests.get(url, params=params, headers=headers, timeout=6)
            if r.status_code == 429:
                log.warning("NewsAPI 429 rateLimited; enabling cooldown for %d min", cooldown_min)
                _set_cooldown("newsapi_cooldown", cooldown_min, "rateLimited")
                return articles
            r.raise_for_status()
            data = r.json() or {}
            items = data.get("articles") or []
            for item in items:
                headline = item.get("title") or ""
                summary = item.get("description") or item.get("content") or ""
                source_obj = item.get("source") or {}
                source = source_obj.get("name") if isinstance(source_obj, dict) else "NewsAPI"
                url_link = item.get("url") or ""
                published = item.get("publishedAt") or ""

                articles.append(NewsArticle(
                    symbol=s,
                    headline=headline,
                    summary=summary,
                    source=source,
                    url=url_link,
                    published_at=published
                ))
        except Exception as e:
            log.warning("NewsAPI error for %s: %s", s, e)
    return articles

# ---------- Sentiment Analysis ----------
def analyze_sentiment(text: str) -> float:
    """
    Analyze sentiment of text using TextBlob.
    Returns a score from -1 (negative) to +1 (positive).
    Falls back to simple keyword matching if TextBlob fails.
    """
    try:
        from textblob import TextBlob
        blob = TextBlob(text)
        return blob.sentiment.polarity
    except ImportError:
        log.warning("TextBlob not available, using fallback sentiment analysis")
        # Simple fallback: count positive and negative keywords
        positive_words = ['gain', 'profit', 'up', 'growth', 'surge', 'rally', 'bullish',
                         'positive', 'strong', 'beat', 'exceed', 'rise', 'soar', 'jump',
                         'breakthrough', 'success', 'win', 'opportunity', 'improve']
        negative_words = ['loss', 'down', 'decline', 'fall', 'drop', 'crash', 'bearish',
                         'negative', 'weak', 'miss', 'fail', 'plunge', 'tumble', 'slide',
                         'concern', 'worry', 'risk', 'threat', 'problem', 'issue']

        text_lower = text.lower()
        pos_count = sum(1 for word in positive_words if word in text_lower)
        neg_count = sum(1 for word in negative_words if word in text_lower)

        total = pos_count + neg_count
        if total == 0:
            return 0.0
        return (pos_count - neg_count) / total
    except Exception as e:
        log.warning("Error analyzing sentiment: %s", e)
        return 0.0

def analyze_articles_sentiment(articles: List[NewsArticle]) -> List[NewsArticle]:
    """Analyze sentiment for each article and update the sentiment_score field."""
    for article in articles:
        # Combine headline and summary for sentiment analysis
        text = f"{article.headline} {article.summary or ''}"
        article.sentiment_score = analyze_sentiment(text)
    return articles

# ---------- Orchestrator the engine should call ----------
def get_news_articles(symbols: List[str], window_hours: int, provider_order: List[str],
                      rotate_key: str="news_rotate_idx", rotate_batch: int=60) -> List[NewsArticle]:
    """
    Try providers in order; rotate through the universe to avoid hammering APIs.
    Returns list of NewsArticle objects with sentiment analysis.
    """
    symbols = [s.upper() for s in (symbols or [])]
    if not symbols:
        return []
    # rotation window to spread calls across loops
    idx = int(get_kv(rotate_key, 0) or 0)
    start = (idx * rotate_batch) % max(len(symbols), 1)
    sub = symbols[start:start+rotate_batch] or symbols[:rotate_batch]
    set_kv(rotate_key, idx + 1)

    articles: List[NewsArticle] = []
    for prov in provider_order:
        if prov == "alpaca":
            articles = fetch_alpaca_news_articles(sub, window_hours)
        elif prov == "finnhub":
            articles = fetch_finnhub_articles(sub, window_hours)
        elif prov == "newsapi":
            articles = fetch_newsapi_articles(sub, window_hours)
        else:
            continue
        if articles:
            # Analyze sentiment for all articles
            articles = analyze_articles_sentiment(articles)
            return articles
    return articles  # may be []

# Backward compatibility: provide counts from articles
def get_news_counts(symbols: List[str], window_hours: int, provider_order: List[str],
                    rotate_key: str="news_rotate_idx", rotate_batch: int=60) -> Dict[str,int]:
    """Legacy function for backward compatibility - returns article counts."""
    articles = get_news_articles(symbols, window_hours, provider_order, rotate_key, rotate_batch)
    counts: Dict[str, int] = {}
    for article in articles:
        counts[article.symbol] = counts.get(article.symbol, 0) + 1
    return counts