"""
Complete Strategy Signals Module
Includes all 8 signal types: momentum, mean_reversion, news, volume,
earnings, longterm_trend, longterm_momentum, and crypto
"""
from __future__ import annotations
from typing import Dict, Tuple, List
import math

# Import NewsArticle for type hints
try:
    from .news import NewsArticle
except ImportError:
    NewsArticle = None


def _mover_from_snap(snap: dict) -> float:
    """Calculate intraday mover percentage from snapshot"""
    mb = (snap.get("minuteBar") or {}).get("c")
    db = (snap.get("dailyBar") or {}).get("c")
    try:
        if mb and db and float(db) > 0:
            return float(mb)/float(db) - 1.0
    except Exception:
        pass
    return 0.0


def score_momentum(current_price: float, open_price: float, prev_close: float, 
                   high: float, low: float) -> Tuple[float, Dict]:
    """
    Score momentum strategy
    Looks at intraday change, gap, and position in range
    """
    details = {}
    
    # Intraday change
    intraday_change = (current_price - open_price) / open_price if open_price > 0 else 0
    details['intraday_change_pct'] = intraday_change * 100
    
    # Gap from previous close
    gap = (open_price - prev_close) / prev_close if prev_close > 0 else 0
    details['gap_pct'] = gap * 100
    
    # Position in daily range
    price_range = high - low
    if price_range > 0:
        position_in_range = (current_price - low) / price_range
    else:
        position_in_range = 0.5
    details['position_in_range'] = position_in_range
    
    # Score calculation
    score = (intraday_change * 5.0 + gap * 2.0 + (position_in_range - 0.5) * 0.3)
    score = max(0, min(1, (score + 0.5)))
    details['raw_score'] = score
    
    return score, details


def score_mean_reversion(current_price: float, open_price: float, prev_close: float,
                         high: float, low: float) -> Tuple[float, Dict]:
    """
    Score mean reversion strategy
    Looks for oversold conditions that may bounce back
    """
    details = {}
    
    # Deviation from previous close
    deviation = (prev_close - current_price) / prev_close if prev_close > 0 else 0
    details['deviation_pct'] = deviation * 100
    
    # Position in range (lower = more oversold)
    price_range = high - low
    if price_range > 0:
        position_in_range = (current_price - low) / price_range
    else:
        position_in_range = 0.5
    details['position_in_range'] = position_in_range
    
    # Score: higher when price has dropped and is near low of range
    if deviation > 0:
        score = deviation * 5.0 + (1 - position_in_range) * 0.3
    else:
        score = 0
    
    score = max(0, min(1, score))
    details['raw_score'] = score
    
    return score, details


def score_news(symbol: str, news_data, window_hours: int = 6) -> Tuple[float, Dict]:
    """
    Score based on news activity and sentiment analysis.

    Args:
        symbol: Stock symbol
        news_data: Either a List[NewsArticle] or Dict[str, int] (for backward compatibility)
        window_hours: Time window for news

    Returns:
        Tuple of (score, details dict)

    Scoring approach:
    - Each article contributes based on its sentiment (-1 to +1)
    - Positive sentiment increases score, negative decreases it
    - Multiple positive articles compound the effect
    - Final score is normalized to 0-1 range
    """
    details = {}
    details['window_hours'] = window_hours

    # Backward compatibility: handle old dict format
    if isinstance(news_data, dict):
        count = news_data.get(symbol, 0)
        details['news_count'] = count
        details['legacy_mode'] = True

        if count > 0:
            # Logarithmic scaling: 1 article = ~0.3, 5 articles = ~0.6, 20 articles = 1.0
            score = min(1.0, math.log(count + 1) / math.log(20))
        else:
            score = 0
        details['score'] = score
        return score, details

    # New approach: use sentiment analysis
    articles = [a for a in news_data if a.symbol == symbol] if news_data else []

    if not articles:
        details['news_count'] = 0
        details['avg_sentiment'] = 0
        details['score'] = 0
        return 0, details

    details['news_count'] = len(articles)

    # Calculate weighted sentiment score
    sentiment_scores = [a.sentiment_score for a in articles if a.sentiment_score is not None]

    if not sentiment_scores:
        # Fallback if no sentiment scores available
        details['avg_sentiment'] = 0
        score = min(1.0, math.log(len(articles) + 1) / math.log(20))
        details['score'] = score
        return score, details

    avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
    details['avg_sentiment'] = round(avg_sentiment, 3)

    # Sentiment distribution for debugging
    positive_count = sum(1 for s in sentiment_scores if s > 0.1)
    negative_count = sum(1 for s in sentiment_scores if s < -0.1)
    neutral_count = len(sentiment_scores) - positive_count - negative_count

    details['positive_articles'] = positive_count
    details['negative_articles'] = negative_count
    details['neutral_articles'] = neutral_count

    # Calculate score based on sentiment and volume
    # 1. Start with article volume score (0-0.5 range)
    volume_score = min(0.5, math.log(len(articles) + 1) / math.log(20) * 0.5)

    # 2. Add sentiment component (0-0.5 range, can be negative)
    # Positive sentiment adds to score, negative subtracts
    sentiment_component = avg_sentiment * 0.5

    # 3. Combine: volume shows activity, sentiment shows direction
    raw_score = volume_score + sentiment_component

    # 4. Normalize to 0-1 range
    # Heavily positive news can push above 1.0, cap it
    # Heavily negative news can go below 0, floor it
    score = max(0.0, min(1.0, raw_score))

    details['volume_score'] = round(volume_score, 3)
    details['sentiment_component'] = round(sentiment_component, 3)
    details['score'] = round(score, 3)

    return score, details


def score_volume(current_volume: float | None, avg_volume: float | None) -> Tuple[float, Dict]:
    """
    Score based on volume
    Higher than average volume = higher score
    """
    details = {}
    
    if not current_volume or not avg_volume or avg_volume == 0:
        return 0.5, {'note': 'no_volume_data'}
    
    volume_ratio = current_volume / avg_volume
    details['volume_ratio'] = volume_ratio
    
    if volume_ratio > 1.0:
        # Above average: scale from 0.5 to 1.0
        score = min(1.0, 0.5 + (volume_ratio - 1.0) * 0.2)
    else:
        # Below average: scale from 0 to 0.5
        score = 0.5 * volume_ratio
    
    details['score'] = score
    return score, details


def score_earnings(symbol: str, earnings_calendar: Dict[str, Dict], 
                   days_until_limit: int = 7) -> Tuple[float, Dict]:
    """
    Score based on upcoming earnings
    Score increases as earnings date approaches
    """
    details = {}
    
    if symbol not in earnings_calendar:
        return 0, {'note': 'no_earnings_scheduled'}
    
    earnings_info = earnings_calendar[symbol]
    days_until = earnings_info.get('days_until', 999)
    details['days_until'] = days_until
    
    if days_until <= days_until_limit:
        # Score increases as earnings approaches: 7 days = 0, 0 days = 1.0
        score = 1.0 - (days_until / days_until_limit)
    else:
        score = 0
    
    details['score'] = score
    return score, details


def score_longterm_trend(current_price: float, prev_close: float, 
                         snapshot: Dict) -> Tuple[float, Dict]:
    """
    Score long-term trend strength
    Uses longer timeframe price action
    """
    details = {}
    
    # Calculate trend over available period
    if prev_close > 0:
        # Simple trend: current vs previous close
        trend_change = (current_price - prev_close) / prev_close
        details['trend_change_pct'] = trend_change * 100
        
        # Score: positive for uptrends, scaled to 0-1
        if trend_change > 0:
            score = min(1.0, trend_change * 10)  # Scale up small moves
        else:
            score = max(0.0, 0.5 + trend_change * 10)  # Downtrends score below 0.5
    else:
        score = 0.5
        details['note'] = 'insufficient_data'
    
    details['score'] = score
    return score, details


def score_longterm_momentum(current_price: float, open_price: float, 
                            prev_close: float, snapshot: Dict) -> Tuple[float, Dict]:
    """
    Score long-term momentum
    Looks at sustained directional movement
    """
    details = {}
    
    # Calculate momentum indicators
    if prev_close > 0 and open_price > 0:
        # Price momentum
        price_momentum = (current_price - prev_close) / prev_close
        details['price_momentum_pct'] = price_momentum * 100
        
        # Gap momentum
        gap_momentum = (open_price - prev_close) / prev_close
        details['gap_momentum_pct'] = gap_momentum * 100
        
        # Combined momentum score
        if price_momentum > 0 and gap_momentum > 0:
            # Both positive - strong momentum
            score = min(1.0, (price_momentum + gap_momentum) * 5)
        elif price_momentum > 0:
            # Only price positive - moderate momentum
            score = min(0.7, price_momentum * 5)
        else:
            # Negative momentum
            score = max(0.0, 0.5 + price_momentum * 5)
    else:
        score = 0.5
        details['note'] = 'insufficient_data'
    
    details['score'] = score
    return score, details


def score_crypto(symbol: str, current_price: float, open_price: float,
                prev_close: float, high: float, low: float,
                historical_prices: List[float] = None,
                historical_volumes: List[float] = None,
                use_advanced_indicators: bool = True) -> Tuple[float, Dict]:
    """
    Score cryptocurrency-specific factors
    Crypto markets are 24/7 and more volatile

    Args:
        symbol: Crypto symbol (e.g., 'BTC/USD')
        current_price: Current price
        open_price: Opening price
        prev_close: Previous close price
        high: High price
        low: Low price
        historical_prices: Optional list of historical prices for advanced indicators
        historical_volumes: Optional list of historical volumes for advanced indicators
        use_advanced_indicators: Whether to use RSI, MACD, Bollinger Bands (default True)

    Returns:
        Tuple of (score, details dict)
    """
    details = {}

    # Check if this is a crypto symbol
    is_crypto = '/' in symbol or symbol in ['BTC', 'ETH', 'BTCUSD', 'ETHUSD', 'SOL', 'AVAX',
                                              'MATIC', 'LINK', 'UNI', 'AAVE', 'DOT', 'DOGE']
    details['is_crypto'] = is_crypto

    if not is_crypto:
        return 0, {'note': 'not_crypto'}

    # Basic crypto scoring (always calculated)
    # 1. Volatility is expected and not necessarily bad
    price_range = high - low
    price_range_pct = (price_range / open_price * 100) if open_price > 0 else 0
    details['price_range_pct'] = round(price_range_pct, 2)

    # 2. 24/7 movement
    intraday_move = abs(current_price - open_price)
    intraday_move_pct = (intraday_move / open_price * 100) if open_price > 0 else 0
    details['intraday_move_pct'] = round(intraday_move_pct, 2)

    # 3. Trend direction
    trend = (current_price - prev_close) / prev_close if prev_close > 0 else 0
    details['trend_pct'] = round(trend * 100, 2)

    # 4. Position in range
    if price_range > 0:
        position_in_range = (current_price - low) / price_range
    else:
        position_in_range = 0.5
    details['position_in_range'] = round(position_in_range, 2)

    # Basic score calculation
    if trend > 0:
        # Uptrend: higher volatility = higher score
        basic_score = min(1.0, (trend * 5) + (price_range_pct / 10))
    else:
        # Downtrend: lower score but not zero
        basic_score = max(0.2, 0.5 + (trend * 3))

    details['basic_score'] = round(basic_score, 3)

    # Advanced indicators (if historical data provided)
    if use_advanced_indicators and historical_prices and len(historical_prices) >= 20:
        try:
            from .crypto_indicators import analyze_crypto
            from . import settings

            # Get crypto settings
            crypto_settings = settings.get('crypto', {})

            # Analyze with technical indicators
            indicators = analyze_crypto(
                prices=historical_prices,
                volumes=historical_volumes or [],
                rsi_period=crypto_settings.get('rsi_period', 14),
                bb_period=crypto_settings.get('bb_period', 20),
                bb_std=crypto_settings.get('bb_std', 2.0),
                volume_period=crypto_settings.get('volume_ma_period', 20),
                macd_fast=crypto_settings.get('macd_fast', 12),
                macd_slow=crypto_settings.get('macd_slow', 26),
                macd_signal=crypto_settings.get('macd_signal', 9)
            )

            # Add indicator details
            details['rsi'] = round(indicators.rsi, 2) if indicators.rsi else None
            details['rsi_signal'] = indicators.rsi_signal
            details['macd_trend'] = indicators.macd_trend
            details['bb_position'] = round(indicators.bb_position, 2) if indicators.bb_position else None
            details['bb_signal'] = indicators.bb_signal
            details['volume_ratio'] = round(indicators.volume_ratio, 2) if indicators.volume_ratio else None
            details['volume_signal'] = indicators.volume_signal
            details['momentum_score'] = round(indicators.momentum_score, 3) if indicators.momentum_score else None
            details['volatility_score'] = round(indicators.volatility_score, 3) if indicators.volatility_score else None

            # Combine basic score with indicator score
            # Weight: 40% basic, 60% indicators
            advanced_score = (basic_score * 0.4) + (indicators.overall_score * 0.6)
            details['indicator_score'] = round(indicators.overall_score, 3)
            details['confidence'] = round(indicators.confidence, 2)

            score = advanced_score
            details['score'] = round(score, 3)
            details['mode'] = 'advanced'

        except Exception as e:
            # Fallback to basic score if indicators fail
            details['indicator_error'] = str(e)
            score = basic_score
            details['score'] = round(score, 3)
            details['mode'] = 'basic_fallback'
    else:
        # Use basic score if no historical data
        score = basic_score
        details['score'] = round(score, 3)
        details['mode'] = 'basic'

    return score, details


def score_forex(symbol: str, current_price: float, open_price: float,
                prev_close: float, high: float, low: float,
                historical_prices: List[float] = None,
                historical_highs: List[float] = None,
                historical_lows: List[float] = None,
                use_advanced_indicators: bool = True) -> Tuple[float, Dict]:
    """
    Score forex (foreign exchange) trading opportunities
    Forex markets are 24/5 and have unique characteristics

    Args:
        symbol: Forex symbol (e.g., 'EUR/USD', 'GBP/JPY')
        current_price: Current price
        open_price: Opening price
        prev_close: Previous close price
        high: High price
        low: Low price
        historical_prices: Optional list of historical prices for advanced indicators
        historical_highs: Optional list of historical highs
        historical_lows: Optional list of historical lows
        use_advanced_indicators: Whether to use ATR, pivots, MACD (default True)

    Returns:
        Tuple of (score, details dict)
    """
    details = {}

    # Check if this is a forex symbol
    is_forex = '/' in symbol and any(curr in symbol for curr in [
        'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'NZD', 'CAD'
    ])
    details['is_forex'] = is_forex

    if not is_forex:
        return 0, {'note': 'not_forex'}

    # Basic forex scoring (always calculated)
    # 1. Intraday movement (forex moves are typically smaller than stocks)
    intraday_move = abs(current_price - open_price)
    intraday_move_pct = (intraday_move / open_price * 100) if open_price > 0 else 0
    details['intraday_move_pct'] = round(intraday_move_pct, 4)

    # 2. Trend direction
    trend = (current_price - prev_close) / prev_close if prev_close > 0 else 0
    details['trend_pct'] = round(trend * 100, 4)

    # 3. Position in daily range
    price_range = high - low
    if price_range > 0:
        position_in_range = (current_price - low) / price_range
    else:
        position_in_range = 0.5
    details['position_in_range'] = round(position_in_range, 2)

    # 4. Volatility (range as % of price)
    price_range_pct = (price_range / open_price * 100) if open_price > 0 else 0
    details['price_range_pct'] = round(price_range_pct, 4)

    # Basic score calculation
    # Forex trends can be strong but moves are smaller
    if trend > 0:
        # Uptrend: moderate volatility preferred
        basic_score = min(1.0, (trend * 50) + (price_range_pct * 5) + (position_in_range * 0.2))
    else:
        # Downtrend or ranging
        basic_score = max(0.2, 0.5 + (trend * 30))

    details['basic_score'] = round(basic_score, 3)

    # Advanced indicators (if historical data provided)
    if use_advanced_indicators and historical_prices and len(historical_prices) >= 26:
        try:
            from .forex_indicators import analyze_forex
            from . import settings

            # Get forex settings
            forex_settings = settings.get('forex', {})

            # Analyze with technical indicators
            indicators = analyze_forex(
                prices=historical_prices,
                highs=historical_highs or [],
                lows=historical_lows or [],
                rsi_period=forex_settings.get('rsi_period', 14),
                atr_period=forex_settings.get('atr_period', 14),
                macd_fast=forex_settings.get('macd_fast', 12),
                macd_slow=forex_settings.get('macd_slow', 26),
                macd_signal=forex_settings.get('macd_signal', 9),
                ema_short=forex_settings.get('ema_short', 12),
                ema_long=forex_settings.get('ema_long', 26)
            )

            # Add indicator details
            details['rsi'] = round(indicators.rsi, 2) if indicators.rsi else None
            details['rsi_signal'] = indicators.rsi_signal
            details['atr_pct'] = round(indicators.atr_pct, 4) if indicators.atr_pct else None
            details['volatility_signal'] = indicators.volatility_signal
            details['pivot'] = round(indicators.pivot, 4) if indicators.pivot else None
            details['pivot_signal'] = indicators.pivot_signal
            details['macd_trend'] = indicators.macd_trend
            details['trend_signal'] = indicators.trend_signal
            details['momentum_score'] = round(indicators.momentum_score, 3) if indicators.momentum_score else None
            details['price_position'] = round(indicators.price_position, 2) if indicators.price_position else None

            # Combine basic score with indicator score
            # Weight: 30% basic, 70% indicators (indicators more important for forex)
            advanced_score = (basic_score * 0.3) + (indicators.overall_score * 0.7)
            details['indicator_score'] = round(indicators.overall_score, 3)
            details['confidence'] = round(indicators.confidence, 2)

            score = advanced_score
            details['score'] = round(score, 3)
            details['mode'] = 'advanced'

        except Exception as e:
            # Fallback to basic score if indicators fail
            details['indicator_error'] = str(e)
            score = basic_score
            details['score'] = round(score, 3)
            details['mode'] = 'basic_fallback'
    else:
        # Use basic score if no historical data
        score = basic_score
        details['score'] = round(score, 3)
        details['mode'] = 'basic'

    return score, details


def score_etf(symbol: str, current_price: float, open_price: float,
              prev_close: float, high: float, low: float,
              current_volume: float = None, avg_volume: float = None,
              sector_performance: Dict[str, float] = None) -> Tuple[float, Dict]:
    """
    Score ETF (Exchange-Traded Fund) trading opportunities
    ETFs track indices, sectors, or commodities

    Args:
        symbol: ETF symbol (e.g., 'SPY', 'QQQ', 'IWM', 'GLD')
        current_price: Current price
        open_price: Opening price
        prev_close: Previous close price
        high: High price
        low: Low price
        current_volume: Current volume
        avg_volume: Average volume
        sector_performance: Optional dict of sector performances

    Returns:
        Tuple of (score, details dict)
    """
    details = {}

    # Common ETF symbols (can be expanded)
    etf_indicators = ['SPY', 'QQQ', 'IWM', 'DIA', 'EEM', 'EFA', 'GLD', 'SLV',
                      'USO', 'TLT', 'HYG', 'LQD', 'VXX', 'XLE', 'XLF', 'XLK',
                      'XLV', 'XLI', 'XLP', 'XLY', 'XLB', 'XLU', 'XLRE', 'XLC']

    is_etf = symbol in etf_indicators or symbol.startswith('X') or symbol.endswith('ETF')
    details['is_etf'] = is_etf

    if not is_etf:
        # Still score but note it may not be an ETF
        details['note'] = 'symbol_not_in_etf_list'

    # 1. Price momentum
    intraday_change = (current_price - open_price) / open_price if open_price > 0 else 0
    details['intraday_change_pct'] = round(intraday_change * 100, 2)

    # 2. Gap from previous close
    gap = (open_price - prev_close) / prev_close if prev_close > 0 else 0
    details['gap_pct'] = round(gap * 100, 2)

    # 3. Position in daily range
    price_range = high - low
    if price_range > 0:
        position_in_range = (current_price - low) / price_range
    else:
        position_in_range = 0.5
    details['position_in_range'] = round(position_in_range, 2)

    # 4. Trend strength
    trend = (current_price - prev_close) / prev_close if prev_close > 0 else 0
    details['trend_pct'] = round(trend * 100, 2)

    # 5. Volume analysis (ETFs should have strong volume)
    volume_score = 0.5
    if current_volume and avg_volume and avg_volume > 0:
        volume_ratio = current_volume / avg_volume
        details['volume_ratio'] = round(volume_ratio, 2)

        # Higher volume = better liquidity and confidence
        if volume_ratio > 1.5:
            volume_score = 0.7
        elif volume_ratio > 1.0:
            volume_score = 0.6
        elif volume_ratio < 0.5:
            volume_score = 0.3  # Low volume is concerning for ETFs
        else:
            volume_score = 0.4

    details['volume_score'] = round(volume_score, 2)

    # 6. ETF-specific scoring
    # ETFs are generally less volatile than individual stocks
    # Look for steady trends with good volume

    # Base score on momentum and trend
    momentum_component = intraday_change * 3.0
    trend_component = trend * 2.0
    position_component = (position_in_range - 0.5) * 0.2
    gap_component = gap * 1.5

    raw_score = 0.5 + momentum_component + trend_component + position_component + gap_component

    # Adjust by volume (critical for ETFs)
    raw_score = raw_score * (0.5 + volume_score * 0.5)

    # Normalize to 0-1
    score = max(0.0, min(1.0, raw_score))

    details['raw_score'] = round(raw_score, 3)
    details['score'] = round(score, 3)

    # 7. Sector rotation signal (if provided)
    if sector_performance:
        # Check if this ETF's sector is outperforming
        etf_sector_map = {
            'XLE': 'energy', 'XLF': 'financials', 'XLK': 'technology',
            'XLV': 'healthcare', 'XLI': 'industrials', 'XLP': 'consumer_staples',
            'XLY': 'consumer_discretionary', 'XLB': 'materials',
            'XLU': 'utilities', 'XLRE': 'real_estate', 'XLC': 'communication'
        }

        if symbol in etf_sector_map:
            sector = etf_sector_map[symbol]
            sector_perf = sector_performance.get(sector, 0)
            details['sector'] = sector
            details['sector_performance'] = round(sector_perf * 100, 2)

            # Boost score if sector is strong
            if sector_perf > 0.01:  # Sector up >1%
                score = min(1.0, score * 1.2)
                details['sector_boost'] = True
            elif sector_perf < -0.01:  # Sector down >1%
                score = score * 0.8
                details['sector_penalty'] = True

    details['final_score'] = round(score, 3)
    return score, details


def score_stock_candidates(snaps: Dict[str, dict], news_counts: Dict[str, int],
                          earnings: Dict[str, str]) -> Dict[str, Dict[str, float]]:
    """
    Legacy function for backward compatibility
    Scores candidates using simple momentum + news + earnings

    Returns dict with format: {symbol: {'mover': float, 'score': float}}
    """
    out: Dict[str, Dict[str, float]] = {}

    for sym, snap in (snaps or {}).items():
        # Get mover percentage
        mover = _mover_from_snap(snap)

        # News boost: 0.05 per article, capped at 5 articles
        news_boost = 0.05 * min(5, int(news_counts.get(sym, 0)))

        # Earnings boost: 0.2 if earnings upcoming
        earn_boost = 0.2 if sym in earnings else 0.0

        # Combined score: base 0.5, momentum scaled by 4x, plus boosts
        score = max(0.0, min(1.0, 0.5 + 4.0 * mover + news_boost + earn_boost))

        out[sym] = {
            "mover": mover,
            "score": score
        }

    return out


# Export all scoring functions for use by StrategyManager
__all__ = [
    'score_momentum',
    'score_mean_reversion',
    'score_news',
    'score_volume',
    'score_earnings',
    'score_longterm_trend',
    'score_longterm_momentum',
    'score_crypto',
    'score_forex',
    'score_etf',
    'score_stock_candidates'
]
