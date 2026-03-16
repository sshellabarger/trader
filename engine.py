"""
Trading Engine — ETF-focused.
No scanner needed. Trades QQQ/TQQQ directly with ORB + VWAP.
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
from .regime import RegimeDetector
from .risk import RiskManager, PositionInfo, SizeResult
from .scanner import Candidate
from .strategies import BaseStrategy, Signal, SignalAction, SignalDirection
from .strategies.orb import ORBStrategy
from .strategies.vwap_reversion import VWAPReversionStrategy

logger = logging.getLogger(__name__)


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

        minutes_left = self.broker.minutes_until_close()

        if self.risk.should_close_all(minutes_left):
            self._close_all_positions("end_of_day")
            return

        self._check_exits()

        if self.risk.should_stop_new_entries(minutes_left):
            return

        # Fetch bars and generate signals for our ETF symbols
        signals = self._generate_signals()

        for signal in signals:
            if signal.action == SignalAction.ENTER:
                self._execute_entry(signal)

    def _new_day(self, today: str):
        if self._today is not None:
            self.journal.save_daily_csv()
            self.journal.save_daily_summary()
            summary = self.journal.daily_summary()
            logger.info(f"Day ended — {summary}")

        self._today = today
        self._bars_cache.clear()
        self._indicators_cache.clear()

        equity = self.broker.get_equity()
        if equity > 0:
            self.risk.reset_daily(equity)

        # Detect regime
        try:
            spy_bars = self.broker.get_bars(
                self.primary, timeframe="1Day",
                start=(datetime.now() - timedelta(days=60)).isoformat(),
                limit=60,
            )
            regime = self.regime_detector.update_from_bars(spy_bars)
            for s in self.strategies:
                s.reset_daily()
                s.set_market_regime(regime)
            status = self.regime_detector.status()
            logger.info(f"Regime: {regime} ({self.primary}=${status['spy_price']:.2f})")
        except Exception as exc:
            logger.warning(f"Regime detection failed: {exc}")
            for s in self.strategies:
                s.reset_daily()
                s.set_market_regime("unknown")

        self.journal = TradeJournal()
        logger.info(f"New trading day: {today}")

    def _generate_signals(self) -> List[Signal]:
        signals: List[Signal] = []
        broker_positions = self.broker.get_positions()
        position_map = {p["symbol"]: p for p in broker_positions}

        # Use primary symbol for analysis (QQQ), but trade leveraged versions
        bars = self._get_bars(self.primary)
        if not bars or len(bars) < 10:
            return signals

        indicators = compute_indicators(bars)

        # Build a candidate from the primary ETF
        candidate = Candidate(
            symbol=self.primary,
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

        for strategy in self.strategies:
            # Check which symbols this strategy might trade
            for sym in self.symbols:
                position = position_map.get(sym)
                try:
                    signal = strategy.evaluate(candidate, bars, indicators, position)
                    if signal is not None:
                        signals.append(signal)
                except Exception as exc:
                    logger.error(f"Strategy {strategy.name} error: {exc}")

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals

    def _get_bars(self, symbol: str) -> List[Dict]:
        cached = self._bars_cache.get(symbol)
        if cached:
            return cached

        today = date.today()
        start = datetime(today.year, today.month, today.day, 4, 0).isoformat() + "-05:00"
        bars = self.broker.get_bars(symbol, timeframe="1Min", start=start, limit=500)
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
            self.risk.record_trade()
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
            for s in self.strategies:
                if s.name == signal.strategy:
                    s.on_fill(signal.symbol, signal)
            self.risk.record_trade()

    def _check_exits(self):
        if not self.journal.open_trades:
            return

        broker_positions = self.broker.get_positions()
        position_map = {p["symbol"]: p for p in broker_positions}

        for sym, trade_record in list(self.journal.open_trades.items()):
            position = position_map.get(sym)
            if position is None:
                self.journal.close_trade(sym, trade_record.entry_price, "external_close")
                self.risk.clear_symbol(sym)
                continue

            current_price = float(position.get("current_price", 0))
            if current_price <= 0:
                continue

            bars = self._get_bars(self.primary)
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

    def _execute_exit(self, symbol: str, price: float, signal: Signal):
        logger.info(f"EXIT: {signal.strategy} → close {symbol} @ {price:.2f}: {signal.reason}")

        if self.config.dry_run:
            record = self.journal.close_trade(symbol, price, signal.reason)
            self.risk.clear_symbol(symbol)
            if record:
                self.risk.record_trade(record.pnl)
            return

        result = self.broker.close_position(symbol)
        if result:
            record = self.journal.close_trade(symbol, price, signal.reason)
            self.risk.clear_symbol(symbol)
            if record:
                self.risk.record_trade(record.pnl)

    def _close_all_positions(self, reason: str):
        positions = self.broker.get_positions()
        if not positions:
            return

        logger.info(f"Closing all {len(positions)} positions: {reason}")
        for pos in positions:
            sym = pos["symbol"]
            price = float(pos.get("current_price", pos.get("avg_entry_price", 0)))
            if not self.config.dry_run:
                self.broker.close_position(sym)
            self.journal.close_trade(sym, price, reason)
            self.risk.clear_symbol(sym)

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
            self._close_all_positions("shutdown")
        self.journal.save_daily_csv()
        self.journal.save_daily_summary()
        logger.info("Engine stopped.")
