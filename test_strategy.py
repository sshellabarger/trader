#!/usr/bin/env python3
"""
Strategy Testing CLI

Command-line tool for testing trading strategies individually with
comprehensive metrics and optimization capabilities.

Usage:
    python test_strategy.py test momentum --duration 60
    python test_strategy.py test-all --duration 30
    python test_strategy.py optimize momentum --duration 30
    python test_strategy.py compare results/*.json
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import List

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_bot.strategy_testing import (
    StrategyType, StrategyTestConfig, StrategyBacktester,
    DetailedStrategyMetrics, compare_strategies
)
from trading_bot.strategy_optimizer import (
    StrategyOptimizer, create_default_parameter_ranges
)
from trading_bot.broker_alpaca import AlpacaBroker
from trading_bot.news import get_news_for_symbols
from trading_bot.earnings import fetch_earnings_calendar

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_single_strategy(args):
    """Test a single strategy"""
    logger.info(f"Testing strategy: {args.strategy}")

    # Parse strategy type
    try:
        strategy_type = StrategyType[args.strategy.upper()]
    except KeyError:
        logger.error(f"Invalid strategy: {args.strategy}")
        logger.error(f"Valid strategies: {', '.join([s.value for s in StrategyType])}")
        return

    # Parse test symbols
    test_symbols = []
    if args.symbols:
        test_symbols = [s.strip().upper() for s in args.symbols.split(',')]
    else:
        # Default symbols
        test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX']

    # Create config
    config = StrategyTestConfig(
        strategy=strategy_type,
        mode='live',
        live_duration_minutes=args.duration,
        entry_threshold=args.entry_threshold,
        exit_threshold=args.exit_threshold,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        max_hold_minutes=args.max_hold,
        position_size_pct=args.position_size,
        max_positions=args.max_positions,
        test_symbols=test_symbols,
        starting_capital=args.capital,
        output_dir=args.output_dir
    )

    logger.info(f"Test configuration: {config}")

    # Initialize broker
    broker = AlpacaBroker()

    # Fetch news and earnings if needed
    news_data = None
    earnings_calendar = None

    if strategy_type == StrategyType.NEWS:
        logger.info("Fetching news data...")
        news_data = get_news_for_symbols(test_symbols, hours=6)
        logger.info(f"Fetched {len(news_data)} news articles")

    if strategy_type == StrategyType.EARNINGS:
        logger.info("Fetching earnings calendar...")
        earnings_calendar = fetch_earnings_calendar()
        logger.info(f"Fetched earnings for {len(earnings_calendar)} symbols")

    # Run test
    logger.info(f"Starting {args.duration} minute test...")
    backtester = StrategyBacktester(config, broker)

    try:
        metrics = backtester.run_live_test(
            news_data=news_data,
            earnings_calendar=earnings_calendar
        )

        # Print results
        metrics.print_summary()

        # Save results
        os.makedirs(args.output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(
            args.output_dir,
            f"{strategy_type.value}_test_{timestamp}.json"
        )
        metrics.to_json(output_file)

        # Save trades
        if config.save_trades and backtester.trades:
            trades_file = os.path.join(
                args.output_dir,
                f"{strategy_type.value}_trades_{timestamp}.json"
            )
            with open(trades_file, 'w') as f:
                json.dump([t.to_dict() for t in backtester.trades], f, indent=2)
            logger.info(f"Trades saved to {trades_file}")

        # Save signals
        if config.save_signals and backtester.signals:
            signals_file = os.path.join(
                args.output_dir,
                f"{strategy_type.value}_signals_{timestamp}.json"
            )
            signal_data = [
                {
                    'timestamp': s.timestamp,
                    'symbol': s.symbol,
                    'score': s.score,
                    'details': s.details,
                    'regime': s.regime
                }
                for s in backtester.signals
            ]
            with open(signals_file, 'w') as f:
                json.dump(signal_data, f, indent=2)
            logger.info(f"Signals saved to {signals_file}")

        logger.info(f"\nTest complete! Results saved to {output_file}")

    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error running test: {e}", exc_info=True)
        sys.exit(1)


def test_all_strategies(args):
    """Test all strategies"""
    logger.info("Testing all strategies")

    all_strategies = [
        StrategyType.MOMENTUM,
        StrategyType.MEAN_REVERSION,
        StrategyType.NEWS,
        StrategyType.VOLUME,
        StrategyType.EARNINGS,
        StrategyType.LONGTERM_TREND,
        StrategyType.LONGTERM_MOMENTUM,
    ]

    if args.include_crypto:
        all_strategies.append(StrategyType.CRYPTO)

    results = []

    for strategy in all_strategies:
        logger.info(f"\n{'='*80}")
        logger.info(f"Testing {strategy.value.upper()}")
        logger.info(f"{'='*80}\n")

        # Update args for this strategy
        args.strategy = strategy.value
        test_single_strategy(args)

        # Load the result
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_file = os.path.join(
            args.output_dir,
            f"{strategy.value}_test_{timestamp}.json"
        )

        # Note: There might be a timing issue here; in production you'd
        # want to track the exact filename returned by test_single_strategy

    logger.info(f"\n{'='*80}")
    logger.info("ALL STRATEGY TESTS COMPLETE")
    logger.info(f"{'='*80}\n")
    logger.info(f"Results saved to {args.output_dir}")


def optimize_strategy(args):
    """Optimize strategy parameters"""
    logger.info(f"Optimizing strategy: {args.strategy}")

    # Parse strategy type
    try:
        strategy_type = StrategyType[args.strategy.upper()]
    except KeyError:
        logger.error(f"Invalid strategy: {args.strategy}")
        logger.error(f"Valid strategies: {', '.join([s.value for s in StrategyType])}")
        return

    # Parse test symbols
    test_symbols = []
    if args.symbols:
        test_symbols = [s.strip().upper() for s in args.symbols.split(',')]

    # Create base config
    base_config = StrategyTestConfig(
        strategy=strategy_type,
        mode='live',
        live_duration_minutes=args.duration,
        test_symbols=test_symbols,
        starting_capital=args.capital,
        output_dir=args.output_dir
    )

    # Get parameter ranges
    if args.quick:
        logger.info("Using quick optimization (reduced parameter space)")
        param_ranges = create_default_parameter_ranges(strategy_type)
        # Reduce the search space for quick test
        for pr in param_ranges:
            pr.values = pr.values[::2]  # Take every other value
    else:
        param_ranges = create_default_parameter_ranges(strategy_type)

    logger.info(f"Parameter ranges: {[(pr.name, pr.values) for pr in param_ranges]}")

    # Run optimization
    optimizer = StrategyOptimizer(strategy_type)

    try:
        results = optimizer.optimize(param_ranges, base_config)

        # Print results
        optimizer.print_summary(top_n=args.top_n)

        # Export results
        output_files = optimizer.export_results(args.output_dir)
        logger.info(f"\nOptimization complete! Results saved to {output_files[0]}")

    except KeyboardInterrupt:
        logger.info("\nOptimization interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error running optimization: {e}", exc_info=True)
        sys.exit(1)


def compare_results(args):
    """Compare multiple test results"""
    logger.info("Comparing strategy results")

    results = []

    for result_file in args.result_files:
        if not os.path.exists(result_file):
            logger.warning(f"Result file not found: {result_file}")
            continue

        with open(result_file, 'r') as f:
            data = json.load(f)

        # Reconstruct metrics object
        metrics = DetailedStrategyMetrics(**data)
        results.append(metrics)

    if not results:
        logger.error("No valid result files found")
        return

    logger.info(f"Comparing {len(results)} strategy results")

    # Generate comparison
    output_file = os.path.join(args.output_dir, f"comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    comparison = compare_strategies(results, output_file)

    logger.info(f"\nComparison complete! Report saved to {output_file}")


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Strategy Testing CLI - Test and optimize trading strategies',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test momentum strategy for 60 minutes
  python test_strategy.py test momentum --duration 60

  # Test with custom parameters
  python test_strategy.py test momentum --duration 30 --entry-threshold 0.7 --stop-loss 0.3

  # Test specific symbols
  python test_strategy.py test news --symbols AAPL,MSFT,GOOGL --duration 30

  # Test all strategies
  python test_strategy.py test-all --duration 30

  # Optimize strategy parameters
  python test_strategy.py optimize momentum --duration 30

  # Compare multiple results
  python test_strategy.py compare results/momentum_*.json
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Test command
    test_parser = subparsers.add_parser('test', help='Test a single strategy')
    test_parser.add_argument('strategy', help='Strategy to test (momentum, mean_reversion, etc.)')
    test_parser.add_argument('--duration', type=int, default=60, help='Test duration in minutes (default: 60)')
    test_parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    test_parser.add_argument('--entry-threshold', type=float, default=0.5, help='Entry threshold (default: 0.5)')
    test_parser.add_argument('--exit-threshold', type=float, default=0.3, help='Exit threshold (default: 0.3)')
    test_parser.add_argument('--stop-loss', type=float, default=0.5, help='Stop loss %% (default: 0.5)')
    test_parser.add_argument('--take-profit', type=float, default=2.0, help='Take profit %% (default: 2.0)')
    test_parser.add_argument('--max-hold', type=int, default=240, help='Max hold time in minutes (default: 240)')
    test_parser.add_argument('--position-size', type=float, default=2.0, help='Position size %% (default: 2.0)')
    test_parser.add_argument('--max-positions', type=int, default=5, help='Max concurrent positions (default: 5)')
    test_parser.add_argument('--capital', type=float, default=100000.0, help='Starting capital (default: 100000)')
    test_parser.add_argument('--output-dir', type=str, default='./test_results', help='Output directory')
    test_parser.set_defaults(func=test_single_strategy)

    # Test all command
    test_all_parser = subparsers.add_parser('test-all', help='Test all strategies')
    test_all_parser.add_argument('--duration', type=int, default=30, help='Test duration in minutes (default: 30)')
    test_all_parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    test_all_parser.add_argument('--include-crypto', action='store_true', help='Include crypto strategy')
    test_all_parser.add_argument('--entry-threshold', type=float, default=0.5, help='Entry threshold (default: 0.5)')
    test_all_parser.add_argument('--exit-threshold', type=float, default=0.3, help='Exit threshold (default: 0.3)')
    test_all_parser.add_argument('--stop-loss', type=float, default=0.5, help='Stop loss %% (default: 0.5)')
    test_all_parser.add_argument('--take-profit', type=float, default=2.0, help='Take profit %% (default: 2.0)')
    test_all_parser.add_argument('--max-hold', type=int, default=240, help='Max hold time in minutes (default: 240)')
    test_all_parser.add_argument('--position-size', type=float, default=2.0, help='Position size %% (default: 2.0)')
    test_all_parser.add_argument('--max-positions', type=int, default=5, help='Max concurrent positions (default: 5)')
    test_all_parser.add_argument('--capital', type=float, default=100000.0, help='Starting capital (default: 100000)')
    test_all_parser.add_argument('--output-dir', type=str, default='./test_results', help='Output directory')
    test_all_parser.set_defaults(func=test_all_strategies)

    # Optimize command
    optimize_parser = subparsers.add_parser('optimize', help='Optimize strategy parameters')
    optimize_parser.add_argument('strategy', help='Strategy to optimize')
    optimize_parser.add_argument('--duration', type=int, default=30, help='Test duration per combination (default: 30)')
    optimize_parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    optimize_parser.add_argument('--quick', action='store_true', help='Quick optimization (reduced parameter space)')
    optimize_parser.add_argument('--top-n', type=int, default=10, help='Number of top results to show (default: 10)')
    optimize_parser.add_argument('--capital', type=float, default=100000.0, help='Starting capital (default: 100000)')
    optimize_parser.add_argument('--output-dir', type=str, default='./optimization_results', help='Output directory')
    optimize_parser.set_defaults(func=optimize_strategy)

    # Compare command
    compare_parser = subparsers.add_parser('compare', help='Compare multiple test results')
    compare_parser.add_argument('result_files', nargs='+', help='Result JSON files to compare')
    compare_parser.add_argument('--output-dir', type=str, default='./test_results', help='Output directory')
    compare_parser.set_defaults(func=compare_results)

    # Parse args
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run command
    args.func(args)


if __name__ == '__main__':
    main()
