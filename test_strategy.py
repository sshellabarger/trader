#!/usr/bin/env python3
"""
Strategy Testing CLI

Command-line tool for testing trading strategies individually with
comprehensive metrics and optimization capabilities.

Each strategy has its own optimal parameters (entry/exit thresholds, stop loss,
take profit, duration). These are used automatically unless overridden via CLI.

Usage:
    # Test single strategy with its optimal parameters
    python test_strategy.py test momentum

    # Test with custom parameters (overrides defaults)
    python test_strategy.py test momentum --duration 60 --entry-threshold 0.7

    # Test all strategies, each with their own optimal parameters
    python test_strategy.py test-all

    # Override parameters for all strategies in test-all
    python test_strategy.py test-all --duration 30 --entry-threshold 0.6

    # Show all strategy configurations
    python test_strategy.py show-configs

    # Optimize strategy parameters
    python test_strategy.py optimize momentum --duration 30

    # Compare multiple results
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
from trading_bot.strategy_configs import get_strategy_config, print_all_configs
from trading_bot.broker_alpaca import AlpacaBroker
from trading_bot.news import get_news_for_symbols
from trading_bot.earnings import fetch_earnings_calendar
from trading_bot.universe import load_universe

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_test_symbols(args) -> List[str]:
    """
    Load symbols from various sources based on args

    Priority:
    1. --symbols-file (CSV or text file)
    2. --symbols (comma-separated list)
    3. --use-watchlist (main bot's universe)
    4. Default list
    """
    # Option 1: Load from file
    if hasattr(args, 'symbols_file') and args.symbols_file:
        logger.info(f"Loading symbols from file: {args.symbols_file}")
        try:
            with open(args.symbols_file, 'r') as f:
                content = f.read().strip()

            # Handle CSV format (with or without header)
            symbols = []
            for line in content.split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # Skip common CSV headers
                if line.upper() in ['SYMBOL', 'TICKER', 'SYMBOLS', 'TICKERS']:
                    continue

                # Split by comma and clean
                parts = [s.strip().upper() for s in line.split(',')]
                symbols.extend([s for s in parts if s and not s.startswith('#')])

            if symbols:
                logger.info(f"Loaded {len(symbols)} symbols from file: {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}")
                return symbols
            else:
                logger.warning(f"No valid symbols found in {args.symbols_file}, using defaults")

        except FileNotFoundError:
            logger.error(f"Symbols file not found: {args.symbols_file}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error reading symbols file: {e}")
            sys.exit(1)

    # Option 2: Load from --use-watchlist (main bot's universe)
    if hasattr(args, 'use_watchlist') and args.use_watchlist:
        logger.info("Loading symbols from main bot's watchlist")
        try:
            # Load using the same function as the main bot
            symbols = load_universe()

            # Separate stocks and crypto
            stock_symbols = [s for s in symbols if '/' not in s and not s.endswith('USD')]
            crypto_symbols = [s for s in symbols if '/' in s or s.endswith('USD')]

            # Use stocks by default, unless --include-crypto is set
            if hasattr(args, 'include_crypto') and args.include_crypto and crypto_symbols:
                symbols = stock_symbols + crypto_symbols
                logger.info(f"Loaded {len(stock_symbols)} stocks and {len(crypto_symbols)} crypto from watchlist")
            else:
                symbols = stock_symbols
                logger.info(f"Loaded {len(stock_symbols)} stocks from watchlist")

            if symbols:
                logger.info(f"Using symbols: {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}")
                return symbols
            else:
                logger.warning("Watchlist is empty, using defaults")

        except Exception as e:
            logger.error(f"Error loading watchlist: {e}")
            logger.info("Falling back to defaults")

    # Option 3: Command-line symbols
    if hasattr(args, 'symbols') and args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',')]
        logger.info(f"Using {len(symbols)} symbols from command line: {', '.join(symbols)}")
        return symbols

    # Option 4: Default symbols
    default_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX']
    logger.info(f"Using default symbols: {', '.join(default_symbols)}")
    return default_symbols


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

    # Load test symbols using the new helper
    test_symbols = load_test_symbols(args)

    # Get strategy-specific defaults
    strategy_defaults = get_strategy_config(strategy_type)

    # Use command-line args if provided, otherwise use strategy-specific defaults
    duration = args.duration if hasattr(args, 'duration') and args.duration is not None else strategy_defaults['default_duration']
    entry_threshold = args.entry_threshold if hasattr(args, 'entry_threshold') and args.entry_threshold is not None else strategy_defaults['entry_threshold']
    exit_threshold = args.exit_threshold if hasattr(args, 'exit_threshold') and args.exit_threshold is not None else strategy_defaults['exit_threshold']
    stop_loss = args.stop_loss if hasattr(args, 'stop_loss') and args.stop_loss is not None else strategy_defaults['stop_loss_pct']
    take_profit = args.take_profit if hasattr(args, 'take_profit') and args.take_profit is not None else strategy_defaults['take_profit_pct']
    max_hold = args.max_hold if hasattr(args, 'max_hold') and args.max_hold is not None else strategy_defaults['max_hold_minutes']
    position_size = args.position_size if hasattr(args, 'position_size') and args.position_size is not None else strategy_defaults['position_size_pct']

    logger.info(f"Using parameters for {strategy_type.value}:")
    logger.info(f"  Duration: {duration} min, Entry: {entry_threshold}, Exit: {exit_threshold}")
    logger.info(f"  Stop Loss: {stop_loss}%, Take Profit: {take_profit}%, Max Hold: {max_hold} min")

    # Create config
    config = StrategyTestConfig(
        strategy=strategy_type,
        mode='live',
        live_duration_minutes=duration,
        entry_threshold=entry_threshold,
        exit_threshold=exit_threshold,
        stop_loss_pct=stop_loss,
        take_profit_pct=take_profit,
        max_hold_minutes=max_hold,
        position_size_pct=position_size,
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
    """Test all strategies with their own optimal parameters"""
    logger.info("Testing all strategies with strategy-specific parameters")

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

    # Load test symbols using the new helper
    test_symbols = load_test_symbols(args)

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


def show_configs(args):
    """Show strategy configurations"""
    print_all_configs()


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Strategy Testing CLI - Test and optimize trading strategies',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show all strategy configurations
  python test_strategy.py show-configs

  # Test momentum strategy with its optimal parameters
  python test_strategy.py test momentum

  # Test with custom parameters (overrides defaults)
  python test_strategy.py test momentum --duration 60 --entry-threshold 0.7

  # Test specific symbols
  python test_strategy.py test news --symbols AAPL,MSFT,GOOGL

  # Test all strategies with their individual optimal parameters
  python test_strategy.py test-all

  # Test all strategies with overridden duration
  python test_strategy.py test-all --duration 30

  # Optimize strategy parameters
  python test_strategy.py optimize momentum --duration 30

  # Compare multiple results
  python test_strategy.py compare results/momentum_*.json

Note: Each strategy has different optimal parameters. Use show-configs to see them.
      Command-line arguments override the per-strategy defaults.
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # Test command
    test_parser = subparsers.add_parser('test', help='Test a single strategy')
    test_parser.add_argument('strategy', help='Strategy to test (momentum, mean_reversion, etc.)')
    test_parser.add_argument('--duration', type=int, default=None, help='Test duration in minutes (default: strategy-specific)')
    test_parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    test_parser.add_argument('--symbols-file', type=str, help='Path to file with symbols (one per line or CSV)')
    test_parser.add_argument('--use-watchlist', action='store_true', help='Use main bot\'s watchlist (from DAYTRADER_UNIVERSE)')
    test_parser.add_argument('--entry-threshold', type=float, default=None, help='Entry threshold (default: strategy-specific)')
    test_parser.add_argument('--exit-threshold', type=float, default=None, help='Exit threshold (default: strategy-specific)')
    test_parser.add_argument('--stop-loss', type=float, default=None, help='Stop loss %% (default: strategy-specific)')
    test_parser.add_argument('--take-profit', type=float, default=None, help='Take profit %% (default: strategy-specific)')
    test_parser.add_argument('--max-hold', type=int, default=None, help='Max hold time in minutes (default: strategy-specific)')
    test_parser.add_argument('--position-size', type=float, default=None, help='Position size %% (default: strategy-specific)')
    test_parser.add_argument('--max-positions', type=int, default=5, help='Max concurrent positions (default: 5)')
    test_parser.add_argument('--capital', type=float, default=100000.0, help='Starting capital (default: 100000)')
    test_parser.add_argument('--output-dir', type=str, default='./test_results', help='Output directory')
    test_parser.set_defaults(func=test_single_strategy)

    # Test all command
    test_all_parser = subparsers.add_parser('test-all', help='Test all strategies with their optimal parameters')
    test_all_parser.add_argument('--duration', type=int, default=None, help='Override duration for all strategies (default: per-strategy)')
    test_all_parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    test_all_parser.add_argument('--symbols-file', type=str, help='Path to file with symbols (one per line or CSV)')
    test_all_parser.add_argument('--use-watchlist', action='store_true', help='Use main bot\'s watchlist (from DAYTRADER_UNIVERSE)')
    test_all_parser.add_argument('--include-crypto', action='store_true', help='Include crypto strategy')
    test_all_parser.add_argument('--entry-threshold', type=float, default=None, help='Override entry threshold for all (default: per-strategy)')
    test_all_parser.add_argument('--exit-threshold', type=float, default=None, help='Override exit threshold for all (default: per-strategy)')
    test_all_parser.add_argument('--stop-loss', type=float, default=None, help='Override stop loss %% for all (default: per-strategy)')
    test_all_parser.add_argument('--take-profit', type=float, default=None, help='Override take profit %% for all (default: per-strategy)')
    test_all_parser.add_argument('--max-hold', type=int, default=None, help='Override max hold time for all (default: per-strategy)')
    test_all_parser.add_argument('--position-size', type=float, default=None, help='Override position size %% for all (default: per-strategy)')
    test_all_parser.add_argument('--max-positions', type=int, default=5, help='Max concurrent positions (default: 5)')
    test_all_parser.add_argument('--capital', type=float, default=100000.0, help='Starting capital (default: 100000)')
    test_all_parser.add_argument('--output-dir', type=str, default='./test_results', help='Output directory')
    test_all_parser.set_defaults(func=test_all_strategies)

    # Optimize command
    optimize_parser = subparsers.add_parser('optimize', help='Optimize strategy parameters')
    optimize_parser.add_argument('strategy', help='Strategy to optimize')
    optimize_parser.add_argument('--duration', type=int, default=30, help='Test duration per combination (default: 30)')
    optimize_parser.add_argument('--symbols', type=str, help='Comma-separated list of symbols to test')
    optimize_parser.add_argument('--symbols-file', type=str, help='Path to file with symbols (one per line or CSV)')
    optimize_parser.add_argument('--use-watchlist', action='store_true', help='Use main bot\'s watchlist (from DAYTRADER_UNIVERSE)')
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

    # Show configs command
    config_parser = subparsers.add_parser('show-configs', help='Show strategy-specific configurations')
    config_parser.set_defaults(func=show_configs)

    # Parse args
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run command
    args.func(args)


if __name__ == '__main__':
    main()
