"""
CLI — ETF-focused day trading.

Usage:
  python -m trader run              # paper trade TQQQ
  python -m trader run --dry-run    # signals only
  python -m trader backtest --start 2025-01-02 --end 2025-06-30
  python -m trader backtest --start 2025-01-02 --end 2025-06-30 --symbol TQQQ
  python -m trader walkforward --start 2025-01-02 --end 2025-06-30 --symbol TQQQ
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import Config
from .engine import Engine
from .backtest import Backtester
from .walkforward import WalkForwardValidator

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
    if args.symbol:
        config.strategy.primary_symbol = args.symbol
        config.strategy.use_leveraged = False
    setup_logging(config.log_level, config.log_file)

    engine = Engine(config)
    engine.run(loop_interval=args.interval)


def cmd_backtest(args):
    config = Config()
    config.log_level = args.log_level
    setup_logging(config.log_level)

    symbol = args.symbol or config.strategy.primary_symbol
    bt = Backtester(config)
    result = bt.run([symbol], start_date=args.start, end_date=args.end)

    print(f"\n{'='*50}")
    print(f"BACKTEST: {symbol} {args.start} -> {args.end}")
    print(f"  Return: {result.total_return_pct:+.2f}%  (${result.total_pnl:+,.2f})")
    print(f"  Capital: ${result.initial_capital:,.0f} -> ${result.final_capital:,.2f}")
    print(f"  Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%")
    print(f"  Avg win: ${result.avg_win:,.2f}  Avg loss: ${result.avg_loss:,.2f}")
    print(f"  Profit factor: {result.profit_factor:.2f}  Sharpe: {result.sharpe_ratio:.2f}")
    print(f"  Max drawdown: {result.max_drawdown_pct:.2f}%")
    for strat, stats in result.by_strategy.items():
        print(f"  [{strat}] {stats['trades']} trades, {stats['win_rate']:.0f}% win, ${stats['pnl']:+,.2f}")


def cmd_walkforward(args):
    config = Config()
    config.log_level = args.log_level
    setup_logging(config.log_level)

    symbol = args.symbol or config.strategy.primary_symbol

    wf = WalkForwardValidator(
        config,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )
    result = wf.run([symbol], start_date=args.start, end_date=args.end)

    print(f"\n{'='*60}")
    print(f"WALK-FORWARD: {symbol}")
    print(f"{'='*60}")
    print(f"  {'':24s} {'TRAIN':>10s} {'TEST':>10s}")
    print(f"  {'P&L':24s} ${result.total_train_pnl:>+9,.2f} ${result.total_test_pnl:>+9,.2f}")
    print(f"  {'Trades':24s} {result.total_train_trades:>10d} {result.total_test_trades:>10d}")
    print(f"  {'Win rate':24s} {result.train_win_rate:>9.1f}% {result.test_win_rate:>9.1f}%")
    print(f"  {'Profit factor':24s} {result.train_profit_factor:>10.2f} {result.test_profit_factor:>10.2f}")
    print()
    if result.consistent:
        print("  VERDICT: CONSISTENT")
    elif result.total_train_pnl > 0 and result.total_test_pnl <= 0:
        print("  VERDICT: OVERFITTING")
    elif result.total_train_pnl <= 0:
        print("  VERDICT: NO EDGE")
    else:
        print("  VERDICT: MIXED")


def main():
    parser = argparse.ArgumentParser(description="DayTrader v2 — ETF Edition")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Start trading")
    run_p.add_argument("--dry-run", action="store_true", help="Signal-only mode")
    run_p.add_argument("--interval", type=int, default=30, help="Loop interval seconds")
    run_p.add_argument("--symbol", type=str, default="", help="Override primary symbol")

    bt_p = sub.add_parser("backtest", help="Run backtest")
    bt_p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    bt_p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    bt_p.add_argument("--symbol", type=str, default="", help="Symbol to test")

    wf_p = sub.add_parser("walkforward", help="Walk-forward validation")
    wf_p.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    wf_p.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    wf_p.add_argument("--symbol", type=str, default="", help="Symbol")
    wf_p.add_argument("--train-days", type=int, default=40)
    wf_p.add_argument("--test-days", type=int, default=20)
    wf_p.add_argument("--step-days", type=int, default=20)

    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "walkforward":
        cmd_walkforward(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
