"""
Trading Engine — ETF-focused.
No scanner needed. Trades QQQ/TQQQ directly with ORB + VWAP.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .broker import AlpacaBroker
from .config import Config
from .indicators import compute_indicators
from .journal import TradeJournal
from .regime import RegimeDetector
from .risk import RiskManager, PositionInfo, SizeResult
from .scanner import Candidate
from .strategies import BaseStrategy, Signal, SignalAction, SignalDirection
from .strategies.orb import ORBStrategy
from .strategies.vwap_reversion import VWAPReversionStrategy

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


class Engine:
    """ETF-focused trading engine."""

    def __init__(self, config: Config):
        self.config = config
        self.broker = AlpacaBroker(config.broker)
        self.risk = RiskManager(config.risk)
        self.journal = TradeJournal()

        # What we trade
        self.symbols = config.get_trading_symbols()
        self.primary = config.strategy.primary_symbol

        # Strategies
        self.strategies: List[BaseStrategy] = []
        if config.strategy.orb_enabled:
            self.strategies.append(ORBStrategy(config))
        if config.strategy.vwap_enabled:
            self.strategies.append(VWAPReversionStrategy(config))

        # Regime
        self.regime_detector = RegimeDetector(
            ema_period=config.strategy.vwap_regime_ema_period
        )

        logger.info(f"ETF Engine: trading {self.symbols}")
        logger.info(f"Strategies: {[s.name for s in self.strategies]}")

        # State
        self._running = False
        self._today: Optional[str] = None
        self._bars_cache: Dict[str, List[Dict]] = {}
        self._indicators_cache: Dict[str, Dict] = {}
        self._flatten_requested = False  # EOD liquidation issued for today
        self._pending_close: set = set()  # symbols committed to exit, retried each tick
        # symbol → UTC ISO time the entry was submitted; bounds exit-fill lookups
        # so a stale prior-session fill can't be booked as this trade's exit.
        self._entry_time_utc: Dict[str, str] = {}
        # Today's overnight gap (regime symbol close->open), for the optional ORB
        # overnight-alignment filter. Recomputed each day in _new_day.
        self._overnight_gap_pct: Optional[float] = None

    def run(self, loop_interval: int = 30):
        logger.info("=" * 60)
        logger.info("STARTING ETF DAYTRADER ENGINE")
        logger.info(f"Symbols: {self.symbols}")
        logger.info("=" * 60)

        self._running = True
        self._initialize()

        try:
            while self._running:
                try:
                    self._tick()
                except Exception as exc:
                    logger.error(f"Error in tick: {exc}", exc_info=True)
                time.sleep(loop_interval)
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        finally:
            self._shutdown()

    def stop(self):
        self._running = False

    def _initialize(self):
        acct = self.broker.get_account()
        if acct:
            equity = float(acct.get("equity", 0))
            bp = float(acct.get("buying_power", 0))
            logger.info(f"Account: equity=${equity:,.2f}  buying_power=${bp:,.2f}")
            self.risk.reset_daily(equity)
        else:
            logger.error("Failed to connect to broker")
            self._running = False
            return

        logger.info(f"Mode: {'DRY RUN' if self.config.dry_run else 'LIVE'}")

    def _tick(self):
        today = date.today().isoformat()
        if self._today != today:
            self._new_day(today)

        if not self.broker.is_market_open():
            return

        # Clear bars cache so we get fresh data each tick
        self._bars_cache.clear()
        self._indicators_cache.clear()

        minutes_left = self.broker.minutes_until_close()
        closing = self.risk.should_close_all(minutes_left)

        if closing:
            self._flatten_all("end_of_day")

        # Always reconcile: positions that have left the book (a bracket TP/SL
        # fill, or the just-issued EOD flatten) are journaled at their real fill
        # prices. Liquidation is async, so the EOD close is booked here over the
        # next tick(s) rather than assumed complete inline.
        self._check_exits(reconcile_only=closing)
        if closing:
            return

        # Retry any committed exit whose close hasn't confirmed yet, so a
        # position whose protective legs were cancelled is never left naked.
        self._retry_pending_closes()

        if self.risk.should_stop_new_entries(minutes_left):
            return

        # Fetch bars and generate signals for our ETF symbols
        signals = self._generate_signals()

        for signal in signals:
            if signal.action != SignalAction.ENTER:
                continue
            strat = self._strategy_named(signal.strategy)
            # can_open() catches cross-symbol caps (e.g. one ORB entry/day) that
            # evaluate() can't see: all signals are generated before any fill, so
            # without this a choppy open could enter both TQQQ and SQQQ at once.
            if strat is not None and not strat.can_open(signal.symbol):
                continue
            self._execute_entry(signal)

    def _strategy_named(self, name: str) -> Optional[BaseStrategy]:
        for s in self.strategies:
            if s.name == name:
                return s
        return None

    def _new_day(self, today: str):
        if self._today is not None:
            # Persist the prior day's journal. A failure here (disk full, a
            # permissions problem, etc.) must never stop the day from rolling
            # over or halt trading, so it is contained and logged rather than
            # raised. Previously an exception here left self._today unchanged,
            # so every subsequent tick re-entered _new_day, re-raised, and the
            # bot stopped trading entirely until the process was restarted.
            try:
                self.journal.save_daily_csv()
                self.journal.save_daily_summary()
                summary = self.journal.daily_summary()
                logger.info(f"Day ended — {summary}")
            except Exception as exc:
                logger.error(
                    f"Failed to persist journal for {self._today}: {exc}",
                    exc_info=True,
                )

        self._today = today

        # Roll the journal to the new day now, before the fallible broker/regime
        # setup below. The journal is rolled by re-instantiation; if it stayed at
        # the end of this method and a call below raised, the day would already
        # be advanced but the journal would still point at the previous day, so
        # the next day's trades would be appended to the previous day's file.
        # Carry any still-open position across the boundary so a live trade is
        # never dropped from the book.
        prev_open = self.journal.open_trades
        self.journal = TradeJournal()
        self.journal.open_trades = prev_open

        self._bars_cache.clear()
        self._indicators_cache.clear()
        self._flatten_requested = False
        self._pending_close.clear()
        self._entry_time_utc.clear()

        equity = self.broker.get_equity()
        if equity > 0:
            self.risk.reset_daily(equity)

        # Overnight gap (regime symbol's prior close -> today's open), same
        # definition the backtester uses. Computed once per day and injected into
        # indicators in _generate_signals. Fail-open: stays None if it can't be
        # computed, which leaves the ORB overnight filter inert for the day.
        self._overnight_gap_pct = None

        # Detect regime
        try:
            start_dt = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            spy_bars = self.broker.get_bars(
                self.primary, timeframe="1Day",
                start=start_dt,
                limit=60,
            )
            regime = self.regime_detector.update_from_bars(spy_bars or [])
            for s in self.strategies:
                s.reset_daily()
                s.set_market_regime(regime)
            status = self.regime_detector.status()
            price = status.get('spy_price')
            ema = status.get('spy_ema')
            if price and ema:
                logger.info(f"Regime: {regime} ({self.primary}=${price:.2f}, EMA=${ema:.2f})")
            else:
                logger.info(f"Regime: {regime}")

            # Overnight gap from the same daily bars (no extra API call).
            if spy_bars and len(spy_bars) >= 2:
                last_bar = spy_bars[-1]
                if str(last_bar.get("t", ""))[:10] == today:
                    prior_close = float(spy_bars[-2].get("c", 0) or 0)
                    today_open = float(last_bar.get("o", 0) or 0)
                    if prior_close > 0 and today_open > 0:
                        self._overnight_gap_pct = (today_open - prior_close) / prior_close * 100.0
            if self.config.strategy.orb_require_overnight_alignment:
                if self._overnight_gap_pct is None:
                    logger.info("Overnight filter ON but gap unavailable — gate inert today")
                else:
                    allowed = self._overnight_gap_pct >= self.config.strategy.orb_overnight_gap_min_pct
                    logger.info(
                        f"Overnight gap ({self.primary}): {self._overnight_gap_pct:+.2f}%  ->  "
                        f"ORB longs {'ALLOWED' if allowed else 'BLOCKED'} today"
                    )
        except Exception as exc:
            logger.warning(f"Regime detection failed: {exc}")
            for s in self.strategies:
                s.reset_daily()
                s.set_market_regime("unknown")

        logger.info(f"New trading day: {today}")

    def _generate_signals(self) -> List[Signal]:
        signals: List[Signal] = []
        broker_positions = self.broker.get_positions()
        position_map = {p["symbol"]: p for p in broker_positions}

        # Evaluate each traded symbol against its own bars so signal prices
        # are always in the traded instrument's terms.
        for sym in self.symbols:
            bars = self._get_bars(sym)
            if not bars:
                logger.info(f"No bars yet for {sym}")
                continue

            logger.info(f"{sym}: {len(bars)} bars, "
                        f"latest=${float(bars[-1]['c']):.2f} @ {bars[-1].get('t', '?')}")

            indicators = compute_indicators(bars)
            # Supply the day's overnight gap (computed in _new_day) to the ORB
            # overnight-alignment gate. None when unavailable -> gate inert.
            indicators["overnight_gap_pct"] = self._overnight_gap_pct

            candidate = Candidate(
                symbol=sym,
                price=float(bars[-1]["c"]),
                prev_close=float(bars[0]["o"]),
                gap_pct=0,
                change_pct=0,
                volume=float(bars[-1]["v"]),
                avg_volume=float(bars[0]["v"]),
                relative_volume=indicators.get("relative_volume", 1) or 1,
                high=max(float(b["h"]) for b in bars),
                low=min(float(b["l"]) for b in bars),
                open_price=float(bars[0]["o"]),
            )

            position = position_map.get(sym)
            for strategy in self.strategies:
                if not strategy.applies_to(sym):
                    continue
                try:
                    signal = strategy.evaluate(candidate, bars, indicators, position)
                    if signal is not None:
                        signals.append(signal)
                except Exception as exc:
                    logger.error(f"Strategy {strategy.name} error: {exc}", exc_info=True)

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals

    def _get_bars(self, symbol: str) -> List[Dict]:
        cached = self._bars_cache.get(symbol)
        if cached:
            return cached

        # Regular session only (09:30 ET onward) so indicators and the ORB
        # opening range see the same data live as in the backtester.
        now = datetime.now(_ET)
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now <= start:
            return []
        bars = self.broker.get_bars(
            symbol, timeframe="1Min",
            start=start.isoformat(), end=now.isoformat(),
            limit=500,
        )
        if bars:
            self._bars_cache[symbol] = bars
        return bars

    def _execute_entry(self, signal: Signal):
        acct = self.broker.get_account()
        if not acct:
            return
        equity = float(acct.get("equity", 0))
        buying_power = float(acct.get("buying_power", 0))
        positions = self._get_position_infos()

        ok, reason = self.risk.validate_entry(signal, equity, buying_power, positions)
        if not ok:
            logger.debug(f"Entry blocked: {reason}")
            return

        size = self.risk.calculate_position_size(
            signal, equity, buying_power, len(positions)
        )
        if size.shares < 1:
            return

        logger.info(
            f"ENTRY: {signal.strategy} → {signal.direction.value.upper()} "
            f"{size.shares} {signal.symbol} @ {signal.entry_price:.2f} "
            f"(SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f})"
        )

        if self.config.dry_run:
            logger.info(f"DRY RUN — would submit order")
            self.journal.open_trade(
                signal.symbol, signal.strategy, signal.direction.value, size.shares,
                signal.entry_price, signal.stop_loss, signal.take_profit,
                signal.reason, signal.indicators,
            )
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(signal.symbol, signal)
            self.risk.record_entry()
            return

        order = self.broker.submit_bracket_order(
            symbol=signal.symbol,
            qty=size.shares,
            side="buy" if signal.direction == SignalDirection.LONG else "sell",
            take_profit_price=signal.take_profit,
            stop_loss_price=signal.stop_loss,
        )

        if order:
            self.journal.open_trade(
                signal.symbol, signal.strategy, signal.direction.value, size.shares,
                signal.entry_price, signal.stop_loss, signal.take_profit,
                signal.reason, signal.indicators,
            )
            # Stamp the entry time so a later "position gone" reconciliation only
            # books exit fills that happened after this point.
            self._entry_time_utc[signal.symbol] = datetime.now(timezone.utc).isoformat()
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(signal.symbol, signal)
            self.risk.record_entry()
        else:
            logger.warning(f"Order not accepted for {signal.symbol}; signal not consumed")

    def _check_exits(self, reconcile_only: bool = False):
        if not self.journal.open_trades:
            return

        broker_positions = self.broker.get_positions()
        position_map = {p["symbol"]: p for p in broker_positions}

        for sym, trade_record in list(self.journal.open_trades.items()):
            position = position_map.get(sym)
            if position is None:
                # A journaled trade with no live position is NOT proof the trade
                # closed: a freshly submitted bracket entry shows no position
                # until it fills (4 minutes on 2026-06-15). If the symbol still
                # has working orders, the entry hasn't filled — wait, don't
                # fabricate an exit. Only once there are no open orders has the
                # position truly left the book (a leg fill or the EOD flatten),
                # which reconciliation then books at the real fill price.
                if self.broker.get_orders(status="open", symbols=[sym]):
                    continue
                self._reconcile_vanished_position(sym, trade_record)
                continue
            if reconcile_only:
                # During the EOD flatten we only book positions that have left
                # the book; we don't run strategy-exit logic or re-close.
                continue

            current_price = float(position.get("current_price", 0))
            if current_price <= 0:
                continue

            bars = self._get_bars(sym)
            if not bars:
                continue
            indicators = compute_indicators(bars)

            candidate = Candidate(
                symbol=sym, price=current_price,
                prev_close=trade_record.entry_price,
                gap_pct=0, change_pct=0, volume=0, avg_volume=1,
                relative_volume=1, high=current_price, low=current_price,
                open_price=trade_record.entry_price,
            )

            for strategy in self.strategies:
                if strategy.name != trade_record.strategy:
                    continue
                signal = strategy.evaluate(candidate, bars, indicators, position)
                if signal and signal.action == SignalAction.EXIT:
                    self._execute_exit(sym, current_price, signal)

    def _reconcile_vanished_position(self, symbol: str, trade_record):
        """A tracked position is gone from the broker — almost always a bracket
        TP/SL leg filled. Journal it at the REAL fill price/reason instead of
        assuming the entry price (which would record $0 P&L)."""
        entry_side = "buy" if trade_record.direction == "long" else "sell"
        exit_price = trade_record.entry_price
        reason = "external_close"

        fill = None
        if not self.config.dry_run:
            try:
                fill = self.broker.last_filled_exit(
                    symbol, entry_side, after=self._entry_time_utc.get(symbol)
                )
            except Exception as exc:
                logger.warning(f"Exit reconciliation lookup failed for {symbol}: {exc}")
        if fill:
            exit_price = fill["price"]
            reason = fill["reason"]
        else:
            bars = self._get_bars(symbol)
            if bars:
                exit_price = float(bars[-1]["c"])  # last known price beats entry price

        record = self.journal.close_trade(symbol, exit_price, reason)
        self.risk.clear_symbol(symbol)
        if record:
            self.risk.record_pnl(record.pnl)
            if reason == "stop_loss":
                for s in self.strategies:
                    if s.name == record.strategy:
                        s.on_stop_loss(symbol)

    def _wait_orders_cleared(self, symbol: str, tries: int = 3, delay: float = 1.0) -> bool:
        """Poll until the symbol has no open orders. Alpaca cancellation is
        async, so we confirm the bracket legs are gone before liquidating —
        otherwise the close is rejected and the position is left unprotected."""
        for attempt in range(tries):
            if not self.broker.get_orders(status="open", symbols=[symbol]):
                return True
            if attempt < tries - 1:
                time.sleep(delay)
        return not self.broker.get_orders(status="open", symbols=[symbol])

    def _execute_exit(self, symbol: str, price: float, signal: Signal):
        logger.info(f"EXIT: {signal.strategy} → close {symbol} @ {price:.2f}: {signal.reason}")

        if self.config.dry_run:
            record = self.journal.close_trade(symbol, price, signal.reason)
            self.risk.clear_symbol(symbol)
            if record:
                self.risk.record_pnl(record.pnl)
            return

        # Commit to flattening this position. Once we cancel the protective
        # bracket legs the shares are exposed, so we must guarantee the close
        # completes — track it in _pending_close and retry every tick until the
        # broker confirms, rather than deferring into a naked position.
        self._pending_close.add(symbol)
        self._force_close(symbol, price, signal.reason)

    def _force_close(self, symbol: str, price: float, reason: str):
        """Cancel the symbol's bracket legs and liquidate. On success, journal
        and drop it from _pending_close; on failure, keep it for next-tick retry
        (the position may have already vanished via a leg fill, which the
        reconcile path then books)."""
        self.broker.cancel_orders_for_symbol(symbol)
        self._wait_orders_cleared(symbol)  # best effort; retry covers the rest
        result = self.broker.close_position(symbol)
        if result:
            record = self.journal.close_trade(symbol, price, reason)
            self.risk.clear_symbol(symbol)
            self._pending_close.discard(symbol)
            if record:
                self.risk.record_pnl(record.pnl)
        else:
            logger.error(f"close_position failed for {symbol}; will retry next tick")

    def _retry_pending_closes(self):
        if not self._pending_close:
            return
        position_map = {p["symbol"]: p for p in self.broker.get_positions()}
        for sym in list(self._pending_close):
            pos = position_map.get(sym)
            if pos is None:
                # Already gone (a leg filled): let reconciliation book it.
                self._pending_close.discard(sym)
                continue
            price = float(pos.get("current_price", pos.get("avg_entry_price", 0)))
            self._force_close(sym, price, "exit_retry")

    def _flatten_all(self, reason: str):
        """Issue an EOD account flatten (cancel all orders + liquidate). Async:
        positions don't vanish immediately, so journaling is left to
        _reconcile_vanished_position on this and subsequent ticks, which books
        each close at its real fill price. Issued once per day."""
        if self.config.dry_run or self._flatten_requested:
            return
        if self.broker.get_positions():
            logger.info(f"Flattening all positions: {reason}")
            self.broker.close_all_positions(cancel_orders=True)
            self._flatten_requested = True

    def _get_position_infos(self) -> List[PositionInfo]:
        positions = self.broker.get_positions()
        result = []
        for p in positions:
            try:
                result.append(PositionInfo(
                    symbol=p["symbol"],
                    qty=int(p.get("qty", 0)),
                    avg_entry=float(p.get("avg_entry_price", 0)),
                    current_price=float(p.get("current_price", 0)),
                    market_value=float(p.get("market_value", 0)),
                    unrealized_pnl=float(p.get("unrealized_pl", 0)),
                    unrealized_pnl_pct=float(p.get("unrealized_plpc", 0)),
                ))
            except Exception:
                pass
        return result

    def _shutdown(self):
        logger.info("Shutting down...")
        if self.config.risk.close_all_eod:
            self._flatten_requested = False  # allow a fresh flatten on shutdown
            self._flatten_all("shutdown")
            # No more ticks will run, so book the async liquidation here with a
            # bounded poll instead of relying on "subsequent ticks".
            if not self.config.dry_run:
                for _ in range(10):
                    self._check_exits(reconcile_only=True)
                    if not self.journal.open_trades:
                        break
                    time.sleep(1.5)
            else:
                self._check_exits(reconcile_only=True)
            # Backstop: never leave the daily record missing a close — book any
            # straggler at its last known price.
            for sym, rec in list(self.journal.open_trades.items()):
                bars = self._get_bars(sym)
                price = float(bars[-1]["c"]) if bars else rec.entry_price
                record = self.journal.close_trade(sym, price, "shutdown_close")
                self.risk.clear_symbol(sym)
                if record:
                    self.risk.record_pnl(record.pnl)
        self.journal.save_daily_csv()
        self.journal.save_daily_summary()
        logger.info("Engine stopped.")
