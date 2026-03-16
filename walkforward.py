"""
Walk-Forward Validation — test that strategy performance generalises.

Splits a date range into train/test windows and runs backtests on each.
If a strategy is profitable on train but not test, it's overfitting.
If profitable on both, the edge is likely real.

Usage:
  python -m trader walkforward --start 2025-01-02 --end 2025-06-30

This will run multiple train/test cycles:
  Train: Jan 02 – Feb 28 → Test: Mar 01 – Mar 31
  Train: Feb 01 – Mar 31 → Test: Apr 01 – Apr 30
  ... etc.

Results show whether performance holds on unseen data.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from .backtest import Backtester, BacktestResult
from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    """One train/test cycle."""
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_result: Optional[BacktestResult] = None
    test_result: Optional[BacktestResult] = None


@dataclass
class WalkForwardResult:
    """Aggregated results across all windows."""
    windows: List[WalkForwardWindow]
    total_train_pnl: float = 0
    total_test_pnl: float = 0
    total_train_trades: int = 0
    total_test_trades: int = 0
    train_win_rate: float = 0
    test_win_rate: float = 0
    train_profit_factor: float = 0
    test_profit_factor: float = 0
    consistent: bool = False  # both train AND test profitable?


class WalkForwardValidator:
    """
    Walk-forward backtester.

    Splits a date range into rolling train/test windows.
    Runs identical strategy logic on both — no parameter changes between them.
    Compares results to detect overfitting.
    """

    def __init__(
        self,
        config: Config,
        train_days: int = 40,  # ~2 months of trading days
        test_days: int = 20,   # ~1 month of trading days
        step_days: int = 20,   # slide forward by ~1 month
    ):
        self.config = config
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.backtester = Backtester(config)

    def run(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> WalkForwardResult:
        """Run walk-forward validation across the full date range."""
        windows = self._generate_windows(start_date, end_date)

        if not windows:
            logger.error("Date range too short for walk-forward validation")
            return WalkForwardResult(windows=[])

        logger.info(f"Walk-forward: {len(windows)} windows, "
                     f"{self.train_days}d train / {self.test_days}d test / {self.step_days}d step")

        for i, window in enumerate(windows):
            logger.info(f"\n{'='*60}")
            logger.info(f"WINDOW {i+1}/{len(windows)}: "
                         f"Train {window.train_start}→{window.train_end}  "
                         f"Test {window.test_start}→{window.test_end}")
            logger.info(f"{'='*60}")

            # Run train
            logger.info(f"\n--- TRAIN ---")
            window.train_result = self.backtester.run(
                symbols, window.train_start, window.train_end
            )

            # Run test (same config, no changes)
            logger.info(f"\n--- TEST ---")
            window.test_result = self.backtester.run(
                symbols, window.test_start, window.test_end
            )

        result = self._aggregate(windows)
        self._print_summary(result)
        self._save_results(result)

        return result

    # ------------------------------------------------------------------
    # Window generation
    # ------------------------------------------------------------------

    def _generate_windows(self, start_date: str, end_date: str) -> List[WalkForwardWindow]:
        """Generate rolling train/test windows across the date range."""
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()

        total_cal_days = (end - start).days
        window_cal_days = self.train_days + self.test_days  # approximate calendar days

        # Convert trading days to approximate calendar days (×1.4 for weekends)
        train_cal = int(self.train_days * 1.45)
        test_cal = int(self.test_days * 1.45)
        step_cal = int(self.step_days * 1.45)

        windows: List[WalkForwardWindow] = []
        cursor = start

        while True:
            train_start = cursor
            train_end = train_start + timedelta(days=train_cal)
            test_start = train_end + timedelta(days=1)
            test_end = test_start + timedelta(days=test_cal)

            if test_end > end:
                break

            windows.append(WalkForwardWindow(
                train_start=train_start.isoformat(),
                train_end=train_end.isoformat(),
                test_start=test_start.isoformat(),
                test_end=test_end.isoformat(),
            ))

            cursor += timedelta(days=step_cal)

        return windows

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate(self, windows: List[WalkForwardWindow]) -> WalkForwardResult:
        """Aggregate results across all windows."""
        total_train_pnl = 0
        total_test_pnl = 0
        total_train_trades = 0
        total_test_trades = 0
        total_train_wins = 0
        total_test_wins = 0
        train_gross_profit = 0
        train_gross_loss = 0
        test_gross_profit = 0
        test_gross_loss = 0

        for w in windows:
            if w.train_result:
                total_train_pnl += w.train_result.total_pnl
                total_train_trades += w.train_result.total_trades
                total_train_wins += w.train_result.winning_trades
                if w.train_result.avg_win and w.train_result.winning_trades:
                    train_gross_profit += w.train_result.avg_win * w.train_result.winning_trades
                if w.train_result.avg_loss and w.train_result.losing_trades:
                    train_gross_loss += abs(w.train_result.avg_loss) * w.train_result.losing_trades

            if w.test_result:
                total_test_pnl += w.test_result.total_pnl
                total_test_trades += w.test_result.total_trades
                total_test_wins += w.test_result.winning_trades
                if w.test_result.avg_win and w.test_result.winning_trades:
                    test_gross_profit += w.test_result.avg_win * w.test_result.winning_trades
                if w.test_result.avg_loss and w.test_result.losing_trades:
                    test_gross_loss += abs(w.test_result.avg_loss) * w.test_result.losing_trades

        train_wr = (total_train_wins / total_train_trades * 100) if total_train_trades else 0
        test_wr = (total_test_wins / total_test_trades * 100) if total_test_trades else 0

        train_pf = (train_gross_profit / train_gross_loss) if train_gross_loss > 0 else 0
        test_pf = (test_gross_profit / test_gross_loss) if test_gross_loss > 0 else 0

        consistent = total_train_pnl > 0 and total_test_pnl > 0

        return WalkForwardResult(
            windows=windows,
            total_train_pnl=round(total_train_pnl, 2),
            total_test_pnl=round(total_test_pnl, 2),
            total_train_trades=total_train_trades,
            total_test_trades=total_test_trades,
            train_win_rate=round(train_wr, 1),
            test_win_rate=round(test_wr, 1),
            train_profit_factor=round(train_pf, 2),
            test_profit_factor=round(test_pf, 2),
            consistent=consistent,
        )

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _print_summary(self, result: WalkForwardResult):
        logger.info(f"\n{'='*60}")
        logger.info(f"WALK-FORWARD SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"  Windows: {len(result.windows)}")
        logger.info(f"")
        logger.info(f"  {'':30s} {'TRAIN':>10s} {'TEST':>10s}")
        logger.info(f"  {'P&L':30s} ${result.total_train_pnl:>+9,.2f} ${result.total_test_pnl:>+9,.2f}")
        logger.info(f"  {'Trades':30s} {result.total_train_trades:>10d} {result.total_test_trades:>10d}")
        logger.info(f"  {'Win rate':30s} {result.train_win_rate:>9.1f}% {result.test_win_rate:>9.1f}%")
        logger.info(f"  {'Profit factor':30s} {result.train_profit_factor:>10.2f} {result.test_profit_factor:>10.2f}")
        logger.info(f"")

        if result.consistent:
            logger.info(f"  VERDICT: CONSISTENT — both train and test profitable")
        elif result.total_train_pnl > 0 and result.total_test_pnl <= 0:
            logger.info(f"  VERDICT: OVERFITTING — profitable on train, losing on test")
        elif result.total_train_pnl <= 0:
            logger.info(f"  VERDICT: NO EDGE — unprofitable even on train data")
        else:
            logger.info(f"  VERDICT: MIXED — needs investigation")

        logger.info(f"")
        for i, w in enumerate(result.windows):
            tr = w.train_result
            te = w.test_result
            tr_pnl = tr.total_pnl if tr else 0
            te_pnl = te.total_pnl if te else 0
            tr_wr = tr.win_rate if tr else 0
            te_wr = te.win_rate if te else 0
            emoji_tr = "+" if tr_pnl > 0 else "-"
            emoji_te = "+" if te_pnl > 0 else "-"
            logger.info(
                f"  Window {i+1}: Train {w.train_start}..{w.train_end} "
                f"[{emoji_tr}${abs(tr_pnl):,.0f} {tr_wr:.0f}%]  "
                f"Test {w.test_start}..{w.test_end} "
                f"[{emoji_te}${abs(te_pnl):,.0f} {te_wr:.0f}%]"
            )

    def _save_results(self, result: WalkForwardResult):
        """Save walk-forward results to files."""
        out_dir = "backtest_results"
        os.makedirs(out_dir, exist_ok=True)

        # Summary JSON
        summary = {
            "type": "walk_forward",
            "windows": len(result.windows),
            "total_train_pnl": result.total_train_pnl,
            "total_test_pnl": result.total_test_pnl,
            "total_train_trades": result.total_train_trades,
            "total_test_trades": result.total_test_trades,
            "train_win_rate": result.train_win_rate,
            "test_win_rate": result.test_win_rate,
            "train_profit_factor": result.train_profit_factor,
            "test_profit_factor": result.test_profit_factor,
            "consistent": result.consistent,
            "window_details": [],
        }

        for w in result.windows:
            detail = {
                "train": {"start": w.train_start, "end": w.train_end},
                "test": {"start": w.test_start, "end": w.test_end},
            }
            if w.train_result:
                detail["train"]["pnl"] = w.train_result.total_pnl
                detail["train"]["trades"] = w.train_result.total_trades
                detail["train"]["win_rate"] = w.train_result.win_rate
                detail["train"]["profit_factor"] = w.train_result.profit_factor
                detail["train"]["by_strategy"] = w.train_result.by_strategy
            if w.test_result:
                detail["test"]["pnl"] = w.test_result.total_pnl
                detail["test"]["trades"] = w.test_result.total_trades
                detail["test"]["win_rate"] = w.test_result.win_rate
                detail["test"]["profit_factor"] = w.test_result.profit_factor
                detail["test"]["by_strategy"] = w.test_result.by_strategy
            summary["window_details"].append(detail)

        filepath = os.path.join(out_dir, "walkforward_summary.json")
        with open(filepath, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"Walk-forward results saved: {filepath}")
