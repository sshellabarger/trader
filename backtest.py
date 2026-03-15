"""
Backtesting Framework — simulate strategies on historical data.

Uses the same strategy classes as live trading for consistency.
Simulates fills with configurable slippage and commission.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from .broker import AlpacaBroker
from .config import Config
from .indicators import compute_indicators
from .journal import TradeRecord
from .risk import RiskManager, PositionInfo
from .scanner import Candidate
from .strategies import BaseStrategy, Signal, SignalAction, SignalDirection
from .strategies.orb import ORBStrategy
from .strategies.vwap_reversion import VWAPReversionStrategy
from .strategies.gap_and_go import GapAndGoStrategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_pnl: float

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0

    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0

    avg_hold_minutes: float = 0.0
    trades_per_day: float = 0.0

    by_strategy: Dict[str, Dict] = field(default_factory=dict)
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)


class Backtester:
    """
    Walk-forward backtester using historical bars from Alpaca.
    Simulates one day at a time, bar by bar.
    """

    def __init__(self, config: Config):
        self.config = config
        self.broker = AlpacaBroker(config.broker)
        self.slippage_pct = config.backtest.slippage_bps / 10000.0
        self.commission = config.backtest.commission_per_share

    def run(
        self,
        symbols: List[str],
        start_date: str,
        end_date: str,
    ) -> BacktestResult:
        """
        Run backtest over a date range.
        Fetches daily 1-min bars and walks through them bar by bar.
        """
        capital = self.config.backtest.initial_capital
        risk = RiskManager(self.config.risk)

        # Initialize strategies
        strategies: List[BaseStrategy] = []
        if self.config.strategy.orb_enabled:
            strategies.append(ORBStrategy(self.config))
        if self.config.strategy.vwap_enabled:
            strategies.append(VWAPReversionStrategy(self.config))
        if self.config.strategy.gap_enabled:
            strategies.append(GapAndGoStrategy(self.config))

        all_trades: List[TradeRecord] = []
        equity_curve: List[Tuple[str, float]] = []
        open_positions: Dict[str, Dict] = {}  # symbol → {entry, stop, tp, qty, strategy, entry_time}

        # Walk through each day
        current = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        trading_days = 0

        logger.info(f"Backtest: {start_date} → {end_date}, {len(symbols)} symbols, "
                     f"capital=${capital:,.0f}")

        while current <= end:
            if current.weekday() >= 5:  # skip weekends
                current += timedelta(days=1)
                continue

            day_str = current.isoformat()

            # Fetch bars for all symbols for this day
            day_bars = self._fetch_day_bars(symbols, day_str)
            if not day_bars:
                current += timedelta(days=1)
                continue

            trading_days += 1
            risk.reset_daily(capital)
            day_start_capital = capital

            # Reset strategy state for new day
            for s in strategies:
                s._active_trades.clear()

            # Simulate bar-by-bar
            capital, day_trades = self._simulate_day(
                day_bars, strategies, risk, capital, open_positions
            )

            all_trades.extend(day_trades)
            equity_curve.append((day_str, capital))

            day_pnl = capital - day_start_capital
            if day_trades:
                logger.info(
                    f"  {day_str}: {len(day_trades)} trades, "
                    f"P&L=${day_pnl:+,.2f}, capital=${capital:,.2f}"
                )

            current += timedelta(days=1)

        # Compute result metrics
        result = self._compute_metrics(
            all_trades, equity_curve, self.config.backtest.initial_capital,
            capital, start_date, end_date, trading_days
        )
        result.trades = all_trades

        logger.info(f"\n{'='*50}")
        logger.info(f"BACKTEST RESULTS: {start_date} → {end_date}")
        logger.info(f"  Return: {result.total_return_pct:+.2f}%  P&L: ${result.total_pnl:+,.2f}")
        logger.info(f"  Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%")
        logger.info(f"  Sharpe: {result.sharpe_ratio:.2f}  Max DD: {result.max_drawdown_pct:.2f}%")
        logger.info(f"  Profit factor: {result.profit_factor:.2f}")
        for strat, stats in result.by_strategy.items():
            logger.info(f"  [{strat}] trades={stats['trades']} win={stats['win_rate']:.0f}% pnl=${stats['pnl']:+,.2f}")

        return result

    # ------------------------------------------------------------------

    def _fetch_day_bars(
        self, symbols: List[str], day_str: str
    ) -> Dict[str, List[Dict]]:
        """Fetch 1-min bars for all symbols for one day."""
        start = f"{day_str}T09:30:00-05:00"
        end = f"{day_str}T16:00:00-05:00"

        result: Dict[str, List[Dict]] = {}
        for sym in symbols:
            bars = self.broker.get_bars(sym, timeframe="1Min", start=start, end=end, limit=500)
            if bars and len(bars) >= 20:
                result[sym] = bars

        return result

    def _simulate_day(
        self,
        day_bars: Dict[str, List[Dict]],
        strategies: List[BaseStrategy],
        risk: RiskManager,
        capital: float,
        open_positions: Dict[str, Dict],
    ) -> Tuple[float, List[TradeRecord]]:
        """Simulate one trading day bar by bar."""
        trades: List[TradeRecord] = []

        # Determine max bars across all symbols
        max_bars = max(len(bars) for bars in day_bars.values()) if day_bars else 0

        for bar_idx in range(20, max_bars):  # start after warmup period

            # Check exits first
            for sym in list(open_positions.keys()):
                if sym not in day_bars or bar_idx >= len(day_bars[sym]):
                    continue
                bars_so_far = day_bars[sym][:bar_idx + 1]
                current_bar = bars_so_far[-1]
                current_price = float(current_bar["c"])
                current_low = float(current_bar["l"])

                pos = open_positions[sym]

                # Stop loss hit?
                if current_low <= pos["stop"]:
                    exit_price = pos["stop"] * (1 - self.slippage_pct)
                    pnl = (exit_price - pos["entry"]) * pos["qty"] - self.commission * pos["qty"] * 2
                    capital += pnl
                    record = TradeRecord(
                        symbol=sym, strategy=pos["strategy"], direction="long",
                        qty=pos["qty"], entry_time=pos["entry_time"],
                        entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                        stop_loss=pos["stop"], take_profit=pos["tp"],
                    )
                    record.close(exit_price, "stop_loss", current_bar.get("t", ""))
                    trades.append(record)
                    del open_positions[sym]
                    continue

                # Take profit hit?
                current_high = float(current_bar["h"])
                if current_high >= pos["tp"]:
                    exit_price = pos["tp"] * (1 - self.slippage_pct)
                    pnl = (exit_price - pos["entry"]) * pos["qty"] - self.commission * pos["qty"] * 2
                    capital += pnl
                    record = TradeRecord(
                        symbol=sym, strategy=pos["strategy"], direction="long",
                        qty=pos["qty"], entry_time=pos["entry_time"],
                        entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                        stop_loss=pos["stop"], take_profit=pos["tp"],
                    )
                    record.close(exit_price, "take_profit", current_bar.get("t", ""))
                    trades.append(record)
                    del open_positions[sym]
                    continue

                # Strategy-based exit
                indicators = compute_indicators(bars_so_far)
                candidate = Candidate(
                    symbol=sym, price=current_price, prev_close=pos["entry"],
                    gap_pct=0, change_pct=0, volume=0, avg_volume=1,
                    relative_volume=1, high=current_price, low=current_price,
                    open_price=pos["entry"],
                )
                mock_position = {"symbol": sym, "current_price": current_price,
                                 "avg_entry_price": pos["entry"]}

                for strategy in strategies:
                    if strategy.name != pos["strategy"]:
                        continue
                    signal = strategy.evaluate(candidate, bars_so_far, indicators, mock_position)
                    if signal and signal.action == SignalAction.EXIT:
                        exit_price = current_price * (1 - self.slippage_pct)
                        pnl = (exit_price - pos["entry"]) * pos["qty"] - self.commission * pos["qty"] * 2
                        capital += pnl
                        record = TradeRecord(
                            symbol=sym, strategy=pos["strategy"], direction="long",
                            qty=pos["qty"], entry_time=pos["entry_time"],
                            entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                            stop_loss=pos["stop"], take_profit=pos["tp"],
                        )
                        record.close(exit_price, signal.reason, current_bar.get("t", ""))
                        trades.append(record)
                        del open_positions[sym]
                        break

            # Check entries
            for sym, bars in day_bars.items():
                if sym in open_positions:
                    continue
                if bar_idx >= len(bars):
                    continue

                bars_so_far = bars[:bar_idx + 1]
                current_bar = bars_so_far[-1]
                current_price = float(current_bar["c"])
                indicators = compute_indicators(bars_so_far)

                # Build candidate from bars
                open_price = float(bars[0]["o"])
                prev_close = open_price  # approximation for backtest
                gap_pct = 0
                rvol = indicators.get("relative_volume", 1) or 1

                candidate = Candidate(
                    symbol=sym, price=current_price, prev_close=prev_close,
                    gap_pct=gap_pct,
                    change_pct=((current_price - open_price) / open_price * 100) if open_price > 0 else 0,
                    volume=float(current_bar["v"]),
                    avg_volume=float(bars[0]["v"]) if bars else 1,
                    relative_volume=rvol,
                    high=max(float(b["h"]) for b in bars_so_far),
                    low=min(float(b["l"]) for b in bars_so_far),
                    open_price=open_price,
                )

                for strategy in strategies:
                    signal = strategy.evaluate(candidate, bars_so_far, indicators, None)
                    if signal and signal.action == SignalAction.ENTER:
                        # Size position
                        positions_list = [
                            PositionInfo(s, 1, p["entry"], current_price,
                                         p["entry"] * p["qty"], 0, 0)
                            for s, p in open_positions.items()
                        ]
                        ok, reason = risk.validate_entry(
                            signal, capital, capital * 0.5, positions_list
                        )
                        if not ok:
                            continue

                        size = risk.calculate_position_size(
                            signal, capital, capital * 0.5, len(open_positions)
                        )
                        if size.shares < 1:
                            continue

                        entry_price = current_price * (1 + self.slippage_pct)
                        open_positions[sym] = {
                            "entry": entry_price,
                            "stop": signal.stop_loss,
                            "tp": signal.take_profit,
                            "qty": size.shares,
                            "strategy": strategy.name,
                            "entry_time": current_bar.get("t", ""),
                            "reason": signal.reason,
                        }
                        strategy.on_fill(sym, signal)
                        risk.record_trade()
                        break

        # End of day: close remaining positions
        for sym in list(open_positions.keys()):
            if sym not in day_bars:
                continue
            last_bar = day_bars[sym][-1]
            exit_price = float(last_bar["c"]) * (1 - self.slippage_pct)
            pos = open_positions[sym]
            pnl = (exit_price - pos["entry"]) * pos["qty"] - self.commission * pos["qty"] * 2
            capital += pnl
            record = TradeRecord(
                symbol=sym, strategy=pos["strategy"], direction="long",
                qty=pos["qty"], entry_time=pos["entry_time"],
                entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                stop_loss=pos["stop"], take_profit=pos["tp"],
            )
            record.close(exit_price, "eod_close", last_bar.get("t", ""))
            trades.append(record)

        open_positions.clear()
        return capital, trades

    # ------------------------------------------------------------------

    def _compute_metrics(
        self, trades: List[TradeRecord], equity_curve: List[Tuple[str, float]],
        initial_capital: float, final_capital: float,
        start_date: str, end_date: str, trading_days: int,
    ) -> BacktestResult:
        result = BacktestResult(
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            final_capital=round(final_capital, 2),
            total_return_pct=round(((final_capital - initial_capital) / initial_capital) * 100, 2),
            total_pnl=round(final_capital - initial_capital, 2),
            equity_curve=equity_curve,
        )

        if not trades:
            return result

        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]

        result.total_trades = len(trades)
        result.winning_trades = len(winners)
        result.losing_trades = len(losers)
        result.win_rate = round(len(winners) / len(trades) * 100, 2)

        if winners:
            result.avg_win = round(sum(t.pnl for t in winners) / len(winners), 2)
            result.largest_win = round(max(t.pnl for t in winners), 2)
        if losers:
            result.avg_loss = round(sum(t.pnl for t in losers) / len(losers), 2)
            result.largest_loss = round(min(t.pnl for t in losers), 2)

        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

        result.avg_hold_minutes = round(
            sum(t.hold_time_minutes for t in trades) / len(trades), 1
        )
        result.trades_per_day = round(len(trades) / max(trading_days, 1), 2)

        # Sharpe ratio from equity curve
        if len(equity_curve) > 1:
            returns = []
            for i in range(1, len(equity_curve)):
                prev = equity_curve[i - 1][1]
                curr = equity_curve[i][1]
                if prev > 0:
                    returns.append((curr - prev) / prev)
            if returns and len(returns) > 1:
                import statistics
                avg_r = statistics.mean(returns)
                std_r = statistics.stdev(returns)
                if std_r > 0:
                    result.sharpe_ratio = round((avg_r / std_r) * (252 ** 0.5), 2)

        # Max drawdown
        peak = initial_capital
        max_dd = 0
        for _, eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = round(max_dd, 2)

        # By strategy
        strats: Dict[str, Dict] = {}
        for t in trades:
            s = strats.setdefault(t.strategy, {"trades": 0, "pnl": 0, "wins": 0})
            s["trades"] += 1
            s["pnl"] = round(s["pnl"] + t.pnl, 2)
            if t.pnl > 0:
                s["wins"] += 1
        for s in strats.values():
            s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0
        result.by_strategy = strats

        return result
