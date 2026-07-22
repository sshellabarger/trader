"""
Technical Indicators — pure-Python implementations operating on lists of bar dicts.

Every function accepts a list of bars where each bar is a dict with keys:
  t (timestamp str), o (open), h (high), l (low), c (close), v (volume)

Returns are always plain lists/floats so they're easy to use anywhere.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _closes(bars: List[Dict]) -> List[float]:
    return [float(b["c"]) for b in bars]


def _highs(bars: List[Dict]) -> List[float]:
    return [float(b["h"]) for b in bars]


def _lows(bars: List[Dict]) -> List[float]:
    return [float(b["l"]) for b in bars]


def _volumes(bars: List[Dict]) -> List[float]:
    return [float(b["v"]) for b in bars]


# ---------------------------------------------------------------------------
# Moving Averages
# ---------------------------------------------------------------------------

def sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average. Returns list same length as input; early entries are None."""
    result: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period
    return result


def ema(values: List[float], period: int) -> List[Optional[float]]:
    """Exponential Moving Average."""
    result: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return result
    k = 2.0 / (period + 1)
    # Seed with SMA of first `period` values
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        val = values[i] * k + prev * (1 - k)
        result[i] = val
        prev = val
    return result


# ---------------------------------------------------------------------------
# VWAP  (Volume Weighted Average Price)
# ---------------------------------------------------------------------------

def vwap(bars: List[Dict]) -> List[Optional[float]]:
    """
    Cumulative intraday VWAP.  Resets each day (assumes bars are intraday for one session).
    Returns list same length as bars.
    """
    result: List[Optional[float]] = []
    cum_vol = 0.0
    cum_tp_vol = 0.0
    for b in bars:
        typical_price = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
        vol = float(b["v"])
        cum_vol += vol
        cum_tp_vol += typical_price * vol
        if cum_vol > 0:
            result.append(cum_tp_vol / cum_vol)
        else:
            result.append(None)
    return result


def vwap_with_bands(bars: List[Dict], num_std: float = 2.0) -> Tuple[
    List[Optional[float]], List[Optional[float]], List[Optional[float]]
]:
    """
    VWAP with upper and lower standard deviation bands.
    Returns (vwap_line, upper_band, lower_band).
    """
    vwap_line: List[Optional[float]] = []
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []

    cum_vol = 0.0
    cum_tp_vol = 0.0
    cum_tp2_vol = 0.0  # for variance

    for b in bars:
        tp = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3.0
        vol = float(b["v"])
        cum_vol += vol
        cum_tp_vol += tp * vol
        cum_tp2_vol += (tp ** 2) * vol

        if cum_vol > 0:
            vw = cum_tp_vol / cum_vol
            variance = max(0.0, (cum_tp2_vol / cum_vol) - vw ** 2)
            std = math.sqrt(variance)
            vwap_line.append(vw)
            upper.append(vw + num_std * std)
            lower.append(vw - num_std * std)
        else:
            vwap_line.append(None)
            upper.append(None)
            lower.append(None)

    return vwap_line, upper, lower


# ---------------------------------------------------------------------------
# RSI  (Relative Strength Index)
# ---------------------------------------------------------------------------

def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    """Wilder's RSI."""
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period + 1:
        return result

    # Calculate initial gains/losses
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    # Smooth with Wilder's method
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(0.0, change)
        loss = max(0.0, -change)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            result[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))

    return result


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    values: List[float], period: int = 20, num_std: float = 2.0
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """
    Returns (middle, upper, lower) bands.
    Middle = SMA, Upper/Lower = SMA ± num_std × std_dev.
    """
    middle = sma(values, period)
    upper_band: List[Optional[float]] = [None] * len(values)
    lower_band: List[Optional[float]] = [None] * len(values)

    for i in range(period - 1, len(values)):
        if middle[i] is None:
            continue
        window = values[i - period + 1: i + 1]
        mean = middle[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper_band[i] = mean + num_std * std
        lower_band[i] = mean - num_std * std

    return middle, upper_band, lower_band


# ---------------------------------------------------------------------------
# ATR  (Average True Range)
# ---------------------------------------------------------------------------

def true_range(bars: List[Dict]) -> List[float]:
    """True Range for each bar. First bar uses high-low."""
    tr: List[float] = []
    for i, b in enumerate(bars):
        h = float(b["h"])
        l = float(b["l"])
        if i == 0:
            tr.append(h - l)
        else:
            prev_c = float(bars[i - 1]["c"])
            tr.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return tr


def atr(bars: List[Dict], period: int = 14) -> List[Optional[float]]:
    """Average True Range using Wilder's smoothing."""
    tr_values = true_range(bars)
    result: List[Optional[float]] = [None] * len(tr_values)
    if len(tr_values) < period:
        return result

    # Seed with SMA
    avg = sum(tr_values[:period]) / period
    result[period - 1] = avg
    for i in range(period, len(tr_values)):
        avg = (avg * (period - 1) + tr_values[i]) / period
        result[i] = avg
    return result


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    values: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """
    MACD line, Signal line, Histogram.
    """
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)

    macd_line: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # Signal line = EMA of MACD line (skip Nones)
    macd_vals = [v for v in macd_line if v is not None]
    if len(macd_vals) < signal_period:
        return macd_line, [None] * len(values), [None] * len(values)

    signal_raw = ema(macd_vals, signal_period)

    # Map signal back to full-length list
    signal_line: List[Optional[float]] = [None] * len(values)
    histogram: List[Optional[float]] = [None] * len(values)
    j = 0
    for i in range(len(values)):
        if macd_line[i] is not None:
            if j < len(signal_raw):
                signal_line[i] = signal_raw[j]
                if signal_raw[j] is not None:
                    histogram[i] = macd_line[i] - signal_raw[j]
            j += 1

    return macd_line, signal_line, histogram


# ---------------------------------------------------------------------------
# Volume analysis
# ---------------------------------------------------------------------------

def relative_volume(bars: List[Dict], lookback: int = 20) -> Optional[float]:
    """
    Current bar volume relative to average of last `lookback` bars.
    Returns ratio (e.g., 2.5 means 2.5× average).
    """
    if len(bars) < lookback + 1:
        return None
    avg_vol = sum(float(b["v"]) for b in bars[-(lookback + 1):-1]) / lookback
    current_vol = float(bars[-1]["v"])
    return current_vol / avg_vol if avg_vol > 0 else None


def cumulative_volume(bars: List[Dict]) -> List[float]:
    """Running cumulative volume across bars."""
    result = []
    total = 0.0
    for b in bars:
        total += float(b["v"])
        result.append(total)
    return result


# ---------------------------------------------------------------------------
# Opening Range
# ---------------------------------------------------------------------------

def opening_range(
    bars: List[Dict], range_minutes: int = 15
) -> Optional[Tuple[float, float]]:
    """
    Calculate the opening range (high, low) from the first N minutes of bars.
    Bars must be 1-minute bars starting from market open.
    Returns (range_high, range_low) or None if not enough data.
    """
    if len(bars) < range_minutes:
        return None

    range_bars = bars[:range_minutes]
    range_high = max(float(b["h"]) for b in range_bars)
    range_low = min(float(b["l"]) for b in range_bars)
    return range_high, range_low


# ---------------------------------------------------------------------------
# Convenience: compute a full indicator snapshot for a symbol
# ---------------------------------------------------------------------------

def compute_indicators(bars: List[Dict]) -> Dict:
    """
    Compute all indicators on a list of intraday bars.
    Returns a dict of indicator names → latest values.
    Useful for quick signal checks.
    """
    if not bars:
        return {}

    closes = _closes(bars)
    result: Dict = {}

    # VWAP
    vwap_vals, vwap_upper, vwap_lower = vwap_with_bands(bars)
    result["vwap"] = vwap_vals[-1] if vwap_vals else None
    result["vwap_upper"] = vwap_upper[-1] if vwap_upper else None
    result["vwap_lower"] = vwap_lower[-1] if vwap_lower else None

    # Price vs VWAP
    if result["vwap"] and result["vwap"] > 0:
        result["vwap_deviation_pct"] = ((closes[-1] - result["vwap"]) / result["vwap"]) * 100
    else:
        result["vwap_deviation_pct"] = 0.0

    # RSI
    rsi_vals = rsi(closes, 14)
    result["rsi_14"] = rsi_vals[-1]

    # EMAs
    ema9 = ema(closes, 9)
    ema20 = ema(closes, 20)
    result["ema_9"] = ema9[-1]
    result["ema_20"] = ema20[-1]

    # Bollinger Bands
    bb_mid, bb_up, bb_low = bollinger_bands(closes, 20, 2.0)
    result["bb_middle"] = bb_mid[-1]
    result["bb_upper"] = bb_up[-1]
    result["bb_lower"] = bb_low[-1]
    if bb_up[-1] and bb_low[-1] and (bb_up[-1] - bb_low[-1]) > 0:
        result["bb_pct_b"] = (closes[-1] - bb_low[-1]) / (bb_up[-1] - bb_low[-1])
    else:
        result["bb_pct_b"] = None

    # ATR
    atr_vals = atr(bars, 14)
    result["atr_14"] = atr_vals[-1]

    # MACD
    macd_line, signal_line, hist = macd(closes)
    result["macd"] = macd_line[-1]
    result["macd_signal"] = signal_line[-1]
    result["macd_histogram"] = hist[-1]

    # Relative volume
    result["relative_volume"] = relative_volume(bars, 20)

    # Current price info
    result["last_close"] = closes[-1]
    result["last_high"] = float(bars[-1]["h"])
    result["last_low"] = float(bars[-1]["l"])
    result["last_volume"] = float(bars[-1]["v"])

    return result


# ---------------------------------------------------------------------------
# Daily ATR% — shared by the live engine and the sleeve replay backtester so
# the ATR-scaled ORB range band sees the SAME number in both places.
# ---------------------------------------------------------------------------

def daily_atr_pct(daily_bars: List[Dict], day_str: str,
                  period: int = 14) -> Optional[float]:
    """ATR of DAILY bars as a % of price, using bars STRICTLY before day_str
    (no lookahead: the value is known before the open it gates).

    Simple mean of true ranges rather than Wilder smoothing — with a fixed
    lookback the difference is noise and the arithmetic stays auditable.
    Returns None with fewer than 5 usable true ranges; the ORB ATR band
    treats None as "fall back to the fixed % band" (fail-safe).
    """
    prior = [b for b in daily_bars if str(b.get("t", ""))[:10] < day_str]
    if len(prior) < 2:
        return None
    window = prior[-(period + 1):]
    trs: List[float] = []
    for i in range(1, len(window)):
        try:
            h = float(window[i]["h"])
            l = float(window[i]["l"])
            pc = float(window[i - 1]["c"])
        except (KeyError, TypeError, ValueError):
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < 5:
        return None
    last_close = float(window[-1].get("c", 0) or 0)
    if last_close <= 0:
        return None
    return (sum(trs) / len(trs)) / last_close * 100.0
