#!/usr/bin/env python3
"""
Concurrent Batch Strategy Testing

Run all trading strategies simultaneously with independent results.
Each strategy runs as a separate live test with real-time market data.

Usage:
    # Test all strategies for 60 minutes each
    python batch_test_strategies.py

    # Test all strategies for custom duration
    python batch_test_strategies.py --duration 90

    # Test specific strategies only
    python batch_test_strategies.py --strategies momentum,news,volume

    # Use custom symbols
    python batch_test_strategies.py --symbols AAPL,MSFT,GOOGL,TSLA

    # Use symbols from file
    python batch_test_strategies.py --symbols-file examples/symbols.csv

    # Include crypto, forex, and ETF strategies
    python batch_test_strategies.py --include-all-asset-classes
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_bot.strategy_testing import (
    StrategyType, StrategyTestConfig, StrategyBacktester,
    DetailedStrategyMetrics, compare_strategies
)
from trading_bot.strategy_configs import get_strategy_config
from trading_bot.broker_alpaca import AlpacaBroker
from trading_bot.news import get_news_for_symbols
from trading_bot.earnings import fetch_earnings_calendar

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StrategyTestResult:
    """Container for strategy test result"""
    def __init__(self, strategy: StrategyType, metrics: Optional[DetailedStrategyMetrics] = None,
                 error: Optional[str] = None):
        self.strategy = strategy
        self.metrics = metrics
        self.error = error
        self.success = metrics is not None


def load_test_symbols(args) -> List[str]:
    """Load symbols from various sources"""
    # Option 1: Load from file
    if args.symbols_file:
        logger.info(f"Loading symbols from file: {args.symbols_file}")
        try:
            with open(args.symbols_file, 'r') as f:
                content = f.read().strip()

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
                logger.info(f"Loaded {len(symbols)} symbols from file")
                return symbols
            else:
                logger.warning(f"No valid symbols found in {args.symbols_file}, using defaults")

        except FileNotFoundError:
            logger.error(f"Symbols file not found: {args.symbols_file}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error reading symbols file: {e}")
            sys.exit(1)

    # Option 2: Command-line symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',')]
        logger.info(f"Using {len(symbols)} symbols from command line: {', '.join(symbols)}")
        return symbols

    # Option 3: Default symbols (diverse set for testing)
    default_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX',
                       'JPM', 'BAC', 'XOM', 'CVX', 'JNJ', 'PFE', 'WMT', 'HD']
    logger.info(f"Using default symbols ({len(default_symbols)} stocks)")
    return default_symbols


def test_strategy_worker(
    strategy: StrategyType,
    config: StrategyTestConfig,
    test_symbols: List[str],
    output_dir: str,
    news_data: Optional[List] = None,
    earnings_calendar: Optional[Dict] = None
) -> StrategyTestResult:
    """
    Worker function to test a single strategy
    Runs in a separate thread for concurrent execution
    """
    thread_name = threading.current_thread().name
    logger.info(f"[{thread_name}] Starting {strategy.value} test...")

    try:
        # Create broker instance for this thread
        broker = AlpacaBroker()

        # Create backtester
        backtester = StrategyBacktester(config, broker)

        # Run test
        metrics = backtester.run_live_test(
            news_data=news_data,
            earnings_calendar=earnings_calendar
        )

        # Save results
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Save metrics
        output_file = os.path.join(
            output_dir,
            f"{strategy.value}_test_{timestamp}.json"
        )
        metrics.to_json(output_file)
        logger.info(f"[{thread_name}] {strategy.value} results saved to {output_file}")

        # Save trades
        if config.save_trades and backtester.trades:
            trades_file = os.path.join(
                output_dir,
                f"{strategy.value}_trades_{timestamp}.json"
            )
            with open(trades_file, 'w') as f:
                json.dump([t.to_dict() for t in backtester.trades], f, indent=2)
            logger.info(f"[{thread_name}] {strategy.value} trades saved to {trades_file}")

        # Save signals
        if config.save_signals and backtester.signals:
            signals_file = os.path.join(
                output_dir,
                f"{strategy.value}_signals_{timestamp}.json"
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
            logger.info(f"[{thread_name}] {strategy.value} signals saved to {signals_file}")

        logger.info(f"[{thread_name}] {strategy.value} test completed successfully!")
        logger.info(f"[{thread_name}] {strategy.value} - Total trades: {metrics.total_trades}, "
                   f"Win rate: {metrics.win_rate:.1%}, Total P&L: ${metrics.total_pnl:.2f}")

        return StrategyTestResult(strategy, metrics=metrics)

    except Exception as e:
        logger.error(f"[{thread_name}] Error testing {strategy.value}: {e}", exc_info=True)
        return StrategyTestResult(strategy, error=str(e))


def run_batch_test(args) -> List[StrategyTestResult]:
    """
    Run all selected strategies concurrently
    Returns list of results for all strategies
    """
    # Determine which strategies to test
    if args.strategies:
        # Parse comma-separated list
        strategy_names = [s.strip().lower() for s in args.strategies.split(',')]
        strategies = []
        for name in strategy_names:
            try:
                strategy = StrategyType[name.upper()]
                strategies.append(strategy)
            except KeyError:
                logger.error(f"Invalid strategy: {name}")
                logger.error(f"Valid strategies: {', '.join([s.value for s in StrategyType])}")
                sys.exit(1)
    else:
        # Default: test all common strategies
        strategies = [
            StrategyType.MOMENTUM,
            StrategyType.MEAN_REVERSION,
            StrategyType.NEWS,
            StrategyType.VOLUME,
            StrategyType.EARNINGS,
            StrategyType.LONGTERM_TREND,
            StrategyType.LONGTERM_MOMENTUM,
        ]

        # Add additional asset classes if requested
        if args.include_all_asset_classes:
            strategies.extend([
                StrategyType.CRYPTO,
                StrategyType.FOREX,
                StrategyType.ETF,
            ])

    logger.info(f"Testing {len(strategies)} strategies: {', '.join([s.value for s in strategies])}")

    # Load test symbols
    test_symbols = load_test_symbols(args)

    # Pre-fetch shared data (news and earnings)
    logger.info("Pre-fetching news and earnings data (shared across strategies)...")
    news_data = None
    earnings_calendar = None

    try:
        logger.info("Fetching news data...")
        news_data = get_news_for_symbols(test_symbols, hours=6)
        logger.info(f"Fetched {len(news_data)} news articles")
    except Exception as e:
        logger.warning(f"Error fetching news data: {e}")

    try:
        logger.info("Fetching earnings calendar...")
        earnings_calendar = fetch_earnings_calendar()
        logger.info(f"Fetched earnings for {len(earnings_calendar)} symbols")
    except Exception as e:
        logger.warning(f"Error fetching earnings data: {e}")

    # Create configurations for all strategies
    configs = {}
    for strategy in strategies:
        # Get strategy-specific defaults
        strategy_defaults = get_strategy_config(strategy)

        # Use CLI overrides if provided, otherwise use strategy defaults
        duration = args.duration if args.duration is not None else strategy_defaults['default_duration']
        entry_threshold = args.entry_threshold if args.entry_threshold is not None else strategy_defaults['entry_threshold']
        exit_threshold = args.exit_threshold if args.exit_threshold is not None else strategy_defaults['exit_threshold']
        stop_loss = args.stop_loss if args.stop_loss is not None else strategy_defaults['stop_loss_pct']
        take_profit = args.take_profit if args.take_profit is not None else strategy_defaults['take_profit_pct']
        max_hold = args.max_hold if args.max_hold is not None else strategy_defaults['max_hold_minutes']
        position_size = args.position_size if args.position_size is not None else strategy_defaults['position_size_pct']

        config = StrategyTestConfig(
            strategy=strategy,
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
            output_dir=args.output_dir,
            save_trades=True,
            save_signals=True
        )

        configs[strategy] = config

        logger.info(f"{strategy.value}: duration={duration}min, entry={entry_threshold}, "
                   f"stop_loss={stop_loss}%, take_profit={take_profit}%")

    # Run tests concurrently
    logger.info(f"\n{'='*80}")
    logger.info(f"STARTING CONCURRENT BATCH TEST FOR {len(strategies)} STRATEGIES")
    logger.info(f"{'='*80}\n")

    results = []

    # Use ThreadPoolExecutor to run strategies in parallel
    # Each strategy will monitor the market independently
    with ThreadPoolExecutor(max_workers=len(strategies), thread_name_prefix="Strategy") as executor:
        # Submit all strategy tests
        future_to_strategy = {
            executor.submit(
                test_strategy_worker,
                strategy,
                configs[strategy],
                test_symbols,
                args.output_dir,
                news_data,
                earnings_calendar
            ): strategy
            for strategy in strategies
        }

        # Collect results as they complete
        for future in as_completed(future_to_strategy):
            strategy = future_to_strategy[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.error(f"Exception in {strategy.value} test: {e}")
                results.append(StrategyTestResult(strategy, error=str(e)))

    return results


def print_batch_summary(results: List[StrategyTestResult], output_dir: str):
    """Print comprehensive summary of all strategy results"""

    logger.info(f"\n{'='*80}")
    logger.info("BATCH TEST SUMMARY")
    logger.info(f"{'='*80}\n")

    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    logger.info(f"Total Strategies Tested: {len(results)}")
    logger.info(f"Successful: {len(successful)}")
    logger.info(f"Failed: {len(failed)}")

    if failed:
        logger.info("\nFailed Strategies:")
        for result in failed:
            logger.info(f"  - {result.strategy.value}: {result.error}")

    if successful:
        logger.info(f"\n{'='*80}")
        logger.info("INDIVIDUAL STRATEGY RESULTS")
        logger.info(f"{'='*80}\n")

        # Sort by total P&L
        successful.sort(key=lambda r: r.metrics.total_pnl, reverse=True)

        for result in successful:
            m = result.metrics
            logger.info(f"\n{result.strategy.value.upper()}")
            logger.info(f"  Trades: {m.total_trades} | Win Rate: {m.win_rate:.1%} | P&L: ${m.total_pnl:.2f}")
            logger.info(f"  Profit Factor: {m.profit_factor:.2f} | Sharpe: {m.sharpe_ratio:.2f} | Max DD: {m.max_drawdown_pct:.2%}")
            logger.info(f"  Avg Win: ${m.avg_win:.2f} | Avg Loss: ${m.avg_loss:.2f}")

        # Create comparison report
        logger.info(f"\n{'='*80}")
        logger.info("STRATEGY COMPARISON")
        logger.info(f"{'='*80}\n")

        metrics_list = [r.metrics for r in successful]
        comparison = compare_strategies(metrics_list)

        # Save comparison report
        comparison_file = os.path.join(output_dir, f"batch_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(comparison_file, 'w') as f:
            json.dump(comparison, f, indent=2)

        logger.info(f"Comparison report saved to {comparison_file}")

        # Print rankings
        logger.info("\nSTRATEGY RANKINGS:")

        if 'Total P&L' in comparison['rankings']:
            logger.info("\nBy Total P&L:")
            for i, rank in enumerate(comparison['rankings']['Total P&L'][:5], 1):
                logger.info(f"  {i}. {rank['strategy']}: ${rank['value']:.2f}")

        if 'Win Rate' in comparison['rankings']:
            logger.info("\nBy Win Rate:")
            for i, rank in enumerate(comparison['rankings']['Win Rate'][:5], 1):
                logger.info(f"  {i}. {rank['strategy']}: {rank['value']:.1f}%")

        if 'Sharpe Ratio' in comparison['rankings']:
            logger.info("\nBy Sharpe Ratio:")
            for i, rank in enumerate(comparison['rankings']['Sharpe Ratio'][:5], 1):
                logger.info(f"  {i}. {rank['strategy']}: {rank['value']:.2f}")

        # Print recommendations
        if 'recommendations' in comparison:
            logger.info(f"\n{'='*80}")
            logger.info("RECOMMENDATIONS")
            logger.info(f"{'='*80}\n")

            for strategy, recs in comparison['recommendations'].items():
                if recs:
                    logger.info(f"\n{strategy}:")
                    for rec in recs:
                        logger.info(f"  • {rec}")

    logger.info(f"\n{'='*80}")
    logger.info(f"All results saved to: {output_dir}")
    logger.info(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Run concurrent batch tests for all trading strategies'
    )

    # Strategy selection
    parser.add_argument(
        '--strategies',
        type=str,
        help='Comma-separated list of strategies to test (e.g., "momentum,news,volume")'
    )
    parser.add_argument(
        '--include-all-asset-classes',
        action='store_true',
        help='Include crypto, forex, and ETF strategies'
    )

    # Symbol selection
    parser.add_argument(
        '--symbols',
        type=str,
        help='Comma-separated list of symbols to test'
    )
    parser.add_argument(
        '--symbols-file',
        type=str,
        help='Path to file containing symbols (CSV or text)'
    )

    # Test parameters
    parser.add_argument(
        '--duration',
        type=int,
        help='Test duration in minutes (default: strategy-specific optimal)'
    )
    parser.add_argument(
        '--capital',
        type=float,
        default=100000.0,
        help='Starting capital (default: 100000)'
    )

    # Strategy parameters (optional overrides)
    parser.add_argument('--entry-threshold', type=float, help='Entry threshold (0-1)')
    parser.add_argument('--exit-threshold', type=float, help='Exit threshold (0-1)')
    parser.add_argument('--stop-loss', type=float, help='Stop loss percentage')
    parser.add_argument('--take-profit', type=float, help='Take profit percentage')
    parser.add_argument('--max-hold', type=int, help='Max hold time in minutes')
    parser.add_argument('--position-size', type=float, help='Position size percentage')
    parser.add_argument('--max-positions', type=int, default=5, help='Max concurrent positions')

    # Output
    parser.add_argument(
        '--output-dir',
        type=str,
        default='test_results',
        help='Output directory for results (default: test_results)'
    )

    args = parser.parse_args()

    try:
        # Run batch test
        results = run_batch_test(args)

        # Print summary
        print_batch_summary(results, args.output_dir)

        # Exit with appropriate code
        if any(not r.success for r in results):
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        logger.info("\nBatch test interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Fatal error in batch test: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
