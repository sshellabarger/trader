"""
Strategy Parameter Optimization

This module provides tools for optimizing strategy parameters through
systematic parameter sweeps and analysis.
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Any, Tuple
from itertools import product
import os

from .strategy_testing import (
    StrategyTestConfig, StrategyBacktester, StrategyType,
    DetailedStrategyMetrics
)
from .broker_alpaca import AlpacaBroker

logger = logging.getLogger(__name__)


@dataclass
class ParameterRange:
    """Definition of a parameter range for optimization"""
    name: str
    values: List[Any]
    description: str = ""


@dataclass
class OptimizationResult:
    """Result of a single parameter combination test"""
    parameters: Dict[str, Any]
    metrics: DetailedStrategyMetrics
    rank_score: float  # Composite score for ranking

    def to_dict(self) -> Dict[str, Any]:
        return {
            'parameters': self.parameters,
            'metrics': self.metrics.to_dict(),
            'rank_score': self.rank_score
        }


class StrategyOptimizer:
    """
    Optimize strategy parameters through systematic testing

    Tests multiple parameter combinations and ranks them by performance.
    """

    def __init__(self, strategy: StrategyType, broker: AlpacaBroker = None):
        self.strategy = strategy
        self.broker = broker or AlpacaBroker()
        self.results: List[OptimizationResult] = []

        logger.info(f"Initialized StrategyOptimizer for {strategy.value}")

    def optimize(self,
                 parameter_ranges: List[ParameterRange],
                 base_config: StrategyTestConfig,
                 ranking_weights: Dict[str, float] = None) -> List[OptimizationResult]:
        """
        Run parameter sweep optimization

        Args:
            parameter_ranges: List of parameters to sweep
            base_config: Base configuration to modify
            ranking_weights: Weights for ranking metrics (default: balanced)

        Returns:
            List of OptimizationResult sorted by rank_score
        """
        if ranking_weights is None:
            ranking_weights = {
                'total_pnl': 0.3,
                'win_rate': 0.2,
                'profit_factor': 0.15,
                'sharpe_ratio': 0.15,
                'max_drawdown_pct': -0.1,  # Negative because lower is better
                'score_predictive_power': 0.1
            }

        # Generate all parameter combinations
        param_names = [pr.name for pr in parameter_ranges]
        param_values = [pr.values for pr in parameter_ranges]
        combinations = list(product(*param_values))

        total_combinations = len(combinations)
        logger.info(f"Testing {total_combinations} parameter combinations")

        self.results = []

        for i, combo in enumerate(combinations, 1):
            params = dict(zip(param_names, combo))
            logger.info(f"\nTesting combination {i}/{total_combinations}: {params}")

            # Create config with these parameters
            config = self._create_config(base_config, params)

            try:
                # Run test
                backtester = StrategyBacktester(config, self.broker)
                metrics = backtester.run_live_test()

                # Calculate rank score
                rank_score = self._calculate_rank_score(metrics, ranking_weights)

                result = OptimizationResult(
                    parameters=params,
                    metrics=metrics,
                    rank_score=rank_score
                )

                self.results.append(result)

                logger.info(f"Rank Score: {rank_score:.4f} | P&L: ${metrics.total_pnl:,.2f} | "
                          f"Win Rate: {metrics.win_rate:.1f}%")

            except Exception as e:
                logger.error(f"Error testing combination {params}: {e}")
                continue

        # Sort by rank score
        self.results.sort(key=lambda r: r.rank_score, reverse=True)

        logger.info(f"\nOptimization complete. Top 3 combinations:")
        for i, result in enumerate(self.results[:3], 1):
            logger.info(f"{i}. Score: {result.rank_score:.4f} | Params: {result.parameters}")

        return self.results

    def _create_config(self, base_config: StrategyTestConfig,
                      params: Dict[str, Any]) -> StrategyTestConfig:
        """Create a test config with modified parameters"""
        config_dict = asdict(base_config)

        # Update top-level parameters
        for key, value in params.items():
            if key in config_dict:
                config_dict[key] = value
            else:
                # Assume it's a strategy-specific parameter
                if 'strategy_params' not in config_dict:
                    config_dict['strategy_params'] = {}
                config_dict['strategy_params'][key] = value

        # Reconstruct config
        return StrategyTestConfig(**config_dict)

    def _calculate_rank_score(self, metrics: DetailedStrategyMetrics,
                              weights: Dict[str, float]) -> float:
        """
        Calculate composite rank score

        Args:
            metrics: Strategy metrics
            weights: Weight for each metric (can be negative for inverse metrics)

        Returns:
            Composite score (higher is better)
        """
        score = 0.0

        for metric_name, weight in weights.items():
            value = getattr(metrics, metric_name, 0)

            # Normalize some metrics
            if metric_name == 'win_rate':
                normalized = value / 100.0  # 0-1 range
            elif metric_name == 'max_drawdown_pct':
                normalized = min(value / 50.0, 1.0)  # Cap at 50%
            elif metric_name == 'total_pnl':
                # Normalize by starting capital
                normalized = value / metrics.test_parameters.get('starting_capital', 100000)
            elif metric_name == 'profit_factor':
                normalized = min(value / 3.0, 1.0)  # Cap at 3.0
            elif metric_name == 'sharpe_ratio':
                normalized = min(max(value / 2.0, 0), 1.0)  # Cap at 2.0, floor at 0
            elif metric_name == 'score_predictive_power':
                normalized = (value + 1) / 2  # -1 to 1 -> 0 to 1
            else:
                normalized = value

            score += normalized * weight

        return score

    def export_results(self, output_dir: str = "./optimization_results"):
        """Export optimization results to JSON files"""
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.strategy.value}_optimization_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        data = {
            'strategy': self.strategy.value,
            'timestamp': datetime.now().isoformat(),
            'total_combinations': len(self.results),
            'results': [r.to_dict() for r in self.results]
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Optimization results exported to {filepath}")

        # Also export top 5 summary
        summary_file = os.path.join(output_dir, f"{self.strategy.value}_top5_{timestamp}.json")
        summary = {
            'strategy': self.strategy.value,
            'timestamp': datetime.now().isoformat(),
            'top_5_combinations': [
                {
                    'rank': i + 1,
                    'parameters': r.parameters,
                    'rank_score': r.rank_score,
                    'total_pnl': r.metrics.total_pnl,
                    'win_rate': r.metrics.win_rate,
                    'profit_factor': r.metrics.profit_factor,
                    'sharpe_ratio': r.metrics.sharpe_ratio,
                    'max_drawdown_pct': r.metrics.max_drawdown_pct
                }
                for i, r in enumerate(self.results[:5])
            ]
        }

        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Top 5 summary exported to {summary_file}")

        return filepath, summary_file

    def print_summary(self, top_n: int = 10):
        """Print summary of optimization results"""
        print("\n" + "="*100)
        print(f"OPTIMIZATION RESULTS: {self.strategy.value.upper()}")
        print("="*100)
        print(f"\nTotal Combinations Tested: {len(self.results)}")
        print(f"\nTop {min(top_n, len(self.results))} Parameter Combinations:")
        print("-"*100)

        for i, result in enumerate(self.results[:top_n], 1):
            print(f"\n#{i} - Rank Score: {result.rank_score:.4f}")
            print(f"Parameters: {result.parameters}")
            print(f"  Total P&L: ${result.metrics.total_pnl:,.2f} ({result.metrics.total_pnl_pct:+.2f}%)")
            print(f"  Win Rate: {result.metrics.win_rate:.1f}%")
            print(f"  Profit Factor: {result.metrics.profit_factor:.2f}")
            print(f"  Sharpe Ratio: {result.metrics.sharpe_ratio:.2f}")
            print(f"  Max Drawdown: {result.metrics.max_drawdown_pct:.2f}%")
            print(f"  Trades: {result.metrics.total_trades}")

        print("\n" + "="*100)


def create_default_parameter_ranges(strategy: StrategyType) -> List[ParameterRange]:
    """
    Create default parameter ranges for optimization based on strategy type

    Args:
        strategy: Strategy to optimize

    Returns:
        List of parameter ranges to test
    """
    # Common parameters for all strategies
    common_ranges = [
        ParameterRange(
            name='entry_threshold',
            values=[0.4, 0.5, 0.6, 0.7],
            description='Minimum score to enter position'
        ),
        ParameterRange(
            name='stop_loss_pct',
            values=[0.3, 0.5, 0.7, 1.0],
            description='Stop loss percentage'
        ),
        ParameterRange(
            name='take_profit_pct',
            values=[1.5, 2.0, 2.5, 3.0],
            description='Take profit percentage'
        ),
        ParameterRange(
            name='max_hold_minutes',
            values=[120, 180, 240, 360],
            description='Maximum hold time in minutes'
        ),
    ]

    # Strategy-specific parameters
    strategy_specific = {}

    # Momentum
    strategy_specific[StrategyType.MOMENTUM] = []

    # Mean Reversion
    strategy_specific[StrategyType.MEAN_REVERSION] = []

    # News
    strategy_specific[StrategyType.NEWS] = [
        ParameterRange(
            name='news_window_hours',
            values=[3, 6, 12, 24],
            description='News lookback window in hours'
        )
    ]

    # Volume
    strategy_specific[StrategyType.VOLUME] = []

    # Earnings
    strategy_specific[StrategyType.EARNINGS] = [
        ParameterRange(
            name='earnings_days_limit',
            values=[3, 5, 7, 10],
            description='Days until earnings to consider'
        )
    ]

    # Long-term Trend
    strategy_specific[StrategyType.LONGTERM_TREND] = []

    # Long-term Momentum
    strategy_specific[StrategyType.LONGTERM_MOMENTUM] = []

    # Crypto
    strategy_specific[StrategyType.CRYPTO] = [
        ParameterRange(
            name='stop_loss_pct',
            values=[0.5, 1.0, 1.5, 2.0],
            description='Stop loss percentage (higher for crypto volatility)'
        ),
    ]

    # Combine common and strategy-specific
    ranges = common_ranges.copy()
    if strategy in strategy_specific:
        ranges.extend(strategy_specific[strategy])

    return ranges


def quick_optimization_test(strategy: StrategyType,
                            live_duration_minutes: int = 30,
                            test_symbols: List[str] = None) -> List[OptimizationResult]:
    """
    Quick optimization test with default parameter ranges

    Args:
        strategy: Strategy to optimize
        live_duration_minutes: How long to run live test
        test_symbols: Symbols to test (uses defaults if None)

    Returns:
        Sorted list of optimization results
    """
    logger.info(f"Starting quick optimization for {strategy.value}")

    # Create base config
    base_config = StrategyTestConfig(
        strategy=strategy,
        mode='live',
        live_duration_minutes=live_duration_minutes,
        test_symbols=test_symbols or [],
        starting_capital=100000.0
    )

    # Get default parameter ranges
    param_ranges = create_default_parameter_ranges(strategy)

    # Run optimization
    optimizer = StrategyOptimizer(strategy)
    results = optimizer.optimize(param_ranges, base_config)

    # Print and export
    optimizer.print_summary()
    optimizer.export_results()

    return results
