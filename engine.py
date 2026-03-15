"""
Trading Engine — the main loop.

Flow each cycle:
  1. Check market status (open / time to close)
  2. Scan for candidates
  3. Fetch intraday bars + compute indicators for each candidate
  4. Run each strategy → collect Signals
  5. Validate signals through risk manager
  6. Size positions
  7. Submit orders (or log in dry-run mode)
  8. Monitor open positions for trailing stops / exits
  9. End-of-day: close all, save journal
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from .broker import AlpacaBroker
from .config import Config
from .indicators import compute_indicators
from .journal import TradeJournal
from .risk import RiskManager, PositionInfo, SizeResult
from .scanner import Candidate, scan_candidates, filter_gap_candidates, filter_orb_candidates, filter_vwap_candidates
from .strategies import BaseStrategy, Signal, SignalAction, SignalDirection
from .strategies.orb import ORBStrategy
from .strategies.vwap_reversion import VWAPReversionStrategy
from .strategies.gap_and_go import GapAndGoStrategy

logger = logging.getLogger(__name__)


class Engine:
    """Main trading engine."""

    def __init__(self, config: Config, universe: Optional[List[str]] = None):
        self.config = config
        self.broker = AlpacaBroker(config.broker)
        self.risk = RiskManager(config.risk)
        self.journal = TradeJournal()

        # Universe of symbols to scan
        self.universe = universe or ["AAPL", "MSFT", "NVDA", "TSLA", "AMD",
                                      "META", "AMZN", "GOOGL", "NFLX", "SPY"]

        # Initialize strategies
        self.strategies: List[BaseStrategy] = []
        if config.strategy.orb_enabled:
            self.strategies.append(ORBStrategy(config))
        if config.strategy.vwap_enabled:
            self.strategies.append(VWAPReversionStrategy(config))
        if config.strategy.gap_enabled:
            self.strategies.append(GapAndGoStrategy(config))

        logger.info(f"Engine initialized with {len(self.strategies)} strategies: "
                     f"{[s.name for s in self.strategies]}")

        # State
        self._running = False
        self._today: Optional[str] = None
        self._bars_cache: Dict[str, List[Dict]] = {}  # symbol → bars
        self._indicators_cache: Dict[str, Dict] = {}
        self._last_scan_time: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, loop_interval: int = 30):
        """Start the main trading loop."""
        logger.info("=" * 60)
        logger.info("STARTING DAYTRADER ENGINE v2")
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

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _initialize(self):
        """Pre-market initialization."""
        # Account check
        acct = self.broker.get_account()
        if acct:
            equity = float(acct.get("equity", 0))
            bp = float(acct.get("buying_power", 0))
            logger.info(f"Account: equity=${equity:,.2f}  buying_power=${bp:,.2f}")
            self.risk.reset_daily(equity)
        else:
            logger.error("Failed to connect to broker — check API keys")
            self._running = False
            return

        logger.info(f"Universe: {len(self.universe)} symbols")
        logger.info(f"Mode: {'DRY RUN' if self.config.dry_run else 'LIVE'}")
        logger.info(f"Paper: {self.config.paper_trading}")

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def _tick(self):
        """One iteration of the main loop."""

        # New day check
        today = date.today().isoformat()
        if self._today != today:
            self._new_day(today)

        # Market status
        if not self.broker.is_market_open():
            return  # wait for market

        minutes_left = self.broker.minutes_until_close()

        # End-of-day close
        if self.risk.should_close_all(minutes_left):
            self._close_all_positions("end_of_day")
            return

        # Check exits on existing positions first (risk priority)
        self._check_exits()

        # Stop new entries if too close to close
        if self.risk.should_stop_new_entries(minutes_left):
            return

        # Scan for candidates (throttled)
        candidates = self._scan()
        if not candidates:
            return

        # Evaluate strategies on each candidate
        signals = self._generate_signals(candidates)

        # Validate and execute
        for signal in signals:
            if signal.action == SignalAction.ENTER:
                self._execute_entry(signal)

    # ------------------------------------------------------------------
    # New day
    # ------------------------------------------------------------------

    def _new_day(self, today: str):
        """Handle day transition."""
        if self._today is not None:
            # Save previous day
            self.journal.save_daily_csv()
            self.journal.save_daily_summary()
            summary = self.journal.daily_summary()
            logger.info(f"Day ended — {summary}")

        self._today = today
        self._bars_cache.clear()
        self._indicators_cache.clear()
        self._last_scan_time = None

        # Reset daily risk
        equity = self.broker.get_equity()
        if equity > 0:
            self.risk.reset_daily(equity)

        # Fresh journal
        self.journal = TradeJournal()
        logger.info(f"New trading day: {today}")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def _scan(self) -> List[Candidate]:
        """Scan for candidates, throttled to avoid over-polling."""
        now = datetime.now()
        if self._last_scan_time and (now - self._last_scan_time).total_seconds() < 60:
            return []  # scan at most once per minute

        candidates = scan_candidates(self.broker, self.universe, self.config.scanner)
        self._last_scan_time = now

        if candidates:
            top_syms = [c.symbol for c in candidates[:5]]
            logger.info(f"Scan: {len(candidates)} candidates, top: {top_syms}")

        return candidates

    # ------------------------------------------------------------------
    # Signal generation
    # ------------------------------------------------------------------

    def _generate_signals(self, candidates: List[Candidate]) -> List[Signal]:
        """Run all strategies on all candidates, collect entry signals."""
        signals: List[Signal] = []

        # Get existing positions
        broker_positions = self.broker.get_positions()
        position_map = {p["symbol"]: p for p in broker_positions}

        for candidate in candidates:
            sym = candidate.symbol

            # Fetch bars and compute indicators (with caching)
            bars = self._get_bars(sym)
            if not bars or len(bars) < 20:
                continue

            indicators = compute_indicators(bars)
            self._indicators_cache[sym] = indicators

            position = position_map.get(sym)

            for strategy in self.strategies:
                try:
                    signal = strategy.evaluate(candidate, bars, indicators, position)
                    if signal is not None:
                        signals.append(signal)
                except Exception as exc:
                    logger.error(f"Strategy {strategy.name} error on {sym}: {exc}")

        # Sort by strength, take best signals first
        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals

    def _get_bars(self, symbol: str) -> List[Dict]:
        """Fetch intraday bars, with simple caching."""
        cached = self._bars_cache.get(symbol)
        if cached and len(cached) > 0:
            # Refresh if stale (more than 2 minutes old)
            # For now just return cache; engine tick interval handles freshness
            return cached

        today = date.today()
        start = datetime(today.year, today.month, today.day, 4, 0).isoformat() + "-05:00"

        bars = self.broker.get_bars(symbol, timeframe="1Min", start=start, limit=500)
        if bars:
            self._bars_cache[symbol] = bars
        return bars

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute_entry(self, signal: Signal):
        """Validate, size, and submit an entry order."""
        sym = signal.symbol

        # Get account state
        acct = self.broker.get_account()
        if not acct:
            return
        equity = float(acct.get("equity", 0))
        buying_power = float(acct.get("buying_power", 0))

        # Get current positions
        positions = self._get_position_infos()

        # Risk validation
        ok, reason = self.risk.validate_entry(signal, equity, buying_power, positions)
        if not ok:
            logger.debug(f"Entry blocked for {sym}: {reason}")
            return

        # Position sizing
        size = self.risk.calculate_position_size(
            signal, equity, buying_power, len(positions)
        )
        if size.shares < 1:
            logger.debug(f"Position too small for {sym}: {size.limited_by}")
            return

        logger.info(
            f"ENTRY SIGNAL: {signal.strategy} → {signal.direction.value.upper()} "
            f"{size.shares} {sym} @ {signal.entry_price:.2f} "
            f"(SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f} "
            f"strength={signal.strength:.2f} R:R={signal.risk_reward or 0:.1f})"
        )

        if self.config.dry_run:
            logger.info(f"DRY RUN — would submit order for {size.shares} {sym}")
            # Still record in journal for analysis
            self.journal.open_trade(
                sym, signal.strategy, signal.direction.value, size.shares,
                signal.entry_price, signal.stop_loss, signal.take_profit,
                signal.reason, signal.indicators,
            )
            # Notify strategy
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(sym, signal)
            self.risk.record_trade()
            return

        # Submit bracket order (entry + stop + target)
        order = self.broker.submit_bracket_order(
            symbol=sym,
            qty=size.shares,
            side="buy" if signal.direction == SignalDirection.LONG else "sell",
            take_profit_price=signal.take_profit,
            stop_loss_price=signal.stop_loss,
        )

        if order:
            logger.info(f"Order submitted: {order.get('id', 'unknown')}")
            self.journal.open_trade(
                sym, signal.strategy, signal.direction.value, size.shares,
                signal.entry_price, signal.stop_loss, signal.take_profit,
                signal.reason, signal.indicators,
            )
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(sym, signal)
            self.risk.record_trade()
        else:
            logger.error(f"Order submission failed for {sym}")

    # ------------------------------------------------------------------
    # Exit management
    # ------------------------------------------------------------------

    def _check_exits(self):
        """Check open positions for exit signals from strategies."""
        if not self.journal.open_trades:
            return

        broker_positions = self.broker.get_positions()
        position_map = {p["symbol"]: p for p in broker_positions}

        for sym, trade_record in list(self.journal.open_trades.items()):
            position = position_map.get(sym)
            if position is None:
                # Position was closed externally (stop hit, etc.)
                exit_price = trade_record.entry_price  # best guess
                self.journal.close_trade(sym, exit_price, "external_close")
                for s in self.strategies:
                    if s.name == trade_record.strategy:
                        exit_signal = Signal(
                            symbol=sym, strategy=s.name,
                            action=SignalAction.EXIT,
                            direction=SignalDirection.FLAT, strength=1.0
                        )
                        s.on_fill(sym, exit_signal)
                self.risk.clear_symbol(sym)
                continue

            current_price = float(position.get("current_price", 0))
            if current_price <= 0:
                continue

            # Update trailing stop
            atr_val = self._indicators_cache.get(sym, {}).get("atr_14")
            self.risk.update_trailing_stop(sym, current_price, atr_val)

            # Get bars/indicators for strategy exit evaluation
            bars = self._get_bars(sym)
            if not bars:
                continue
            indicators = compute_indicators(bars)

            # Create a minimal candidate for the strategy
            candidate = Candidate(
                symbol=sym, price=current_price,
                prev_close=float(position.get("avg_entry_price", current_price)),
                gap_pct=0, change_pct=0, volume=0, avg_volume=1,
                relative_volume=1, high=current_price, low=current_price,
                open_price=current_price,
            )

            for strategy in self.strategies:
                if strategy.name != trade_record.strategy:
                    continue
                try:
                    signal = strategy.evaluate(candidate, bars, indicators, position)
                    if signal and signal.action == SignalAction.EXIT:
                        self._execute_exit(sym, current_price, signal)
                except Exception as exc:
                    logger.error(f"Exit check error {strategy.name}/{sym}: {exc}")

    def _execute_exit(self, symbol: str, price: float, signal: Signal):
        """Close a position."""
        logger.info(f"EXIT SIGNAL: {signal.strategy} → close {symbol} @ {price:.2f}: {signal.reason}")

        if self.config.dry_run:
            logger.info(f"DRY RUN — would close {symbol}")
            record = self.journal.close_trade(symbol, price, signal.reason)
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(symbol, signal)
            self.risk.clear_symbol(symbol)
            if record:
                self.risk.record_trade(record.pnl)
            return

        result = self.broker.close_position(symbol)
        if result:
            record = self.journal.close_trade(symbol, price, signal.reason)
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(symbol, signal)
            self.risk.clear_symbol(symbol)
            if record:
                self.risk.record_trade(record.pnl)
        else:
            logger.error(f"Failed to close position {symbol}")

    def _close_all_positions(self, reason: str):
        """Close all open positions."""
        positions = self.broker.get_positions()
        if not positions:
            return

        logger.info(f"Closing all {len(positions)} positions: {reason}")

        for pos in positions:
            sym = pos["symbol"]
            price = float(pos.get("current_price", pos.get("avg_entry_price", 0)))
            if self.config.dry_run:
                logger.info(f"DRY RUN — would close {sym}")
            else:
                self.broker.close_position(sym)

            self.journal.close_trade(sym, price, reason)
            self.risk.clear_symbol(sym)
            for s in self.strategies:
                exit_signal = Signal(
                    symbol=sym, strategy=s.name,
                    action=SignalAction.EXIT,
                    direction=SignalDirection.FLAT, strength=1.0,
                )
                s.on_fill(sym, exit_signal)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_position_infos(self) -> List[PositionInfo]:
        """Convert broker positions to PositionInfo objects."""
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
            except Exception as exc:
                logger.warning(f"Error converting position {p.get('symbol')}: {exc}")
        return result

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down engine...")

        if self.config.risk.close_all_eod:
            self._close_all_positions("shutdown")

        self.journal.save_daily_csv()
        self.journal.save_daily_summary()
        summary = self.journal.daily_summary()
        logger.info(f"Final daily summary: {summary}")
        logger.info("Engine stopped.")
