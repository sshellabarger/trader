"""
Settings module with KV-backed configuration
✅ UPDATED: Optimized intervals and simulation mode support
"""
import json
import logging
from typing import Dict, Any

from .state import get_kv, set_kv

# ✅ OPTIMIZED: Adjusted for better rate limit management
scheduling = {
    "candidate_refresh_min": 30,  # Increased from 20 to avoid rate limits
    "candidate_max_symbols": 100,  # Reduced from 150 to avoid rate limits
    "news_interval_s": 1800,  # 30 minutes instead of 20
    "earnings_refresh_min": 120,  # Only refresh twice per day
    "health_refresh_min": 15  # Reduced from 20 for better monitoring
}

strategies = {
    "momentum": True,
    "mean_reversion": True,
    "news": True,
    "volume": True,
    "earnings": True,
    "longterm_trend": True,
    "longterm_momentum": True,
    "crypto": False  # Set to True to enable 24/7 crypto trading
}

thresholds = {
    "min_spread_bps": 25.0,
    "trade_stop_loss_bps": 50.0,
    "daily_stop_loss_pct": 2.0
}

news = {
    "provider_order": ["alpaca", "finnhub", "newsapi"],
    "window_hours": 6,
    "rotate_batch": 60,
    "newsapi_cooldown_min": 120
}

crypto = {
    "enabled": False,
    "universe": ["BTC/USD", "ETH/USD"]
}

data = {
    "strict_batch_only": True,
    "min_volume": 100000,
    "min_price": 5.0,
    "max_price": 500.0,
    "exclude_patterns": ["^", ".", "-"]
}

# Enhanced settings for risk management
risk = {
    "max_position_size_pct": 5.0,
    "max_total_exposure_pct": 80.0,
    "max_positions": 10,
    "risk_per_trade_pct": 1.0,
    "trailing_stop_enabled": True,
    "trailing_stop_pct": 1.5,
    "max_hold_time_minutes": 240,
    "close_all_eod": True,
    "max_daily_trades": 50,
    "min_trade_value": 100,
    "position_monitor_interval_sec": 30,
    "position_monitor_enabled": True
}

# ✅ NEW: Added simulation mode flag
backtest = {
    "initial_capital": 100000.0,
    "commission_per_trade": 1.0,
    "slippage_bps": 2.0,
    "simulation_mode": False  # Set to True to log orders without executing
}


def get_settings() -> Dict[str, Any]:
    """
    Get current settings merged with KV overrides
    Returns all settings as a dictionary
    """
    settings = {
        'scheduling': scheduling.copy(),
        'strategies': strategies.copy(),
        'thresholds': thresholds.copy(),
        'news': news.copy(),
        'crypto': crypto.copy(),
        'data': data.copy(),
        'risk': risk.copy(),
        'backtest': backtest.copy()
    }

    # Merge with any KV overrides
    for category in settings.keys():
        stored = get_kv(f'settings.{category}')
        if stored:
            try:
                override = json.loads(stored)
                if isinstance(override, dict):
                    settings[category].update(override)
            except Exception as e:
                logging.warning(f"Failed to parse stored settings for {category}: {e}")

    return settings


def update_settings(updates: Dict[str, Any]) -> Dict[str, str]:
    """
    Update settings and persist to KV store

    Args:
        updates: Dictionary of setting updates

    Returns:
        Dict with 'status' and 'message'
    """
    try:
        if not isinstance(updates, dict):
            return {'status': 'error', 'message': 'Updates must be a dictionary'}

        for key, value in updates.items():
            if isinstance(value, dict):
                set_kv(f'settings.{key}', json.dumps(value))
            else:
                logging.warning(f"Skipping non-dict update for {key}")

        return {
            'status': 'success',
            'message': f'Updated {len(updates)} setting categories'
        }

    except Exception as e:
        logging.error(f"Error updating settings: {e}")
        return {'status': 'error', 'message': str(e)}


def get(category: str, key: str = None, default=None):
    """
    Get a specific setting value

    Args:
        category: Setting category (e.g., 'thresholds')
        key: Optional specific key within category
        default: Default value if not found

    Returns:
        Setting value or default
    """
    all_settings = get_settings()

    if category not in all_settings:
        return default

    if key is None:
        return all_settings[category]

    return all_settings[category].get(key, default)


def _refresh_from_kv():
    """Refresh module-level dicts from KV store"""
    all_settings = get_settings()

    scheduling.update(all_settings['scheduling'])
    strategies.update(all_settings['strategies'])
    thresholds.update(all_settings['thresholds'])
    news.update(all_settings['news'])
    crypto.update(all_settings['crypto'])
    data.update(all_settings['data'])
    risk.update(all_settings['risk'])
    backtest.update(all_settings['backtest'])


try:
    _refresh_from_kv()
except Exception as e:
    logging.warning(f"Failed to load settings from KV: {e}")


class Settings:
    """Object-oriented settings interface"""

    def __init__(self):
        self._cache = None

    def get(self, category: str, default=None):
        """
        Get setting category

        Args:
            category: Setting category name
            default: Default value if not found

        Returns:
            Dictionary of settings for that category
        """
        all_settings = get_settings()

        if category not in all_settings:
            return default if default is not None else {}

        return all_settings[category]

    def as_dict(self) -> Dict[str, Any]:
        """Get all settings as dictionary"""
        return get_settings()

    def refresh(self):
        """Refresh settings from KV store"""
        _refresh_from_kv()
        self._cache = None