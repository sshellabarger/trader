from __future__ import annotations
from typing import Any, Dict
from .state import get_kv, set_kv

# src/trading_bot/settings.py  (defaults section)
_DEFAULTS = {
    "strategies": {
        "momentum": True,
        "mean_reversion": True,
        "vwap": True,
        "news": True,
        "earnings": True,
        "longterm_trend": True,
        "longterm_momentum": True,
    },
    "thresholds": { "enter": 0.62, "exit": 0.45, "min_spread_bps": 25.0 },
    "weights": { "momentum": 0.40, "mean_reversion": 0.25, "news": 0.15, "earnings": 0.05, "longterm": 0.15 },
    "scheduling": {
        "news_interval_s": 1200,  # 20 min
        "earnings_refresh_min": 60,
        "candidate_refresh_min": 20,
        "candidate_max_symbols": 150,
        "longterm_refresh_min": 240,
        "health_refresh_min": 20,
        "health_stock_symbols": ["AAPL", "MSFT", "NVDA"]
    },
    "news": {
        "provider_order": ["alpaca", "finnhub", "newsapi"],
        "window_hours": 6,
        "rotate_batch": 60,         # how many symbols per pass
        "newsapi_batch_size": 20,   # serial NewsAPI calls
        "newsapi_cooldown_min": 120 # backoff when 429 seen
    },
    "focus": { "positions_always_focus": True },
    "crypto": { "enabled": False, "universe": ["BTCUSD", "ETHUSD"] },
    "data": { "strict_batch_only": True }
}

def _deep_merge(base: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (delta or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base

def get_settings() -> Dict[str, Any]:
    stored = get_kv("settings", None)
    if stored is None:
        set_kv("settings", _DEFAULTS)
        return {**_DEFAULTS}
    merged = _deep_merge({**_DEFAULTS}, stored)
    set_kv("settings", merged)
    return merged

def update_settings(new_values: Dict[str, Any]) -> Dict[str, Any]:
    current = get_settings()
    updated = _deep_merge(current, new_values or {})
    set_kv("settings", updated)
    return updated
