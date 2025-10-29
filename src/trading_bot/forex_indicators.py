"""
Forex-specific Technical Indicators
Provides advanced indicators for foreign exchange trading analysis
"""
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class ForexIndicators:
    """Container for forex technical indicators"""
    # Technical indicators
    rsi: Optional[float] = None
    rsi_signal: str = "neutral"  # oversold, neutral, overbought

    atr: Optional[float] = None  # Average True Range
    atr_pct: Optional[float] = None  # ATR as percentage of price
    volatility_signal: str = "normal"  # low, normal, high

    # Pivot points for support/resistance
    pivot: Optional[float] = None
    resistance_1: Optional[float] = None
    resistance_2: Optional[float] = None
    support_1: Optional[float] = None
    support_2: Optional[float] = None
    pivot_signal: str = "neutral"  # support, neutral, resistance

    # MACD
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    macd_trend: str = "neutral"  # bullish, neutral, bearish

    # Trend indicators
    ema_short: Optional[float] = None
    ema_long: Optional[float] = None
    trend_signal: str = "neutral"  # bullish, neutral, bearish

    # Momentum
    momentum_score: Optional[float] = None

    # Price position
    price_position: Optional[float] = None  # Where price is in recent range (0-1)

    # Overall assessment
    overall_score: float = 0.5
    confidence: float = 0.0


def calculate_atr(highs: List[float], lows: List[float], closes: List[float],
                  period: int = 14) -> Tuple[Optional[float], Optional[float], str]:
    """
    Calculate Average True Range (ATR) for volatility measurement

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of closing prices
        period: ATR period (default 14)

    Returns:
        Tuple of (ATR value, ATR percentage, volatility signal)
    """
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None, None, "normal"

    # Calculate True Range for each period
    true_ranges = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        true_range = max(high_low, high_close, low_close)
        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None, None, "normal"

    # Calculate ATR (simple moving average of true ranges)
    atr = sum(true_ranges[-period:]) / period

    # Calculate ATR as percentage of current price
    current_price = closes[-1]
    atr_pct = (atr / current_price * 100) if current_price > 0 else 0

    # Determine volatility signal
    # Forex typical ATR%: 0.5-1.5% normal, >2% high, <0.3% low
    if atr_pct > 2.0:
        signal = "high"
    elif atr_pct < 0.3:
        signal = "low"
    else:
        signal = "normal"

    return atr, atr_pct, signal


def calculate_pivot_points(high: float, low: float, close: float) -> Dict[str, float]:
    """
    Calculate pivot points for support and resistance levels

    Args:
        high: Previous day's high
        low: Previous day's low
        close: Previous day's close

    Returns:
        Dictionary with pivot, resistance, and support levels
    """
    # Standard pivot point calculation
    pivot = (high + low + close) / 3

    # Resistance levels
    r1 = (2 * pivot) - low
    r2 = pivot + (high - low)

    # Support levels
    s1 = (2 * pivot) - high
    s2 = pivot - (high - low)

    return {
        'pivot': pivot,
        'r1': r1,
        'r2': r2,
        's1': s1,
        's2': s2
    }


def get_pivot_signal(current_price: float, pivot_levels: Dict[str, float]) -> str:
    """
    Determine if price is at support, resistance, or neutral

    Args:
        current_price: Current price
        pivot_levels: Dictionary of pivot levels

    Returns:
        Signal string: "support", "resistance", or "neutral"
    """
    pivot = pivot_levels['pivot']
    r1 = pivot_levels['r1']
    s1 = pivot_levels['s1']

    # Check if near support (within 0.1% of s1)
    if abs(current_price - s1) / s1 < 0.001:
        return "support"

    # Check if near resistance (within 0.1% of r1)
    if abs(current_price - r1) / r1 < 0.001:
        return "resistance"

    # Check if below pivot (bearish) or above (bullish)
    if current_price < pivot:
        return "below_pivot"
    elif current_price > pivot:
        return "above_pivot"

    return "neutral"


def calculate_rsi(prices: List[float], period: int = 14) -> Tuple[Optional[float], str]:
    """
    Calculate Relative Strength Index (RSI)

    Args:
        prices: List of closing prices (most recent last)
        period: RSI period (default 14)

    Returns:
        Tuple of (RSI value, signal string)
    """
    if len(prices) < period + 1:
        return None, "neutral"

    # Calculate price changes
    changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    # Separate gains and losses
    gains = [max(0, change) for change in changes]
    losses = [max(0, -change) for change in changes]

    # Calculate average gain and loss
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0, "overbought"

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # Determine signal
    if rsi < 30:
        signal = "oversold"
    elif rsi > 70:
        signal = "overbought"
    else:
        signal = "neutral"

    return rsi, signal


def calculate_macd(prices: List[float], fast: int = 12, slow: int = 26,
                   signal_period: int = 9) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    """
    Calculate MACD (Moving Average Convergence Divergence)

    Args:
        prices: List of closing prices (most recent last)
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal_period: Signal line period (default 9)

    Returns:
        Tuple of (MACD line, signal line, histogram, trend string)
    """
    if len(prices) < slow:
        return None, None, None, "neutral"

    # Calculate EMAs
    fast_ema = _calculate_ema(prices, fast)
    slow_ema = _calculate_ema(prices, slow)

    if fast_ema is None or slow_ema is None:
        return None, None, None, "neutral"

    # MACD line
    macd = fast_ema - slow_ema

    # Simplified signal line
    signal = macd * 0.9
    histogram = macd - signal

    # Determine trend
    if histogram > 0 and macd > 0:
        trend = "bullish"
    elif histogram < 0 and macd < 0:
        trend = "bearish"
    else:
        trend = "neutral"

    return macd, signal, histogram, trend


def calculate_ema_cross(prices: List[float], short_period: int = 12,
                        long_period: int = 26) -> Tuple[Optional[float], Optional[float], str]:
    """
    Calculate EMA crossover signal

    Args:
        prices: List of closing prices
        short_period: Short EMA period
        long_period: Long EMA period

    Returns:
        Tuple of (short EMA, long EMA, signal)
    """
    if len(prices) < long_period:
        return None, None, "neutral"

    short_ema = _calculate_ema(prices, short_period)
    long_ema = _calculate_ema(prices, long_period)

    if short_ema is None or long_ema is None:
        return None, None, "neutral"

    # Determine signal
    if short_ema > long_ema:
        signal = "bullish"
    elif short_ema < long_ema:
        signal = "bearish"
    else:
        signal = "neutral"

    return short_ema, long_ema, signal


def calculate_momentum_score(prices: List[float], short_period: int = 5,
                             long_period: int = 20) -> Optional[float]:
    """
    Calculate momentum score based on short vs long term price movement

    Args:
        prices: List of closing prices (most recent last)
        short_period: Short period for momentum (default 5)
        long_period: Long period for momentum (default 20)

    Returns:
        Momentum score (0-1)
    """
    if len(prices) < long_period:
        return None

    # Short-term momentum
    short_change = (prices[-1] - prices[-short_period]) / prices[-short_period] if prices[-short_period] > 0 else 0

    # Long-term momentum
    long_change = (prices[-1] - prices[-long_period]) / prices[-long_period] if prices[-long_period] > 0 else 0

    # Combined momentum score (0-1 range)
    # Forex moves are smaller than stocks, so scale differently
    score = 0.5 + (short_change * 10) + (long_change * 5)
    score = max(0.0, min(1.0, score))

    return score


def calculate_price_position(prices: List[float], period: int = 20) -> Optional[float]:
    """
    Calculate where current price is in recent range (0-1)

    Args:
        prices: List of closing prices
        period: Lookback period

    Returns:
        Price position (0 = at low, 1 = at high)
    """
    if len(prices) < period:
        return None

    recent_prices = prices[-period:]
    high = max(recent_prices)
    low = min(recent_prices)
    current = prices[-1]

    if high == low:
        return 0.5

    position = (current - low) / (high - low)
    return position


def analyze_forex(prices: List[float], highs: List[float] = None, lows: List[float] = None,
                  rsi_period: int = 14, atr_period: int = 14, macd_fast: int = 12,
                  macd_slow: int = 26, macd_signal: int = 9, ema_short: int = 12,
                  ema_long: int = 26) -> ForexIndicators:
    """
    Comprehensive forex technical analysis

    Args:
        prices: List of closing prices (most recent last)
        highs: List of high prices
        lows: List of low prices
        rsi_period: RSI calculation period
        atr_period: ATR calculation period
        macd_fast: MACD fast period
        macd_slow: MACD slow period
        macd_signal: MACD signal period
        ema_short: Short EMA period
        ema_long: Long EMA period

    Returns:
        ForexIndicators object with all calculated values
    """
    indicators = ForexIndicators()

    # RSI
    indicators.rsi, indicators.rsi_signal = calculate_rsi(prices, rsi_period)

    # ATR (if highs/lows provided)
    if highs and lows and len(highs) == len(prices) and len(lows) == len(prices):
        indicators.atr, indicators.atr_pct, indicators.volatility_signal = \
            calculate_atr(highs, lows, prices, atr_period)

        # Pivot points (using most recent data)
        if len(highs) >= 2 and len(lows) >= 2 and len(prices) >= 2:
            pivot_levels = calculate_pivot_points(highs[-2], lows[-2], prices[-2])
            indicators.pivot = pivot_levels['pivot']
            indicators.resistance_1 = pivot_levels['r1']
            indicators.resistance_2 = pivot_levels['r2']
            indicators.support_1 = pivot_levels['s1']
            indicators.support_2 = pivot_levels['s2']
            indicators.pivot_signal = get_pivot_signal(prices[-1], pivot_levels)

    # MACD
    indicators.macd, indicators.macd_signal, indicators.macd_histogram, indicators.macd_trend = \
        calculate_macd(prices, macd_fast, macd_slow, macd_signal)

    # EMA Crossover
    indicators.ema_short, indicators.ema_long, indicators.trend_signal = \
        calculate_ema_cross(prices, ema_short, ema_long)

    # Momentum Score
    indicators.momentum_score = calculate_momentum_score(prices)

    # Price Position
    indicators.price_position = calculate_price_position(prices)

    # Calculate overall score (0-1)
    score_components = []

    # RSI component (0-1)
    if indicators.rsi is not None:
        if indicators.rsi_signal == "oversold":
            score_components.append(0.7)  # Good buy signal
        elif indicators.rsi_signal == "overbought":
            score_components.append(0.3)  # Weak signal
        else:
            score_components.append(0.5)

    # MACD component (0-1)
    if indicators.macd_trend == "bullish":
        score_components.append(0.7)
    elif indicators.macd_trend == "bearish":
        score_components.append(0.3)
    else:
        score_components.append(0.5)

    # Trend component (0-1)
    if indicators.trend_signal == "bullish":
        score_components.append(0.7)
    elif indicators.trend_signal == "bearish":
        score_components.append(0.3)
    else:
        score_components.append(0.5)

    # Pivot component (0-1)
    if indicators.pivot_signal == "support":
        score_components.append(0.7)  # At support = good buy
    elif indicators.pivot_signal == "resistance":
        score_components.append(0.3)  # At resistance = weak
    elif indicators.pivot_signal == "above_pivot":
        score_components.append(0.6)  # Above pivot = bullish
    elif indicators.pivot_signal == "below_pivot":
        score_components.append(0.4)  # Below pivot = bearish
    else:
        score_components.append(0.5)

    # Momentum component
    if indicators.momentum_score is not None:
        score_components.append(indicators.momentum_score)

    # Volatility component (high volatility = more opportunity but risk)
    if indicators.volatility_signal == "high":
        score_components.append(0.6)  # Opportunities in volatility
    elif indicators.volatility_signal == "low":
        score_components.append(0.4)  # Less opportunity
    else:
        score_components.append(0.5)

    # Calculate overall score
    if score_components:
        indicators.overall_score = sum(score_components) / len(score_components)
        indicators.confidence = len(score_components) / 6.0  # Max 6 components

    return indicators


def _calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """
    Calculate Exponential Moving Average

    Args:
        prices: List of closing prices (most recent last)
        period: EMA period

    Returns:
        EMA value or None
    """
    if len(prices) < period:
        return None

    # Start with SMA
    sma = sum(prices[:period]) / period

    # Calculate multiplier
    multiplier = 2 / (period + 1)

    # Calculate EMA
    ema = sma
    for price in prices[period:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))

    return ema


# Export all functions
__all__ = [
    'ForexIndicators',
    'calculate_atr',
    'calculate_pivot_points',
    'get_pivot_signal',
    'calculate_rsi',
    'calculate_macd',
    'calculate_ema_cross',
    'calculate_momentum_score',
    'calculate_price_position',
    'analyze_forex',
]
