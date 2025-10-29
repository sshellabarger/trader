"""
Strategy-Specific Configuration Parameters

Each strategy has its own optimal parameters for:
- Entry threshold
- Exit threshold
- Stop loss percentage
- Take profit percentage
- Maximum hold time
- Test duration
"""

from typing import Dict, Any


# Strategy-specific parameter configurations
# Using string keys to avoid circular import with StrategyType enum
STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {
    "momentum": {
        'entry_threshold': 0.65,       # Higher threshold for momentum - need strong signal
        'exit_threshold': 0.40,        # Exit when momentum weakens
        'stop_loss_pct': 0.8,          # Tighter stop for momentum trades
        'take_profit_pct': 2.5,        # Momentum can run farther
        'max_hold_minutes': 180,       # Shorter hold for intraday momentum
        'default_duration': 60,        # 1 hour test is enough for momentum
        'position_size_pct': 2.5,      # Slightly larger position for strong signals
    },

    "mean_reversion": {
        'entry_threshold': 0.60,       # Need clear deviation from mean
        'exit_threshold': 0.35,        # Exit near mean
        'stop_loss_pct': 1.0,          # Wider stop - give it room to revert
        'take_profit_pct': 1.5,        # Smaller targets - quick mean reversion
        'max_hold_minutes': 240,       # Hold longer for reversion
        'default_duration': 90,        # Longer test window for mean reversion
        'position_size_pct': 2.0,      # Standard position sizing
    },

    "news": {
        'entry_threshold': 0.55,       # News can be powerful, moderate threshold
        'exit_threshold': 0.30,        # Exit when news impact fades
        'stop_loss_pct': 1.2,          # Wider stop - news can be volatile
        'take_profit_pct': 3.0,        # News can drive big moves
        'max_hold_minutes': 300,       # News impact can last several hours
        'default_duration': 120,       # 2 hour test for news strategy
        'position_size_pct': 1.8,      # Smaller size due to unpredictability
    },

    "volume": {
        'entry_threshold': 0.50,       # Volume signals are common, moderate threshold
        'exit_threshold': 0.30,        # Exit when volume normalizes
        'stop_loss_pct': 0.7,          # Tighter stop for volume breakouts
        'take_profit_pct': 2.0,        # Standard targets
        'max_hold_minutes': 180,       # Volume moves are often quick
        'default_duration': 60,        # 1 hour test window
        'position_size_pct': 2.0,      # Standard position sizing
    },

    "earnings": {
        'entry_threshold': 0.70,       # High threshold - earnings are scheduled
        'exit_threshold': 0.40,        # Hold through announcement
        'stop_loss_pct': 1.5,          # Wide stop - earnings are volatile
        'take_profit_pct': 4.0,        # Earnings can drive large moves
        'max_hold_minutes': 480,       # May hold through earnings (8 hours)
        'default_duration': 240,       # 4 hour test window
        'position_size_pct': 1.5,      # Smaller size due to event risk
    },

    "longterm_trend": {
        'entry_threshold': 0.58,       # Moderate threshold for trend following
        'exit_threshold': 0.35,        # Exit when trend weakens
        'stop_loss_pct': 1.5,          # Wider stop for longer-term trades
        'take_profit_pct': 5.0,        # Larger targets for trend trades
        'max_hold_minutes': 600,       # Can hold for full day (10 hours)
        'default_duration': 180,       # 3 hour test window
        'position_size_pct': 2.5,      # Larger size for strong trends
    },

    "longterm_momentum": {
        'entry_threshold': 0.62,       # Need sustained momentum
        'exit_threshold': 0.38,        # Exit when momentum fades
        'stop_loss_pct': 1.3,          # Wide stop for longer-term momentum
        'take_profit_pct': 4.5,        # Large targets for sustained moves
        'max_hold_minutes': 540,       # 9 hour hold time
        'default_duration': 180,       # 3 hour test window
        'position_size_pct': 2.3,      # Good size for momentum trades
    },

    "crypto": {
        'entry_threshold': 0.60,       # Higher threshold with advanced indicators
        'exit_threshold': 0.40,        # Exit when signal weakens significantly
        'stop_loss_pct': 2.5,          # Wide stop - crypto is very volatile
        'take_profit_pct': 6.0,        # Large targets - crypto can move 5-10%
        'max_hold_minutes': 480,       # 8 hours - crypto trades 24/7
        'default_duration': 180,       # 3 hour test window
        'position_size_pct': 1.2,      # Conservative size due to volatility

        # Crypto-specific parameters
        'use_advanced_indicators': True,  # Enable RSI, MACD, Bollinger Bands
        'min_rsi': 30,                    # Don't buy if RSI < 30 (oversold extreme)
        'max_rsi': 80,                    # Don't buy if RSI > 80 (overbought extreme)
        'require_volume_confirmation': True,  # Require volume > average
        'min_volume_ratio': 0.8,          # Minimum volume ratio (80% of average)
        'volatility_adjustment': True,    # Adjust position size by volatility
        'trailing_stop_activation': 3.0,  # Activate trailing stop at 3% profit
        'trailing_stop_distance': 1.5,    # Trail by 1.5%
    },

    "forex": {
        'entry_threshold': 0.62,       # Higher threshold - forex is technical
        'exit_threshold': 0.38,        # Exit when technical signals weaken
        'stop_loss_pct': 0.5,          # Tight stop - forex moves in small pips
        'take_profit_pct': 1.2,        # Smaller targets - typical forex swing
        'max_hold_minutes': 360,       # 6 hours - forex trades 24/5
        'default_duration': 120,       # 2 hour test window
        'position_size_pct': 2.0,      # Standard position sizing

        # Forex-specific parameters
        'use_advanced_indicators': True,  # Enable RSI, MACD, ATR, Pivots
        'min_rsi': 30,                    # Don't buy if RSI < 30 (oversold)
        'max_rsi': 70,                    # Don't buy if RSI > 70 (overbought)
        'max_atr_pct': 2.5,               # Don't trade if ATR > 2.5% (too volatile)
        'min_atr_pct': 0.2,               # Don't trade if ATR < 0.2% (too quiet)
        'respect_pivot_levels': True,     # Use pivot points for entry/exit
        'trailing_stop_activation': 0.8,  # Activate trailing stop at 0.8% profit
        'trailing_stop_distance': 0.3,    # Trail by 0.3% (30 pips on typical pair)
        'use_ema_confirmation': True,     # Require EMA crossover confirmation
    },

    "etf": {
        'entry_threshold': 0.58,       # Moderate threshold - ETFs are less volatile
        'exit_threshold': 0.35,        # Exit when momentum fades
        'stop_loss_pct': 1.0,          # Standard stop for ETFs
        'take_profit_pct': 2.5,        # Moderate targets - ETFs move with sectors
        'max_hold_minutes': 300,       # 5 hours - hold through day
        'default_duration': 120,       # 2 hour test window
        'position_size_pct': 3.0,      # Larger size - ETFs are diversified

        # ETF-specific parameters
        'require_high_volume': True,      # ETFs must have strong volume
        'min_volume_ratio': 1.0,          # Require at least average volume
        'sector_rotation_enabled': True,  # Use sector performance signals
        'sector_boost_threshold': 0.01,   # Boost if sector up >1%
        'prefer_broad_market': True,      # Prefer SPY, QQQ, IWM over narrow ETFs
        'max_spread_bps': 10,             # Maximum 10 basis point spread
        'check_nav_premium': False,       # Check for premium/discount to NAV (future)
    },
}


def get_strategy_config(strategy) -> Dict[str, Any]:
    """
    Get configuration parameters for a specific strategy

    Args:
        strategy: The strategy type (StrategyType enum or string)

    Returns:
        Dictionary of configuration parameters
    """
    # Handle both StrategyType enum and string inputs
    if hasattr(strategy, 'value'):
        strategy_key = strategy.value
    else:
        strategy_key = str(strategy)

    return STRATEGY_CONFIGS.get(strategy_key, {
        # Default fallback configuration
        'entry_threshold': 0.5,
        'exit_threshold': 0.3,
        'stop_loss_pct': 0.5,
        'take_profit_pct': 2.0,
        'max_hold_minutes': 240,
        'default_duration': 60,
        'position_size_pct': 2.0,
    })


def get_all_strategy_configs() -> Dict[str, Dict[str, Any]]:
    """Get all strategy configurations"""
    return STRATEGY_CONFIGS.copy()


def print_strategy_config(strategy):
    """Print configuration for a strategy"""
    config = get_strategy_config(strategy)
    strategy_name = strategy.value if hasattr(strategy, 'value') else str(strategy)
    print(f"\n{'='*60}")
    print(f"Configuration for {strategy_name.upper()}")
    print(f"{'='*60}")
    for key, value in config.items():
        print(f"  {key:20s}: {value}")
    print(f"{'='*60}\n")


def print_all_configs():
    """Print all strategy configurations"""
    print("\n" + "="*80)
    print("STRATEGY-SPECIFIC CONFIGURATIONS")
    print("="*80)

    for strategy_key in STRATEGY_CONFIGS.keys():
        config = STRATEGY_CONFIGS[strategy_key]
        print(f"\n{strategy_key.upper()}:")
        for key, value in config.items():
            print(f"  {key:25s}: {value}")

    print("\n" + "="*80)
