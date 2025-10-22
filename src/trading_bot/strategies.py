"""
Complete Strategy Signals Module
Includes all 8 signal types: momentum, mean_reversion, news, volume, 
earnings, longterm_trend, longterm_momentum, and crypto
"""
from __future__ import annotations
from typing import Dict, Tuple
import math


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


def score_news(symbol: str, news_counts: Dict[str, int], window_hours: int = 6) -> Tuple[float, Dict]:
    """
    Score based on news activity
    More news articles = higher score (logarithmic)
    """
    details = {}
    
    count = news_counts.get(symbol, 0)
    details['news_count'] = count
    details['window_hours'] = window_hours
    
    if count > 0:
        # Logarithmic scaling: 1 article = ~0.3, 5 articles = ~0.6, 20 articles = 1.0
        score = min(1.0, math.log(count + 1) / math.log(20))
    else:
        score = 0
    
    details['score'] = score
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
                prev_close: float, high: float, low: float) -> Tuple[float, Dict]:
    """
    Score cryptocurrency-specific factors
    Crypto markets are 24/7 and more volatile
    """
    details = {}
    
    # Check if this is a crypto symbol
    is_crypto = '/' in symbol or symbol in ['BTC', 'ETH', 'BTCUSD', 'ETHUSD']
    details['is_crypto'] = is_crypto
    
    if not is_crypto:
        return 0, {'note': 'not_crypto'}
    
    # Crypto-specific scoring
    # 1. Volatility is expected and not necessarily bad
    price_range = high - low
    price_range_pct = (price_range / open_price * 100) if open_price > 0 else 0
    details['price_range_pct'] = price_range_pct
    
    # 2. 24/7 movement
    intraday_move = abs(current_price - open_price)
    intraday_move_pct = (intraday_move / open_price * 100) if open_price > 0 else 0
    details['intraday_move_pct'] = intraday_move_pct
    
    # 3. Trend direction
    trend = (current_price - prev_close) / prev_close if prev_close > 0 else 0
    details['trend_pct'] = trend * 100
    
    # Score: favor strong trends and volatility in crypto
    if trend > 0:
        # Uptrend: higher volatility = higher score
        score = min(1.0, (trend * 5) + (price_range_pct / 10))
    else:
        # Downtrend: lower score but not zero
        score = max(0.2, 0.5 + (trend * 3))
    
    details['score'] = score
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
    'score_stock_candidates'
]
