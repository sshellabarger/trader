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
from zoneinfo import ZoneInfo

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
from .news import NewsFeed, allow_entry, fetch_alpaca_news, score_articles

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def session_window(day_str: str) -> Tuple[str, str]:
    """
    Regular-session start/end for a trading day as ISO strings with the
    correct ET offset for that date (handles EST/EDT transitions).
    """
    day = datetime.strptime(day_str, "%Y-%m-%d")
    start = day.replace(hour=9, minute=30, tzinfo=_ET)
    end = day.replace(hour=16, minute=0, tzinfo=_ET)
    return start.isoformat(), end.isoformat()


def minutes_to_close(t_iso: str) -> Optional[float]:
    """Minutes from a bar's timestamp to the 16:00 ET close, or None if the
    timestamp can't be parsed. Used to mirror the engine's EOD gating."""
    try:
        dt = datetime.fromisoformat(t_iso.replace("Z", "+00:00")).astimezone(_ET)
    except (ValueError, AttributeError):
        return None
    close_dt = dt.replace(hour=16, minute=0, second=0, microsecond=0)
    return (close_dt - dt).total_seconds() / 60.0


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
        self._news_feed = None      # set per-run when news_enabled
        self._news_blocked = 0
        self.commission = config.backtest.commission_per_share

    def run(self, symbols: List[str], start_date: str, end_date: str) -> BacktestResult:
        capital = self.config.backtest.initial_capital
        risk = RiskManager(self.config.risk)

        strategies: List[BaseStrategy] = []
        if self.config.strategy.orb_enabled:
            strategies.append(ORBStrategy(self.config))
        if self.config.strategy.vwap_enabled:
            strategies.append(VWAPReversionStrategy(self.config))

        traded = list(symbols) if symbols else self.config.get_trading_symbols()
        label = "+".join(traded)

        # Regime mirrors the live engine: always the primary index (QQQ),
        # never the leveraged instrument being traded.
        regime_symbol = self.config.strategy.primary_symbol
        regime_detector = RegimeDetector(
            ema_period=self.config.strategy.vwap_regime_ema_period
        )
        regime_daily_bars = self._fetch_regime_bars(regime_symbol, start_date, end_date)
        logger.info(f"Regime: {len(regime_daily_bars)} daily bars for {regime_symbol}")

        # Optional news sentiment filter (OFF unless news_enabled). Pre-fetch the
        # whole window once; NewsFeed.as_of keeps each per-bar lookup
        # lookahead-safe. A fetch failure leaves the filter inert, never crashes.
        self._news_feed = None
        self._news_blocked = 0
        if self.config.strategy.news_enabled:
            sc = self.config.strategy
            try:
                articles = score_articles(fetch_alpaca_news(
                    sc.news_symbols, start_date, end_date,
                    api_key=self.broker.config.api_key,
                    api_secret=self.broker.config.api_secret,
                    max_pages=400,
                ))
                self._news_feed = NewsFeed(articles)
                logger.info(f"News filter ON: {len(articles)} articles for "
                            f"{','.join(sc.news_symbols)}")
            except Exception as exc:
                logger.warning(f"News fetch failed; filter inert this run: {exc}")

        all_trades: List[TradeRecord] = []
        equity_curve: List[Tuple[str, float]] = []
        open_positions: Dict[str, Dict] = {}

        current = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        trading_days = 0

        logger.info(f"Backtest: {start_date} -> {end_date}, symbols={label}, "
                     f"capital=${capital:,.0f}")

        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue

            day_str = current.isoformat()

            day_bars = {sym: self._fetch_day_bars(sym, day_str) for sym in traded}
            if not any(len(b) >= 20 for b in day_bars.values()):
                current += timedelta(days=1)
                continue

            trading_days += 1
            risk.reset_daily(capital)
            day_start_capital = capital

            # Strictly PRIOR days only: the live engine computes regime pre-open,
            # when the current day's daily bar does not yet exist. Using "<=" here
            # would leak the simulated day's own close (lookahead bias).
            bars_up_to_today = [
                b for b in regime_daily_bars if b.get("t", "")[:10] < day_str
            ]
            regime = regime_detector.update_from_bars(bars_up_to_today)

            # Overnight gap (prior close -> today's open) of the regime symbol,
            # for the optional ORB overnight-alignment gate. Uses ONLY today's
            # open (known at the bell) and strictly prior closes, so it adds no
            # lookahead. None when the prior/today daily bar is unavailable, in
            # which case the strategy gate stays inert.
            overnight_gap_pct = None
            today_daily = next(
                (b for b in regime_daily_bars if b.get("t", "")[:10] == day_str), None
            )
            if bars_up_to_today and today_daily:
                prior_close = float(bars_up_to_today[-1].get("c", 0) or 0)
                today_open = float(today_daily.get("o", 0) or 0)
                if prior_close > 0 and today_open > 0:
                    overnight_gap_pct = (today_open - prior_close) / prior_close * 100.0

            for s in strategies:
                s.reset_daily()
                s.set_market_regime(regime)

            capital, day_trades = self._simulate_day(
                day_bars, strategies, risk, capital, open_positions,
                overnight_gap_pct=overnight_gap_pct,
            )

            all_trades.extend(day_trades)
            # Mark any position carried overnight to the day's last close, so the
            # equity curve, Sharpe and drawdown stay honest on holding days
            # (capital itself only reflects realized trades). No-op when flat.
            mtm = 0.0
            for psym, pos in open_positions.items():
                pbars = day_bars.get(psym)
                if pbars:
                    lc = float(pbars[-1]["c"])
                    if pos.get("direction") == "short":
                        mtm += (pos["entry"] - lc) * pos["qty"]
                    else:
                        mtm += (lc - pos["entry"]) * pos["qty"]
            equity_curve.append((day_str, capital + mtm))

            day_pnl = capital - day_start_capital
            if day_trades:
                logger.info(f"  {day_str}: {len(day_trades)} trades, "
                            f"P&L=${day_pnl:+,.2f}, capital=${capital:,.2f} "
                            f"[{regime}]")

            current += timedelta(days=1)

        # Settle any position still held at the very end of the backtest at the
        # last available close, so realized P&L and metrics include it.
        for sym in list(open_positions.keys()):
            pbars = day_bars.get(sym)
            if not pbars:
                continue
            pos = open_positions[sym]
            lc = float(pbars[-1]["c"])
            exit_px = self._apply_slippage(pos.get("direction", "long"), lc)
            capital += self._book_exit(
                pos, sym, exit_px, "backtest_end", pbars[-1].get("t", ""),
                all_trades, strategies,
            )
            del open_positions[sym]
        if equity_curve:
            equity_curve[-1] = (equity_curve[-1][0], capital)

        if self._news_feed is not None:
            logger.info(f"News filter blocked {self._news_blocked} entries")

        result = self._compute_metrics(
            all_trades, equity_curve, self.config.backtest.initial_capital,
            capital, start_date, end_date, trading_days
        )
        result.trades = all_trades
        result.symbol = label

        logger.info(f"\n{'='*50}")
        logger.info(f"BACKTEST: {start_date} -> {end_date} ({label})")
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

    def _apply_slippage(self, direction: str, price: float) -> float:
        """Exit fill price after slippage. A long exit SELLS (receives less);
        a short exit BUYS to cover (pays more)."""
        if direction == "short":
            return price * (1 + self.slippage_pct)
        return price * (1 - self.slippage_pct)

    def _book_exit(
        self, pos: Dict, sym: str, exit_price: float, reason: str,
        exit_time: str, trades: List[TradeRecord], strategies: List[BaseStrategy],
    ) -> float:
        """Close a simulated position: build the record, fire on_stop_loss when
        relevant, append to trades, and return the realized P&L."""
        qty = pos["qty"]
        direction = pos.get("direction", "long")
        if direction == "short":
            pnl = (pos["entry"] - exit_price) * qty - self.commission * qty * 2
        else:
            pnl = (exit_price - pos["entry"]) * qty - self.commission * qty * 2
        record = TradeRecord(
            symbol=sym, strategy=pos["strategy"], direction=direction,
            qty=qty, entry_time=pos["entry_time"],
            entry_price=pos["entry"], entry_reason=pos.get("reason", ""),
            stop_loss=pos["stop"], take_profit=pos["tp"],
        )
        record.close(exit_price, reason, exit_time)
        # record.close() recomputes a commission-free pnl; overwrite with the
        # net figure that actually moved capital so win-rate/profit-factor/
        # daily_pnl all agree with the equity curve.
        record.pnl = pnl
        record.is_winner = pnl > 0
        trades.append(record)
        if reason == "stop_loss":
            for s in strategies:
                if s.name == pos["strategy"]:
                    s.on_stop_loss(sym)
        return pnl

    def _simulate_day(
        self,
        day_bars: Dict[str, List[Dict]],
        strategies: List[BaseStrategy],
        risk: RiskManager,
        capital: float,
        open_positions: Dict[str, Dict],
        overnight_gap_pct: Optional[float] = None,
    ) -> Tuple[float, List[TradeRecord]]:
        """Simulate one session across all traded symbols on a shared timeline.

        Each symbol is evaluated against its own bars; positions, capital, and
        the daily trade counter are shared. EOD entry-cutoff and force-flatten
        mirror the live engine (no_trade_last_minutes / eod_minutes_before_close).
        """
        trades: List[TradeRecord] = []
        warmup = 5  # bars before a symbol is eligible (range + indicators)
        bp = capital * self.config.backtest.margin_multiple  # mirror live buying power
        # entry_fill_next_open: signals waiting to fill at the NEXT bar's open
        # (symbol → pending entry details). Local to the day on purpose — an
        # unfilled market order does not survive the session.
        pending_entries: Dict[str, Dict] = {}

        # Overnight-hold: a position carried in from a prior day exits at THIS
        # day's open (capturing the overnight close->open move) before any new
        # intraday logic, so the symbol is free to trade again today.
        if self.config.strategy.orb_hold_overnight and open_positions:
            for psym, pbars in day_bars.items():
                pos = open_positions.get(psym)
                if pos and pos.get("held_overnight") and pbars:
                    pdir = pos.get("direction", "long")
                    open_px = self._apply_slippage(pdir, float(pbars[0]["o"]))
                    capital += self._book_exit(
                        pos, psym, open_px, "overnight_exit",
                        pbars[0].get("t", ""), trades, strategies,
                    )
                    risk.record_pnl(trades[-1].pnl)
                    del open_positions[psym]

        # Merge all symbols' bars into one time-ordered event stream. Ties broken
        # by the traded-symbol order (e.g. TQQQ before SQQQ) so the per-day ORB
        # cap resolves the SAME instrument the live engine would pick.
        order_index = {sym: i for i, sym in enumerate(day_bars)}
        events = []
        for sym, bars in day_bars.items():
            for idx in range(len(bars)):
                events.append((bars[idx].get("t", ""), sym, idx))
        events.sort(key=lambda e: (e[0], order_index[e[1]]))

        for t_iso, sym, idx in events:
            if idx < warmup:
                continue
            bars = day_bars[sym]
            bars_so_far = bars[: idx + 1]
            current_bar = bars_so_far[-1]
            current_price = float(current_bar["c"])
            current_high = float(current_bar["h"])
            current_low = float(current_bar["l"])
            ctime = current_bar.get("t", "")
            mins_left = minutes_to_close(t_iso)

            # --- Deferred entry fill (entry_fill_next_open) ---
            # A signal from bar N fills as a market order at bar N+1's OPEN —
            # the earliest price the live poll-loop bot could actually get.
            # Booked before exit handling so this bar's low/high can stop the
            # brand-new position out realistically within its fill bar.
            pend = pending_entries.get(sym)
            if pend is not None and idx > pend["signal_idx"]:
                fill_ref = float(current_bar.get("o", current_price))
                if pend["direction"] == "short":
                    entry_px = fill_ref * (1 - self.slippage_pct)
                else:
                    entry_px = fill_ref * (1 + self.slippage_pct)
                open_positions[sym] = {
                    "entry": entry_px,
                    "stop": pend["stop"],
                    "tp": pend["tp"],
                    "qty": pend["qty"],
                    "strategy": pend["strategy"],
                    "entry_time": ctime,
                    "reason": pend["reason"],
                    "direction": pend["direction"],
                }
                del pending_entries[sym]

            # --- Exit handling for this symbol's open position ---
            if sym in open_positions:
                pos = open_positions[sym]
                direction = pos.get("direction", "long")

                # EOD forced flatten (mirror live should_close_all). With
                # overnight-hold ON, an ORB position is NOT force-flattened in
                # this window; it keeps its stop/TP through the close and the
                # end-of-day sweep decides whether to carry it overnight.
                if mins_left is not None and mins_left <= self.config.risk.eod_minutes_before_close:
                    hold_orb = (self.config.strategy.orb_hold_overnight
                                and pos.get("strategy") == "orb")
                    if not hold_orb:
                        exit_price = self._apply_slippage(direction, current_price)
                        capital += self._book_exit(pos, sym, exit_price, "eod_close", ctime, trades, strategies)
                        risk.record_pnl(trades[-1].pnl)
                        del open_positions[sym]
                        continue

                # Intrabar stop / take-profit — gap-aware fills. A stop is a
                # MARKET order once touched: if the bar OPENS beyond the stop
                # (gapped through), the realistic fill is the open, not the
                # stop price. Stops were 139 of 209 exits in the 1-yr run, so
                # assuming at-price fills systematically overstated results.
                # A take-profit LIMIT gets the favorable side of the same
                # logic: a bar opening beyond the limit fills at the (better)
                # open.
                bar_open = float(current_bar.get("o", current_price))
                exit_price = None
                reason = None
                if direction == "short":
                    if current_high >= pos["stop"]:
                        stop_ref = max(pos["stop"], bar_open)
                        exit_price, reason = self._apply_slippage("short", stop_ref), "stop_loss"
                    elif current_low <= pos["tp"]:
                        tp_ref = min(pos["tp"], bar_open)
                        exit_price, reason = self._apply_slippage("short", tp_ref), "take_profit"
                else:
                    if current_low <= pos["stop"]:
                        stop_ref = min(pos["stop"], bar_open)
                        exit_price, reason = self._apply_slippage("long", stop_ref), "stop_loss"
                    elif current_high >= pos["tp"]:
                        tp_ref = max(pos["tp"], bar_open)
                        exit_price, reason = self._apply_slippage("long", tp_ref), "take_profit"

                if exit_price is not None:
                    capital += self._book_exit(pos, sym, exit_price, reason, ctime, trades, strategies)
                    risk.record_pnl(trades[-1].pnl)
                    del open_positions[sym]
                    continue

                # Strategy-driven exit
                indicators = compute_indicators(bars_so_far)
                ex_candidate = Candidate(
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
                    signal = strategy.evaluate(ex_candidate, bars_so_far, indicators, mock_pos)
                    if signal and signal.action == SignalAction.EXIT:
                        ep = self._apply_slippage(direction, current_price)
                        capital += self._book_exit(pos, sym, ep, signal.reason, ctime, trades, strategies)
                        risk.record_pnl(trades[-1].pnl)
                        del open_positions[sym]
                        break

                if sym not in open_positions:
                    continue  # exited this bar; no entry on the same bar

            # --- Entry handling for this symbol ---
            if sym in open_positions:
                continue
            if len(open_positions) >= self.config.risk.max_positions:
                continue
            if mins_left is not None and mins_left <= self.config.risk.no_trade_last_minutes:
                continue

            indicators = compute_indicators(bars_so_far)
            # Same overnight gap for every symbol/bar this day; the ORB gate
            # reads it only when orb_require_overnight_alignment is enabled.
            indicators["overnight_gap_pct"] = overnight_gap_pct
            day_open = float(bars[0]["o"])
            candidate = Candidate(
                symbol=sym, price=current_price,
                prev_close=day_open,
                gap_pct=0,
                change_pct=((current_price - day_open) / day_open * 100) if day_open else 0,
                volume=float(current_bar["v"]),
                avg_volume=float(bars[0]["v"]),
                relative_volume=indicators.get("relative_volume", 1) or 1,
                high=max(float(b["h"]) for b in bars_so_far),
                low=min(float(b["l"]) for b in bars_so_far),
                open_price=day_open,
            )

            for strategy in strategies:
                if not strategy.applies_to(sym):
                    continue
                signal = strategy.evaluate(candidate, bars_so_far, indicators, None)
                if signal and signal.action == SignalAction.ENTER:
                    trade_symbol = signal.symbol

                    # News sentiment gate (inert unless news_enabled). Block
                    # entries that fight strong recent sentiment. as_of(ctime)
                    # never sees future articles, so this stays backtest-safe.
                    if self._news_feed is not None:
                        sc = self.config.strategy
                        is_long = signal.direction != SignalDirection.SHORT
                        bullish = ((trade_symbol == sc.leveraged_bull) if is_long
                                   else (trade_symbol != sc.leveraged_bull))
                        ok_news, _why = allow_entry(
                            self._news_feed, ctime, bullish,
                            window_min=sc.news_window_min,
                            block_below=sc.news_block_below,
                            min_articles=sc.news_min_articles,
                            symbols=sc.news_symbols,
                        )
                        if not ok_news:
                            self._news_blocked += 1
                            continue

                    positions_list = [
                        PositionInfo(s, 1, p["entry"], current_price,
                                     p["entry"] * p["qty"], 0, 0)
                        for s, p in open_positions.items()
                    ]
                    ok, reason = risk.validate_entry(
                        signal, capital, bp, positions_list
                    )
                    if not ok:
                        continue

                    size = risk.calculate_position_size(
                        signal, capital, bp, len(open_positions)
                    )
                    if size.shares < 1:
                        continue

                    direction = "short" if signal.direction == SignalDirection.SHORT else "long"
                    if (self.config.backtest.entry_fill_next_open
                            and trade_symbol == sym):
                        # Honest timing: defer the fill to the next bar's open
                        # (see BacktestConfig.entry_fill_next_open). The order
                        # is committed now — daily entry budget and strategy
                        # state advance exactly as on an immediate fill.
                        pending_entries[trade_symbol] = {
                            "signal_idx": idx,
                            "direction": direction,
                            "stop": signal.stop_loss,
                            "tp": signal.take_profit,
                            "qty": size.shares,
                            "strategy": strategy.name,
                            "reason": signal.reason,
                        }
                    else:
                        if direction == "short":
                            entry_price = current_price * (1 - self.slippage_pct)
                        else:
                            entry_price = current_price * (1 + self.slippage_pct)

                        open_positions[trade_symbol] = {
                            "entry": entry_price,
                            "stop": signal.stop_loss,
                            "tp": signal.take_profit,
                            "qty": size.shares,
                            "strategy": strategy.name,
                            "entry_time": ctime,
                            "reason": signal.reason,
                            "direction": direction,
                        }
                    strategy.on_fill(trade_symbol, signal)
                    risk.record_entry()
                    break

        # End of day: close anything still open at its symbol's last bar —
        # UNLESS overnight-hold is on and the ORB position is a winner at the
        # close, in which case carry it past the close (it exits at the next
        # session's open, handled by the day-open pass above). Losers and
        # non-ORB positions always flatten here.
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            last_bar = day_bars[sym][-1]
            direction = pos.get("direction", "long")
            last_close = float(last_bar["c"])
            in_profit = (last_close > pos["entry"]) if direction != "short" else (last_close < pos["entry"])
            if (self.config.strategy.orb_hold_overnight
                    and pos.get("strategy") == "orb" and in_profit):
                pos["held_overnight"] = True
                continue
            exit_price = self._apply_slippage(direction, last_close)
            capital += self._book_exit(pos, sym, exit_price, "eod_close", last_bar.get("t", ""), trades, strategies)
            risk.record_pnl(trades[-1].pnl)
            del open_positions[sym]

        return capital, trades

    # ------------------------------------------------------------------

    def _fetch_day_bars(self, symbol: str, day_str: str) -> List[Dict]:
        start, end = session_window(day_str)
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
