#!/usr/bin/env python3
"""
Example: Using the Strategy Testing Framework

This script demonstrates how to use the strategy testing framework
programmatically to test, optimize, and compare trading strategies.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from trading_bot.strategy_testing import (
    StrategyType, StrategyTestConfig, StrategyBacktester,
    DetailedStrategyMetrics, compare_strategies
)
from trading_bot.strategy_optimizer import (
    StrategyOptimizer, ParameterRange, create_default_parameter_ranges
)
from trading_bot.broker_alpaca import AlpacaBroker


def example_1_basic_test():
    """Example 1: Run a basic strategy test"""
    print("\n" + "="*80)
    print("EXAMPLE 1: Basic Strategy Test")
    print("="*80)

    # Configure test
    config = StrategyTestConfig(
        strategy=StrategyType.MOMENTUM,
        mode='live',
        live_duration_minutes=30,  # 30 minute test
        entry_threshold=0.6,
        stop_loss_pct=0.5,
        take_profit_pct=2.0,
        test_symbols=['AAPL', 'MSFT', 'GOOGL'],
        starting_capital=100000.0
    )

    print(f"\nTesting {config.strategy.value} strategy for {config.live_duration_minutes} minutes...")

    # Run test
    backtester = StrategyBacktester(config)
    metrics = backtester.run_live_test()

    # Show results
    print("\nTest Results:")
    print(f"  Total Trades: {metrics.total_trades}")
    print(f"  Win Rate: {metrics.win_rate:.1f}%")
    print(f"  Total P&L: ${metrics.total_pnl:,.2f} ({metrics.total_pnl_pct:+.2f}%)")
    print(f"  Profit Factor: {metrics.profit_factor:.2f}")
    print(f"  Sharpe Ratio: {metrics.sharpe_ratio:.2f}")
    print(f"  Max Drawdown: {metrics.max_drawdown_pct:.2f}%")

    # Save results
    metrics.to_json('example_momentum_test.json')
    print(f"\nFull results saved to: example_momentum_test.json")

    return metrics


def example_2_compare_strategies():
    """Example 2: Test and compare multiple strategies"""
    print("\n" + "="*80)
    print("EXAMPLE 2: Compare Multiple Strategies")
    print("="*80)

    strategies_to_test = [
        StrategyType.MOMENTUM,
        StrategyType.MEAN_REVERSION,
        StrategyType.VOLUME
    ]

    all_metrics = []

    for strategy in strategies_to_test:
        print(f"\nTesting {strategy.value}...")

        config = StrategyTestConfig(
            strategy=strategy,
            mode='live',
            live_duration_minutes=15,  # Quick test
            test_symbols=['AAPL', 'MSFT'],
            starting_capital=100000.0
        )

        backtester = StrategyBacktester(config)
        metrics = backtester.run_live_test()
        all_metrics.append(metrics)

        print(f"  Result: {metrics.total_trades} trades, "
              f"{metrics.win_rate:.1f}% win rate, "
              f"${metrics.total_pnl:,.2f} P&L")

    # Compare results
    print("\nGenerating comparison report...")
    comparison = compare_strategies(all_metrics, 'example_comparison.json')

    print("\nTop strategy by Total P&L:")
    top_pnl = comparison['rankings']['Total P&L'][0]
    print(f"  {top_pnl['strategy']}: ${top_pnl['value']:,.2f}")

    print("\nTop strategy by Win Rate:")
    top_wr = comparison['rankings']['Win Rate'][0]
    print(f"  {top_wr['strategy']}: {top_wr['value']:.1f}%")

    return comparison


def example_3_optimize_parameters():
    """Example 3: Optimize strategy parameters"""
    print("\n" + "="*80)
    print("EXAMPLE 3: Parameter Optimization")
    print("="*80)

    # Define parameter ranges to test
    param_ranges = [
        ParameterRange(
            name='entry_threshold',
            values=[0.5, 0.6, 0.7],
            description='Entry score threshold'
        ),
        ParameterRange(
            name='stop_loss_pct',
            values=[0.3, 0.5],
            description='Stop loss percentage'
        ),
        ParameterRange(
            name='take_profit_pct',
            values=[2.0, 2.5],
            description='Take profit percentage'
        )
    ]

    # Base configuration
    base_config = StrategyTestConfig(
        strategy=StrategyType.MOMENTUM,
        mode='live',
        live_duration_minutes=15,  # Quick test per combination
        test_symbols=['AAPL'],
        starting_capital=100000.0
    )

    print(f"\nOptimizing {base_config.strategy.value} with {3*2*2} = 12 combinations...")

    # Run optimization
    optimizer = StrategyOptimizer(StrategyType.MOMENTUM)
    results = optimizer.optimize(param_ranges, base_config)

    # Show top 3 results
    print("\nTop 3 Parameter Combinations:")
    for i, result in enumerate(results[:3], 1):
        print(f"\n#{i} - Rank Score: {result.rank_score:.4f}")
        print(f"  Parameters: {result.parameters}")
        print(f"  P&L: ${result.metrics.total_pnl:,.2f}")
        print(f"  Win Rate: {result.metrics.win_rate:.1f}%")
        print(f"  Profit Factor: {result.metrics.profit_factor:.2f}")

    # Export results
    optimizer.export_results('./example_optimization')
    print("\nOptimization results exported to: ./example_optimization/")

    return results


def example_4_detailed_analysis():
    """Example 4: Detailed metric analysis"""
    print("\n" + "="*80)
    print("EXAMPLE 4: Detailed Metrics Analysis")
    print("="*80)

    # Run test
    config = StrategyTestConfig(
        strategy=StrategyType.MOMENTUM,
        mode='live',
        live_duration_minutes=20,
        test_symbols=['AAPL', 'MSFT'],
        starting_capital=100000.0
    )

    backtester = StrategyBacktester(config)
    metrics = backtester.run_live_test()

    # Analyze results
    print("\nPerformance Analysis:")

    # Overall assessment
    print(f"\n1. Overall Performance:")
    print(f"   Win Rate: {metrics.win_rate:.1f}% ", end="")
    if metrics.win_rate >= 55:
        print("✅ Good")
    elif metrics.win_rate >= 45:
        print("⚠️ Acceptable")
    else:
        print("❌ Needs improvement")

    print(f"   Profit Factor: {metrics.profit_factor:.2f} ", end="")
    if metrics.profit_factor >= 2.0:
        print("✅ Excellent")
    elif metrics.profit_factor >= 1.5:
        print("⚠️ Good")
    elif metrics.profit_factor >= 1.0:
        print("⚠️ Acceptable")
    else:
        print("❌ Unprofitable")

    # Risk analysis
    print(f"\n2. Risk Assessment:")
    print(f"   Max Drawdown: {metrics.max_drawdown_pct:.2f}% ", end="")
    if metrics.max_drawdown_pct <= 5:
        print("✅ Low risk")
    elif metrics.max_drawdown_pct <= 10:
        print("⚠️ Moderate risk")
    else:
        print("❌ High risk")

    print(f"   Sharpe Ratio: {metrics.sharpe_ratio:.2f} ", end="")
    if metrics.sharpe_ratio >= 1.5:
        print("✅ Excellent risk-adjusted return")
    elif metrics.sharpe_ratio >= 1.0:
        print("⚠️ Good risk-adjusted return")
    else:
        print("❌ Poor risk-adjusted return")

    # Signal quality
    print(f"\n3. Signal Quality:")
    print(f"   Score Predictive Power: {metrics.score_predictive_power:.3f} ", end="")
    if metrics.score_predictive_power >= 0.3:
        print("✅ Scores are predictive")
    elif metrics.score_predictive_power >= 0.1:
        print("⚠️ Scores somewhat predictive")
    else:
        print("❌ Scores not predictive")

    # Exit analysis
    print(f"\n4. Exit Behavior:")
    if metrics.total_trades > 0:
        stop_pct = (metrics.stop_loss_exits / metrics.total_trades) * 100
        tp_pct = (metrics.take_profit_exits / metrics.total_trades) * 100
        print(f"   Stop Loss: {metrics.stop_loss_exits} ({stop_pct:.1f}%)")
        print(f"   Take Profit: {metrics.take_profit_exits} ({tp_pct:.1f}%)")

        if stop_pct > 50:
            print("   ⚠️ High stop loss rate - consider wider stops or better entries")
        if tp_pct < 30:
            print("   ⚠️ Low take profit rate - consider realistic profit targets")

    # Regime performance
    if metrics.regime_performance:
        print(f"\n5. Best Market Regime:")
        best_regime = max(metrics.regime_performance.items(),
                         key=lambda x: x[1].get('win_rate', 0))
        print(f"   {best_regime[0]}: {best_regime[1]['win_rate']:.1f}% win rate")

    # Recommendations
    print(f"\n6. AI Recommendations:")
    recommendations = []

    if metrics.win_rate < 50:
        recommendations.append("- Increase entry threshold to be more selective")
    if metrics.profit_factor < 1.5:
        recommendations.append("- Review risk/reward ratio (stop loss vs take profit)")
    if metrics.score_predictive_power < 0.2:
        recommendations.append("- Strategy scores may not be meaningful, review strategy logic")
    if metrics.max_drawdown_pct > 10:
        recommendations.append("- Reduce position size or tighten risk controls")
    if metrics.stop_loss_exits > metrics.take_profit_exits:
        recommendations.append("- Too many stop losses - improve entry timing or widen stops")

    if recommendations:
        for rec in recommendations:
            print(f"   {rec}")
    else:
        print("   ✅ Strategy performance looks good!")

    # Full report
    print("\nGenerating full report...")
    metrics.print_summary()

    return metrics


def main():
    """Run all examples"""
    print("\n" + "="*80)
    print("STRATEGY TESTING FRAMEWORK - EXAMPLES")
    print("="*80)

    try:
        # Example 1: Basic test
        # metrics1 = example_1_basic_test()

        # Example 2: Compare strategies
        # comparison = example_2_compare_strategies()

        # Example 3: Optimize parameters
        # results = example_3_optimize_parameters()

        # Example 4: Detailed analysis
        metrics4 = example_4_detailed_analysis()

        print("\n" + "="*80)
        print("ALL EXAMPLES COMPLETE")
        print("="*80)
        print("\nGenerated files:")
        print("  - example_momentum_test.json")
        print("  - example_comparison.json")
        print("  - example_optimization/")

    except KeyboardInterrupt:
        print("\n\nExamples interrupted by user")
    except Exception as e:
        print(f"\n\nError running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    # Note: These examples require a valid Alpaca API connection
    # and will perform live paper trading for the specified durations

    # Uncomment to run specific examples:
    # example_1_basic_test()
    # example_2_compare_strategies()
    # example_3_optimize_parameters()
    # example_4_detailed_analysis()

    # Or run all examples:
    # main()

    print("\n" + "="*80)
    print("STRATEGY TESTING EXAMPLES")
    print("="*80)
    print("\nUncomment the example you want to run in the __main__ section.")
    print("\nAvailable examples:")
    print("  1. example_1_basic_test() - Simple strategy test")
    print("  2. example_2_compare_strategies() - Compare multiple strategies")
    print("  3. example_3_optimize_parameters() - Parameter optimization")
    print("  4. example_4_detailed_analysis() - In-depth metric analysis")
    print("\nOr run main() to execute all examples.")
    print("\nNote: These examples will perform live paper trading tests.")
    print("="*80 + "\n")
