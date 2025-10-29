"""
Crypto-specific Technical Indicators
Provides advanced indicators for cryptocurrency trading analysis
"""
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass


@dataclass
class CryptoIndicators:
    """Container for cryptocurrency technical indicators"""
    rsi: Optional[float] = None
    rsi_signal: str = "neutral"  # oversold, neutral, overbought

    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    macd_trend: str = "neutral"  # bullish, neutral, bearish

    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_position: Optional[float] = None  # 0-1, where price is in bands
    bb_signal: str = "neutral"  # oversold, neutral, overbought

    volume_ratio: Optional[float] = None
    volume_signal: str = "neutral"  # low, normal, high

    momentum_score: Optional[float] = None
    volatility_score: Optional[float] = None

    overall_score: float = 0.5
    confidence: float = 0.0


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

    # For signal line, we'd need historical MACD values
    # Simplified: use a percentage of MACD as signal approximation
    signal = macd * 0.9  # Simplified signal line
    histogram = macd - signal

    # Determine trend
    if histogram > 0 and macd > 0:
        trend = "bullish"
    elif histogram < 0 and macd < 0:
        trend = "bearish"
    else:
        trend = "neutral"

    return macd, signal, histogram, trend


def calculate_bollinger_bands(prices: List[float], period: int = 20,
                               num_std: float = 2.0) -> Tuple[Optional[float], Optional[float],
                                                               Optional[float], Optional[float], str]:
    """
    Calculate Bollinger Bands

    Args:
        prices: List of closing prices (most recent last)
        period: Period for moving average (default 20)
        num_std: Number of standard deviations (default 2.0)

    Returns:
        Tuple of (upper band, middle band, lower band, position, signal)
    """
    if len(prices) < period:
        return None, None, None, None, "neutral"

    # Calculate middle band (SMA)
    recent_prices = prices[-period:]
    middle = sum(recent_prices) / period

    # Calculate standard deviation
    variance = sum((p - middle) ** 2 for p in recent_prices) / period
    std = variance ** 0.5

    # Calculate bands
    upper = middle + (num_std * std)
    lower = middle - (num_std * std)

    # Current price position in bands
    current_price = prices[-1]
    if upper != lower:
        position = (current_price - lower) / (upper - lower)
    else:
        position = 0.5

    # Determine signal
    if position < 0.2:
        signal = "oversold"
    elif position > 0.8:
        signal = "overbought"
    else:
        signal = "neutral"

    return upper, middle, lower, position, signal


def calculate_volume_analysis(volumes: List[float], period: int = 20) -> Tuple[Optional[float], str]:
    """
    Analyze volume relative to average

    Args:
        volumes: List of volume values (most recent last)
        period: Period for average (default 20)

    Returns:
        Tuple of (volume ratio, signal)
    """
    if len(volumes) < period:
        return None, "neutral"

    # Calculate average volume
    avg_volume = sum(volumes[-period:-1]) / (period - 1)  # Exclude current
    current_volume = volumes[-1]

    if avg_volume == 0:
        return None, "neutral"

    ratio = current_volume / avg_volume

    # Determine signal
    if ratio > 2.0:
        signal = "high"
    elif ratio < 0.5:
        signal = "low"
    else:
        signal = "normal"

    return ratio, signal


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
    # Positive momentum increases score, negative decreases
    score = 0.5 + (short_change * 2) + (long_change * 1)
    score = max(0.0, min(1.0, score))

    return score


def calculate_volatility_score(prices: List[float], period: int = 20) -> Optional[float]:
    """
    Calculate volatility score (normalized standard deviation)

    Args:
        prices: List of closing prices (most recent last)
        period: Period for calculation (default 20)

    Returns:
        Volatility score (0-1, higher = more volatile)
    """
    if len(prices) < period:
        return None

    recent_prices = prices[-period:]
    mean = sum(recent_prices) / period

    # Calculate standard deviation
    variance = sum((p - mean) ** 2 for p in recent_prices) / period
    std = variance ** 0.5

    # Normalize by mean (coefficient of variation)
    if mean > 0:
        cv = std / mean
        # Scale to 0-1 (typical crypto CV is 0-0.1)
        score = min(1.0, cv * 10)
    else:
        score = 0.5

    return score


def analyze_crypto(prices: List[float], volumes: List[float],
                   rsi_period: int = 14, bb_period: int = 20, bb_std: float = 2.0,
                   volume_period: int = 20, macd_fast: int = 12, macd_slow: int = 26,
                   macd_signal: int = 9) -> CryptoIndicators:
    """
    Comprehensive crypto technical analysis

    Args:
        prices: List of closing prices (most recent last)
        volumes: List of volume values (most recent last)
        rsi_period: RSI calculation period
        bb_period: Bollinger Bands period
        bb_std: Bollinger Bands standard deviations
        volume_period: Volume moving average period
        macd_fast: MACD fast period
        macd_slow: MACD slow period
        macd_signal: MACD signal period

    Returns:
        CryptoIndicators object with all calculated values
    """
    indicators = CryptoIndicators()

    # RSI
    indicators.rsi, indicators.rsi_signal = calculate_rsi(prices, rsi_period)

    # MACD
    indicators.macd, indicators.macd_signal, indicators.macd_histogram, indicators.macd_trend = \
        calculate_macd(prices, macd_fast, macd_slow, macd_signal)

    # Bollinger Bands
    indicators.bb_upper, indicators.bb_middle, indicators.bb_lower, indicators.bb_position, indicators.bb_signal = \
        calculate_bollinger_bands(prices, bb_period, bb_std)

    # Volume Analysis
    if volumes:
        indicators.volume_ratio, indicators.volume_signal = calculate_volume_analysis(volumes, volume_period)

    # Momentum Score
    indicators.momentum_score = calculate_momentum_score(prices)

    # Volatility Score
    indicators.volatility_score = calculate_volatility_score(prices)

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

    # Bollinger Bands component (0-1)
    if indicators.bb_signal == "oversold":
        score_components.append(0.7)
    elif indicators.bb_signal == "overbought":
        score_components.append(0.3)
    else:
        score_components.append(0.5)

    # Volume component (0-1)
    if indicators.volume_signal == "high":
        score_components.append(0.6)  # High volume supports moves
    elif indicators.volume_signal == "low":
        score_components.append(0.4)  # Low volume is weak
    else:
        score_components.append(0.5)

    # Momentum component
    if indicators.momentum_score is not None:
        score_components.append(indicators.momentum_score)

    # Calculate overall score
    if score_components:
        indicators.overall_score = sum(score_components) / len(score_components)
        indicators.confidence = len(score_components) / 5.0  # Max 5 components

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
    'CryptoIndicators',
    'calculate_rsi',
    'calculate_macd',
    'calculate_bollinger_bands',
    'calculate_volume_analysis',
    'calculate_momentum_score',
    'calculate_volatility_score',
    'analyze_crypto',
]
