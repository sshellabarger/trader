"""
Backtesting Framework — Single-symbol ETF.
Simulates ORB + VWAP on one symbol (TQQQ) bar by bar.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from .broker import AlpacaBroker
from .config import Config
from .indicators import compute_indicators
from .journal import TradeRecord
from .regime import RegimeDetector
from .risk import RiskManager, PositionInfo
from .scanner import Candidate
from .strategies import BaseStrategy, Signal, SignalAction, SignalDirection
from .strategies.orb import ORBStrategy
from .strategies.vwap_reversion import VWAPReversionStrategy

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    total_pnl: float
    symbol: str = ""

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
    def __init__(self, config: Config):
        self.config = config
        self.broker = AlpacaBroker(config.broker)
        self.slippage_pct = config.backtest.slippage_bps / 10000.0
        self.commission = config.backtest.commission_per_share

    def run(self, symbols: List[str], start_date: str, end_date: str) -> BacktestResult:
        capital = self.config.backtest.initial_capital
        risk = RiskManager(self.config.risk)

        strategies: List[BaseStrategy] = []
        if self.config.strategy.orb_enabled:
            strategies.append(ORBStrategy(self.config))
        if self.config.strategy.vwap_enabled:
            strategies.append(VWAPReversionStrategy(self.config))

        primary = symbols[0] if symbols else self.config.strategy.primary_symbol

        regime_detector = RegimeDetector(
            ema_period=self.config.strategy.vwap_regime_ema_period
        )
        regime_daily_bars = self._fetch_regime_bars(primary, start_date, end_date)
        logger.info(f"Regime: {len(regime_daily_bars)} daily bars for {primary}")

        all_trades: List[TradeRecord] = []
        equity_curve: List[Tuple[str, float]] = []
        open_positions: Dict[str, Dict] = {}

        current = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        trading_days = 0

        logger.info(f"Backtest: {start_date} -> {end_date}, symbol={primary}, "
                     f"capital=${capital:,.0f}")

        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            day_str = current.isoformat()

            bars = self._fetch_day_bars(primary, day_str)
            if not bars or len(bars) < 20:
                current += timedelta(days=1)
                continue

            trading_days += 1
            risk.reset_daily(capital)
            day_start_capital = capital

            bars_up_to_today = [
                b for b in regime_daily_bars if b.get("t", "")[:10] <= day_str
            ]
            regime = regime_detector.update_from_bars(bars_up_to_today)

            for s in strategies:
                s.reset_daily()
                s.set_market_regime(regime)

            capital, day_trades = self._simulate_day(
                bars, strategies, risk, capital, open_positions, primary
            )

            all_trades.extend(day_trades)
            equity_curve.append((day_str, capital))

            day_pnl = capital - day_start_capital
            if day_trades:
                logger.info(f"  {day_str}: {len(day_trades)} trades, "
                            f"P&L=${day_pnl:+,.2f}, capital=${capital:,.2f} "
                            f"[{regime}]")

            current += timedelta(days=1)

        result = self._compute_metrics(
            all_trades, equity_curve, self.config.backtest.initial_capital,
            capital, start_date, end_date, trading_days
        )
        result.trades = all_trades
        result.symbol = primary

        logger.info(f"\n{'='*50}")
        logger.info(f"BACKTEST: {start_date} -> {end_date} ({primary})")
        logger.info(f"  Return: {result.total_return_pct:+.2f}%  P&L: ${result.total_pnl:+,.2f}")
        logger.info(f"  Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%")
        logger.info(f"  Sharpe: {result.sharpe_ratio:.2f}  Max DD: {result.max_drawdown_pct:.2f}%")
        logger.info(f"  Profit factor: {result.profit_factor:.2f}")
        for strat, stats in result.by_strategy.items():
            logger.info(f"  [{strat}] trades={stats['trades']} "
                        f"win={stats['win_rate']:.0f}% pnl=${stats['pnl']:+,.2f}")

        self._save_results(result)
        return result

    # ------------------------------------------------------------------

    def _simulate_day(
        self,
        bars: List[Dict],
        strategies: List[BaseStrategy],
        risk: RiskManager,
        capital: float,
        open_positions: Dict[str, Dict],
        primary: str,
    ) -> Tuple[float, List[TradeRecord]]:
        trades: List[TradeRecord] = []
        max_bars = len(bars)

        for bar_idx in range(5, max_bars):
            bars_so_far = bars[:bar_idx + 1]
            current_bar = bars_so_far[-1]
            current_price = float(current_bar["c"])
            current_high = float(current_bar["h"])
            current_low = float(current_bar["l"])

            # --- Check exits on open positions ---
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]

                if pos.get("direction") == "short":
                    if current_high >= pos["stop"]:
                        exit_price = pos["stop"] * (1 + self.slippage_pct)
                        pnl = (pos["entry"] - exit_price) * pos["qty"] - self.commission * pos["qty"] * 2
                        capital += pnl
                        record = TradeRecord(
                            symbol=sym, strategy=pos["strategy"], direction="short",
                            qty=pos["qty"], entry_time=pos["entry_time"],
                            entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                            stop_loss=pos["stop"], take_profit=pos["tp"],
                        )
                        record.close(exit_price, "stop_loss", current_bar.get("t", ""))
                        trades.append(record)
                        for s in strategies:
                            if s.name == pos["strategy"]:
                                s.on_stop_loss(sym)
                        del open_positions[sym]
                        continue

                    if current_low <= pos["tp"]:
                        exit_price = pos["tp"] * (1 + self.slippage_pct)
                        pnl = (pos["entry"] - exit_price) * pos["qty"] - self.commission * pos["qty"] * 2
                        capital += pnl
                        record = TradeRecord(
                            symbol=sym, strategy=pos["strategy"], direction="short",
                            qty=pos["qty"], entry_time=pos["entry_time"],
                            entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                            stop_loss=pos["stop"], take_profit=pos["tp"],
                        )
                        record.close(exit_price, "take_profit", current_bar.get("t", ""))
                        trades.append(record)
                        del open_positions[sym]
                        continue
                else:
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
                        for s in strategies:
                            if s.name == pos["strategy"]:
                                s.on_stop_loss(sym)
                        del open_positions[sym]
                        continue

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
                mock_pos = {"symbol": sym, "current_price": current_price,
                            "avg_entry_price": pos["entry"]}

                for strategy in strategies:
                    if strategy.name != pos["strategy"]:
                        continue
                    signal = strategy.evaluate(candidate, bars_so_far, indicators, mock_pos)
                    if signal and signal.action == SignalAction.EXIT:
                        exit_price = current_price * (1 - self.slippage_pct)
                        direction = pos.get("direction", "long")
                        if direction == "short":
                            pnl = (pos["entry"] - exit_price) * pos["qty"] - self.commission * pos["qty"] * 2
                        else:
                            pnl = (exit_price - pos["entry"]) * pos["qty"] - self.commission * pos["qty"] * 2
                        capital += pnl
                        record = TradeRecord(
                            symbol=sym, strategy=pos["strategy"], direction=direction,
                            qty=pos["qty"], entry_time=pos["entry_time"],
                            entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                            stop_loss=pos["stop"], take_profit=pos["tp"],
                        )
                        record.close(exit_price, signal.reason, current_bar.get("t", ""))
                        trades.append(record)
                        del open_positions[sym]
                        break

            # --- Check entries ---
            if len(open_positions) >= self.config.risk.max_positions:
                continue

            indicators = compute_indicators(bars_so_far)
            candidate = Candidate(
                symbol=primary, price=current_price,
                prev_close=float(bars[0]["o"]),
                gap_pct=0,
                change_pct=((current_price - float(bars[0]["o"])) / float(bars[0]["o"]) * 100),
                volume=float(current_bar["v"]),
                avg_volume=float(bars[0]["v"]),
                relative_volume=indicators.get("relative_volume", 1) or 1,
                high=max(float(b["h"]) for b in bars_so_far),
                low=min(float(b["l"]) for b in bars_so_far),
                open_price=float(bars[0]["o"]),
            )

            for strategy in strategies:
                signal = strategy.evaluate(candidate, bars_so_far, indicators, None)
                if signal and signal.action == SignalAction.ENTER:
                    trade_symbol = primary

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

                    direction = "long"
                    if signal.direction == SignalDirection.SHORT:
                        direction = "short"
                        entry_price = current_price * (1 - self.slippage_pct)

                    open_positions[trade_symbol] = {
                        "entry": entry_price,
                        "stop": signal.stop_loss,
                        "tp": signal.take_profit,
                        "qty": size.shares,
                        "strategy": strategy.name,
                        "entry_time": current_bar.get("t", ""),
                        "reason": signal.reason,
                        "direction": direction,
                    }
                    strategy.on_fill(trade_symbol, signal)
                    risk.record_trade()
                    break

        # End of day: close remaining
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            last_bar = bars[-1]
            direction = pos.get("direction", "long")
            exit_price = float(last_bar["c"])

            if direction == "short":
                exit_price = exit_price * (1 + self.slippage_pct)
                pnl = (pos["entry"] - exit_price) * pos["qty"] - self.commission * pos["qty"] * 2
            else:
                exit_price = exit_price * (1 - self.slippage_pct)
                pnl = (exit_price - pos["entry"]) * pos["qty"] - self.commission * pos["qty"] * 2

            capital += pnl
            record = TradeRecord(
                symbol=sym, strategy=pos["strategy"], direction=direction,
                qty=pos["qty"], entry_time=pos["entry_time"],
                entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
                stop_loss=pos["stop"], take_profit=pos["tp"],
            )
            record.close(exit_price, "eod_close", last_bar.get("t", ""))
            trades.append(record)

        open_positions.clear()
        return capital, trades

    # ------------------------------------------------------------------

    def _fetch_day_bars(self, symbol: str, day_str: str) -> List[Dict]:
        start = f"{day_str}T09:30:00-05:00"
        end = f"{day_str}T16:00:00-05:00"
        bars = self.broker.get_bars(symbol, timeframe="1Min", start=start, end=end, limit=500)
        time.sleep(0.3)
        return bars if bars else []

    def _fetch_regime_bars(self, symbol: str, start_date: str, end_date: str) -> List[Dict]:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date() - timedelta(days=60)
        bars = self.broker.get_bars(
            symbol, timeframe="1Day",
            start=start_dt.isoformat(), end=end_date, limit=500,
        )
        return bars if bars else []

    # ------------------------------------------------------------------

    def _compute_metrics(self, trades, equity_curve, initial, final,
                         start_date, end_date, trading_days):
        result = BacktestResult(
            start_date=start_date, end_date=end_date,
            initial_capital=initial,
            final_capital=round(final, 2),
            total_return_pct=round(((final - initial) / initial) * 100, 2),
            total_pnl=round(final - initial, 2),
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

        peak = initial
        max_dd = 0
        for _, eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown_pct = round(max_dd, 2)

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

    # ------------------------------------------------------------------

    def _save_results(self, result: BacktestResult):
        out_dir = "backtest_results"
        os.makedirs(out_dir, exist_ok=True)
        tag = f"{result.start_date}_to_{result.end_date}"

        if result.trades:
            trades_path = os.path.join(out_dir, f"trades_{tag}.csv")
            fieldnames = [
                "symbol", "strategy", "direction", "qty",
                "entry_time", "entry_price", "entry_reason",
                "stop_loss", "take_profit",
                "exit_time", "exit_price", "exit_reason",
                "pnl", "pnl_pct", "risk_reward_target", "risk_reward_actual",
                "hold_time_minutes", "is_winner",
            ]
            with open(trades_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                for trade in result.trades:
                    writer.writerow(asdict(trade))
            logger.info(f"  Trades: {trades_path}")

        summary_path = os.path.join(out_dir, f"summary_{tag}.json")
        summary = {
            "start_date": result.start_date,
            "end_date": result.end_date,
            "symbol": result.symbol,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return_pct": result.total_return_pct,
            "total_pnl": result.total_pnl,
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "win_rate": result.win_rate,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "largest_win": result.largest_win,
            "largest_loss": result.largest_loss,
            "profit_factor": result.profit_factor,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "avg_hold_minutes": result.avg_hold_minutes,
            "trades_per_day": result.trades_per_day,
            "by_strategy": result.by_strategy,
        }
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"  Summary: {summary_path}")

        if result.equity_curve:
            equity_path = os.path.join(out_dir, f"equity_{tag}.csv")
            with open(equity_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["date", "equity"])
                for dt, eq in result.equity_curve:
                    writer.writerow([dt, round(eq, 2)])
            logger.info(f"  Equity: {equity_path}")

        analysis_path = os.path.join(out_dir, f"analysis_{tag}.txt")
        with open(analysis_path, "w") as f:
            f.write(f"BACKTEST: {result.start_date} to {result.end_date} ({result.symbol})\n")
            f.write(f"{'='*60}\n\n")
            f.write(f"Capital: ${result.initial_capital:,.2f} -> ${result.final_capital:,.2f}\n")
            f.write(f"Return: {result.total_return_pct:+.2f}%  P&L: ${result.total_pnl:+,.2f}\n")
            f.write(f"Trades: {result.total_trades}  Win rate: {result.win_rate:.1f}%\n")
            f.write(f"Avg win: ${result.avg_win:,.2f}  Avg loss: ${result.avg_loss:,.2f}\n")
            f.write(f"Profit factor: {result.profit_factor:.2f}  Sharpe: {result.sharpe_ratio:.2f}\n")
            f.write(f"Max DD: {result.max_drawdown_pct:.2f}%\n\n")

            for strat, stats in result.by_strategy.items():
                f.write(f"[{strat}] {stats['trades']} trades, "
                        f"{stats['win_rate']:.0f}% win, ${stats['pnl']:+,.2f}\n")

            f.write(f"\nEXIT REASONS\n")
            exit_reasons: Dict[str, Dict] = {}
            for t in result.trades:
                r = t.exit_reason or "unknown"
                if len(r) > 42:
                    r = r[:40] + "..."
                if r not in exit_reasons:
                    exit_reasons[r] = {"count": 0, "pnl": 0}
                exit_reasons[r]["count"] += 1
                exit_reasons[r]["pnl"] += t.pnl
            for reason, data in sorted(exit_reasons.items(), key=lambda x: x[1]["count"], reverse=True):
                f.write(f"  {reason:42s} n={data['count']:3d}  ${data['pnl']:+,.2f}\n")

        logger.info(f"  Analysis: {analysis_path}")
