"""
CLI — command-line entry point for DayTrader v2.

Usage:
  python -m daytrader run              # start live/paper trading
  python -m daytrader run --dry-run    # signals only, no orders
  python -m daytrader backtest         # run backtest
  python -m daytrader backtest --start 2025-01-02 --end 2025-01-31
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Config
from .engine import Engine
from .backtest import Backtester
from .universe import get_universe, get_universe_stats

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO", log_file: str = ""):
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)


def cmd_run(args):
    config = Config()
    config.dry_run = args.dry_run
    config.log_level = args.log_level
    setup_logging(config.log_level, config.log_file)

    if args.symbols:
        universe = args.symbols.split(",")
    elif args.categories:
        universe = get_universe(args.categories.split(","))
    else:
        universe = get_universe()

    logger.info(f"Universe: {len(universe)} symbols")
    engine = Engine(config, universe=universe)
    engine.run(loop_interval=args.interval)


def cmd_backtest(args):
    config = Config()
    config.log_level = args.log_level
    setup_logging(config.log_level)

    if args.symbols:
        symbols = args.symbols.split(",")
    elif args.categories:
        symbols = get_universe(args.categories.split(","))
    else:
        # Default to most active stocks for backtest (not entire universe)
        symbols = get_universe(["mega_cap", "tech_volatile"])[:30]

    bt = Backtester(config)
    result = bt.run(symbols, start_date=args.start, end_date=args.end)

    print(f"\n{'='*50}")
    print(f"Total return: {result.total_return_pct:+.2f}%  (${result.total_pnl:+,.2f})")
    print(f"Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%")
    print(f"Profit factor: {result.profit_factor:.2f}  Sharpe: {result.sharpe_ratio:.2f}")
    print(f"Max drawdown: {result.max_drawdown_pct:.2f}%")
    print(f"Avg hold: {result.avg_hold_minutes:.0f} min  Trades/day: {result.trades_per_day:.1f}")
    for strat, stats in result.by_strategy.items():
        print(f"  [{strat}] {stats['trades']} trades, {stats['win_rate']:.0f}% win, ${stats['pnl']:+,.2f}")


def main():
    parser = argparse.ArgumentParser(description="DayTrader v2")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    sub = parser.add_subparsers(dest="command")

    # Run
    run_parser = sub.add_parser("run", help="Start live/paper trading")
    run_parser.add_argument("--dry-run", action="store_true", help="Signal-only mode")
    run_parser.add_argument("--interval", type=int, default=30, help="Loop interval seconds")
    run_parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    run_parser.add_argument("--categories", type=str, default="",
                            help="Comma-separated universe categories (e.g. mega_cap,tech_volatile)")

    # Backtest
    bt_parser = sub.add_parser("backtest", help="Run backtest")
    bt_parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    bt_parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    bt_parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols")
    bt_parser.add_argument("--categories", type=str, default="",
                            help="Comma-separated universe categories")

    # Universe info
    sub.add_parser("universe", help="Show universe statistics")

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "universe":
        cmd_universe()
    else:
        parser.print_help()


def cmd_universe():
    """Print universe breakdown."""
    stats = get_universe_stats()
    print(f"\nDayTrader v2 Universe: {stats['total_symbols']} total symbols")
    print(f"  Stocks: {stats['stocks_only']}  |  ETFs: {stats['etfs_only']}\n")

    from .universe import UNIVERSE
    for cat, syms in UNIVERSE.items():
        count = len(syms)
        preview = ", ".join(syms[:8])
        more = f" ... +{count - 8} more" if count > 8 else ""
        print(f"  {cat:20s} ({count:3d}):  {preview}{more}")

    print(f"\nUsage:")
    print(f"  python -m daytrader run --categories mega_cap,tech_volatile")
    print(f"  python -m daytrader run --symbols AAPL,TSLA,NVDA")
    print(f"  python -m daytrader run                 (uses full universe)")


if __name__ == "__main__":
    main()
