"""
Enhanced Settings Schema
Includes all new risk management and strategy parameters
"""
from typing import Dict, Any
import json
from .state import get_kv, set_kv


DEFAULT_SETTINGS = {
    # Scheduling
    "scheduling": {
        "candidate_refresh_min": 20,
        "candidate_max_symbols": 150,
        "news_interval_s": 1200,
        "earnings_refresh_min": 60,
        "health_refresh_min": 10,  # More frequent health checks
        "loop_interval_s": 30
    },
    
    # Strategy toggles
    "strategies": {
        "momentum": True,
        "mean_reversion": True,
        "news": True,
        "earnings": False,
        "volume": True,
        "longterm_trend": False,
        "longterm_momentum": False,
        "crypto": False
    },
    
    # Risk management (NEW)
    "risk": {
        "max_position_size_pct": 5.0,  # Max 5% per position
        "max_total_exposure_pct": 80.0,  # Max 80% total exposure
        "max_positions": 10,  # Maximum concurrent positions
        "risk_per_trade_pct": 1.0,  # Risk 1% per trade
        
        # Stop loss settings
        "trailing_stop_enabled": True,
        "trailing_stop_pct": 1.5,  # 1.5% trailing stop
        
        # Time-based exits
        "max_hold_time_minutes": 240,  # 4 hours max hold
        "close_all_eod": True,  # Close all positions before market close
        "eod_close_minutes": 15,  # Close 15 min before close
        
        # Trading limits
        "max_daily_trades": 50,  # Maximum trades per day
        "min_trade_value": 100,  # Minimum $100 per trade
    },
    
    # Entry/Exit thresholds
    "thresholds": {
        "enter": 0.62,  # Entry score threshold
        "exit": 0.45,  # Exit score threshold (if using)
        "take_profit_pct": 2.0,  # Take profit at 2%
        "min_spread_bps": 25.0,  # Minimum spread
        "trade_stop_loss_bps": 50.0,  # 0.5% stop loss per trade
        "daily_stop_loss_pct": 2.0,  # 2% daily stop loss
        "min_confidence": 0.3,  # Minimum strategy confidence
        "min_active_strategies": 2  # Require at least 2 strategies agreeing
    },
    
    # Strategy weights (base weights, adjusted by regime)
    "strategy_weights": {
        "momentum": 0.4,
        "mean_reversion": 0.3,
        "news": 0.15,
        "volume": 0.1,
        "earnings": 0.05
    },
    
    # Regime-specific adjustments (NEW)
    "regime_weights": {
        "trending_up": {
            "momentum": 0.6,
            "mean_reversion": 0.1,
            "volume": 0.2,
            "news": 0.08,
            "earnings": 0.02
        },
        "trending_down": {
            "momentum": 0.2,
            "mean_reversion": 0.4,
            "volume": 0.2,
            "news": 0.15,
            "earnings": 0.05
        },
        "ranging": {
            "momentum": 0.1,
            "mean_reversion": 0.5,
            "volume": 0.15,
            "news": 0.2,
            "earnings": 0.05
        },
        "high_volatility": {
            "momentum": 0.3,
            "mean_reversion": 0.2,
            "volume": 0.3,
            "news": 0.15,
            "earnings": 0.05
        }
    },
    
    # News settings
    "news": {
        "provider_order": ["alpaca", "finnhub", "newsapi"],
        "window_hours": 6,
        "rotate_batch": 60,
        "newsapi_cooldown_min": 120,
        "max_articles_per_symbol": 50
    },
    
    # Earnings settings
    "earnings": {
        "days_ahead": 7,
        "boost_factor": 1.2  # Slight boost for upcoming earnings
    },
    
    # Crypto settings
    "crypto": {
        "enabled": False,
        "universe": ["BTC/USD", "ETH/USD"],
        "notional_usd": 25
    },
    
    # Data settings
    "data": {
        "strict_batch_only": True,  # Only use batch snapshots
        "min_volume": 100000,  # Minimum daily volume
        "min_price": 5.0,  # Minimum stock price
        "max_price": 500.0,  # Maximum stock price (avoid super expensive)
        "exclude_patterns": ["^", ".", "-"]  # Exclude special symbols
    },
    
    # Backtesting settings (NEW)
    "backtest": {
        "initial_capital": 100000.0,
        "commission_per_trade": 1.0,
        "slippage_bps": 2.0,  # 2 basis points slippage
        "enable_shorting": False,
        "margin_multiplier": 1.0  # No margin for now
    },
    
    # Alert settings (NEW)
    "alerts": {
        "enabled": True,
        "daily_loss_threshold_pct": 1.5,  # Alert at 1.5% daily loss
        "position_loss_threshold_pct": 3.0,  # Alert at 3% position loss
        "exposure_threshold_pct": 85.0,  # Alert at 85% exposure
        "max_positions_warning": 8  # Warn at 8 positions
    },
    
    # Logging settings (NEW)
    "logging": {
        "log_all_decisions": True,  # Log every trading decision
        "log_order_details": True,  # Log full order details
        "log_health_checks": True,  # Log health check results
        "log_risk_metrics": True,  # Log risk metrics
        "console_level": "INFO",
        "file_level": "DEBUG"
    }
}


def merge_settings(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge settings dictionaries
    Overrides take precedence over base settings
    """
    result = base.copy()
    
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_settings(result[key], value)
        else:
            result[key] = value
    
    return result


def validate_settings(settings: Dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate settings for logical consistency
    Returns (is_valid, list_of_errors)
    """
    errors = []
    
    # Risk validation
    risk = settings.get('risk', {})
    if risk.get('max_position_size_pct', 0) > risk.get('max_total_exposure_pct', 100):
        errors.append("max_position_size_pct cannot exceed max_total_exposure_pct")
    
    if risk.get('max_positions', 0) < 1:
        errors.append("max_positions must be at least 1")
    
    if risk.get('risk_per_trade_pct', 0) > 10:
        errors.append("risk_per_trade_pct seems too high (>10%)")
    
    # Threshold validation
    thresholds = settings.get('thresholds', {})
    if thresholds.get('enter', 0) <= thresholds.get('exit', 0):
        errors.append("enter threshold should be higher than exit threshold")
    
    if thresholds.get('trade_stop_loss_bps', 0) <= 0:
        errors.append("trade_stop_loss_bps must be positive")
    
    if thresholds.get('daily_stop_loss_pct', 0) <= 0:
        errors.append("daily_stop_loss_pct must be positive")
    
    # Strategy validation
    strategies = settings.get('strategies', {})
    if not any(strategies.values()):
        errors.append("At least one strategy must be enabled")
    
    # Weight validation
    weights = settings.get('strategy_weights', {})
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > 0.01:  # Allow small floating point errors
        errors.append(f"Strategy weights should sum to 1.0 (currently {total_weight:.2f})")
    
    return len(errors) == 0, errors


class SettingsManager:
    """Manage settings with validation and persistence"""
    
    def __init__(self, state_store=None):
        self.state = state_store
        self.current_settings = DEFAULT_SETTINGS.copy()
        
        # Load persisted overrides if available
        if self.state:
            self.load_from_state()
    
    def load_from_state(self):
        """Load settings overrides from state store"""
        if not self.state:
            return
        
        # Load each settings category from KV store
        for category in DEFAULT_SETTINGS.keys():
            stored = self.state.get_kv(f'settings.{category}')
            if stored:
                try:
                    import json
                    overrides = json.loads(stored)
                    self.current_settings[category] = merge_settings(
                        self.current_settings[category],
                        overrides
                    )
                except Exception as e:
                    print(f"Error loading {category} settings: {e}")
    
    def update(self, updates: Dict[str, Any]) -> tuple[bool, str]:
        """
        Update settings with validation
        Returns (success, message)
        """
        # Merge with current settings
        new_settings = merge_settings(self.current_settings, updates)
        
        # Validate
        is_valid, errors = validate_settings(new_settings)
        if not is_valid:
            return False, f"Validation failed: {'; '.join(errors)}"
        
        # Apply updates
        self.current_settings = new_settings
        
        # Persist to state store
        if self.state:
            for category, values in updates.items():
                if category in DEFAULT_SETTINGS:
                    import json
                    self.state.set_kv(
                        f'settings.{category}',
                        json.dumps(values)
                    )
        
        return True, "Settings updated successfully"
    
    def get(self, key: str = None, default=None):
        """Get setting value by dot-notation key (e.g., 'risk.max_positions')"""
        if key is None:
            return self.current_settings
        
        keys = key.split('.')
        value = self.current_settings
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
        
        return value
    
    def reset_to_defaults(self):
        """Reset all settings to defaults"""
        self.current_settings = DEFAULT_SETTINGS.copy()
        
        # Clear from state store
        if self.state:
            for category in DEFAULT_SETTINGS.keys():
                self.state.delete_kv(f'settings.{category}')
    
    def export_json(self) -> str:
        """Export settings as JSON string"""
        import json
        return json.dumps(self.current_settings, indent=2)
    
    def import_json(self, json_str: str) -> tuple[bool, str]:
        """Import settings from JSON string"""
        try:
            import json
            settings = json.loads(json_str)
            return self.update(settings)
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e}"
        except Exception as e:
            return False, f"Error importing settings: {e}"

        def get_settings():
            """Get current settings"""
            import sys
            settings = {}
            current_module = sys.modules[__name__]
            for name in dir(current_module):
                if not name.startswith('_') and not callable(getattr(current_module, name)):
                    value = getattr(current_module, name)
                    if isinstance(value, (dict, list, str, int, float, bool)):
                        settings[name] = value
            return settings

        def update_settings(updates):
            """Update settings"""
            for key, value in updates.items():
                set_kv(f'settings.{key}', json.dumps(value))
            return {'status': 'success', 'message': f'Updated {len(updates)} settings'}
