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
from .strategy_testing import StrategyType


# Strategy-specific parameter configurations
STRATEGY_CONFIGS: Dict[StrategyType, Dict[str, Any]] = {
    StrategyType.MOMENTUM: {
        'entry_threshold': 0.65,       # Higher threshold for momentum - need strong signal
        'exit_threshold': 0.40,        # Exit when momentum weakens
        'stop_loss_pct': 0.8,          # Tighter stop for momentum trades
        'take_profit_pct': 2.5,        # Momentum can run farther
        'max_hold_minutes': 180,       # Shorter hold for intraday momentum
        'default_duration': 60,        # 1 hour test is enough for momentum
        'position_size_pct': 2.5,      # Slightly larger position for strong signals
    },

    StrategyType.MEAN_REVERSION: {
        'entry_threshold': 0.60,       # Need clear deviation from mean
        'exit_threshold': 0.35,        # Exit near mean
        'stop_loss_pct': 1.0,          # Wider stop - give it room to revert
        'take_profit_pct': 1.5,        # Smaller targets - quick mean reversion
        'max_hold_minutes': 240,       # Hold longer for reversion
        'default_duration': 90,        # Longer test window for mean reversion
        'position_size_pct': 2.0,      # Standard position sizing
    },

    StrategyType.NEWS: {
        'entry_threshold': 0.55,       # News can be powerful, moderate threshold
        'exit_threshold': 0.30,        # Exit when news impact fades
        'stop_loss_pct': 1.2,          # Wider stop - news can be volatile
        'take_profit_pct': 3.0,        # News can drive big moves
        'max_hold_minutes': 300,       # News impact can last several hours
        'default_duration': 120,       # 2 hour test for news strategy
        'position_size_pct': 1.8,      # Smaller size due to unpredictability
    },

    StrategyType.VOLUME: {
        'entry_threshold': 0.50,       # Volume signals are common, moderate threshold
        'exit_threshold': 0.30,        # Exit when volume normalizes
        'stop_loss_pct': 0.7,          # Tighter stop for volume breakouts
        'take_profit_pct': 2.0,        # Standard targets
        'max_hold_minutes': 180,       # Volume moves are often quick
        'default_duration': 60,        # 1 hour test window
        'position_size_pct': 2.0,      # Standard position sizing
    },

    StrategyType.EARNINGS: {
        'entry_threshold': 0.70,       # High threshold - earnings are scheduled
        'exit_threshold': 0.40,        # Hold through announcement
        'stop_loss_pct': 1.5,          # Wide stop - earnings are volatile
        'take_profit_pct': 4.0,        # Earnings can drive large moves
        'max_hold_minutes': 480,       # May hold through earnings (8 hours)
        'default_duration': 240,       # 4 hour test window
        'position_size_pct': 1.5,      # Smaller size due to event risk
    },

    StrategyType.LONGTERM_TREND: {
        'entry_threshold': 0.58,       # Moderate threshold for trend following
        'exit_threshold': 0.35,        # Exit when trend weakens
        'stop_loss_pct': 1.5,          # Wider stop for longer-term trades
        'take_profit_pct': 5.0,        # Larger targets for trend trades
        'max_hold_minutes': 600,       # Can hold for full day (10 hours)
        'default_duration': 180,       # 3 hour test window
        'position_size_pct': 2.5,      # Larger size for strong trends
    },

    StrategyType.LONGTERM_MOMENTUM: {
        'entry_threshold': 0.62,       # Need sustained momentum
        'exit_threshold': 0.38,        # Exit when momentum fades
        'stop_loss_pct': 1.3,          # Wide stop for longer-term momentum
        'take_profit_pct': 4.5,        # Large targets for sustained moves
        'max_hold_minutes': 540,       # 9 hour hold time
        'default_duration': 180,       # 3 hour test window
        'position_size_pct': 2.3,      # Good size for momentum trades
    },

    StrategyType.CRYPTO: {
        'entry_threshold': 0.55,       # Moderate threshold for crypto
        'exit_threshold': 0.35,        # Exit when signal weakens
        'stop_loss_pct': 2.0,          # Much wider stop - crypto is volatile
        'take_profit_pct': 5.0,        # Large targets - crypto moves big
        'max_hold_minutes': 360,       # 6 hours - crypto trades 24/7
        'default_duration': 120,       # 2 hour test window
        'position_size_pct': 1.5,      # Smaller size due to volatility
    },
}


def get_strategy_config(strategy: StrategyType) -> Dict[str, Any]:
    """
    Get configuration parameters for a specific strategy

    Args:
        strategy: The strategy type

    Returns:
        Dictionary of configuration parameters
    """
    return STRATEGY_CONFIGS.get(strategy, {
        # Default fallback configuration
        'entry_threshold': 0.5,
        'exit_threshold': 0.3,
        'stop_loss_pct': 0.5,
        'take_profit_pct': 2.0,
        'max_hold_minutes': 240,
        'default_duration': 60,
        'position_size_pct': 2.0,
    })


def get_all_strategy_configs() -> Dict[StrategyType, Dict[str, Any]]:
    """Get all strategy configurations"""
    return STRATEGY_CONFIGS.copy()


def print_strategy_config(strategy: StrategyType):
    """Print configuration for a strategy"""
    config = get_strategy_config(strategy)
    print(f"\n{'='*60}")
    print(f"Configuration for {strategy.value.upper()}")
    print(f"{'='*60}")
    for key, value in config.items():
        print(f"  {key:20s}: {value}")
    print(f"{'='*60}\n")


def print_all_configs():
    """Print all strategy configurations"""
    print("\n" + "="*80)
    print("STRATEGY-SPECIFIC CONFIGURATIONS")
    print("="*80)

    for strategy_type in StrategyType:
        config = get_strategy_config(strategy_type)
        print(f"\n{strategy_type.value.upper()}:")
        for key, value in config.items():
            print(f"  {key:25s}: {value}")

    print("\n" + "="*80)
