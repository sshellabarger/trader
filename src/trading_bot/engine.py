"""
Enhanced Trading Engine - COMPLETE Implementation
Critical fixes and improvements applied:

1. ✅ Actual entry logic in check_entries()
2. ✅ Order validation before every trade
3. ✅ Position monitoring integration
4. ✅ Proper exit logic with multiple strategies
5. ✅ Detailed logging and state tracking
6. ✅ Simulation mode support
7. ✅ Crypto/stock universe separation
8. ✅ Rate limit protection

USAGE:
Replace your existing engine.py with this file.
"""
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

from .broker_alpaca import AlpacaBroker
from .state import StateStore
from .position_monitor import PositionMonitor

# Import settings - handle different possible structures
try:
    from .settings import Settings
except ImportError:
    from . import settings as settings_module

    class Settings:
        """Wrapper for function-based settings"""
        def __init__(self):
            self._settings = settings_module

        def get(self, *args, **kwargs):
            if hasattr(self._settings, 'get'):
                return self._settings.get(*args, **kwargs)
            if args:
                key = args[0]
                if hasattr(self._settings, key):
                    return getattr(self._settings, key)
            return kwargs.get('default')

        def as_dict(self):
            """Convert settings to dictionary"""
            result = {}
            for attr in dir(self._settings):
                if not attr.startswith('_'):
                    val = getattr(self._settings, attr)
                    if isinstance(val, (dict, list, str, int, float, bool)):
                        result[attr] = val
            return result

# Import news
try:
    from .news import NewsManager
except ImportError:
    from . import news as news_module

    class NewsManager:
        def __init__(self, settings, logger):
            self.settings = settings
            self.logger = logger
            self._news_module = news_module

        def get_news_counts(self, symbols):
            if hasattr(self._news_module, 'get_news_counts'):
                return self._news_module.get_news_counts(symbols)
            return {}

# Import earnings
try:
    from .earnings import EarningsCalendar
except ImportError:
    from . import earnings as earnings_module

    class EarningsCalendar:
        def __init__(self, settings, logger):
            self.settings = settings
            self.logger = logger
            self._earnings_module = earnings_module

        def get_upcoming_earnings(self, days_ahead=7):
            if hasattr(self._earnings_module, 'get_upcoming_earnings'):
                return self._earnings_module.get_upcoming_earnings(days_ahead)
            return {}

from .universe import load_universe
from .strategy_configs import get_strategy_config, STRATEGY_CONFIGS
from .strategy_testing import StrategyType

# Import enhanced modules
try:
    from .risk_manager import RiskManager, Position as RiskPosition
    RISK_MANAGER_AVAILABLE = True
except ImportError:
    RISK_MANAGER_AVAILABLE = False
    logging.warning("Risk manager not available - using basic mode")

try:
    from .strategy_manager import StrategyManager, MarketRegime
    STRATEGY_MANAGER_AVAILABLE = True
except ImportError:
    STRATEGY_MANAGER_AVAILABLE = False
    logging.warning("Strategy manager not available - using basic mode")

try:
    from .order_validator import OrderValidator
    ORDER_VALIDATOR_AVAILABLE = True
except ImportError:
    ORDER_VALIDATOR_AVAILABLE = False
    logging.warning("Order validator not available - skipping validation")


def serialize_candidate(candidate):
    """Convert a candidate (CombinedSignal dataclass or dict) to JSON-serializable dict"""
    if hasattr(candidate, '__dataclass_fields__'):
        return {
            'symbol': candidate.symbol,
            'final_score': candidate.final_score,
            'confidence': candidate.confidence,
            'active_strategies': candidate.active_strategies,
            'regime': candidate.regime.value if hasattr(candidate.regime, 'value') else str(candidate.regime),
            'is_crypto': getattr(candidate, 'is_crypto', False),
            'signals': [
                {
                    'strategy_name': s.strategy_name,
                    'score': s.score,
                    'confidence': s.confidence,
                    'regime_match': s.regime_match,
                    'details': s.details
                }
                for s in candidate.signals
            ],
            'metadata': candidate.metadata
        }
    elif isinstance(candidate, dict):
        return candidate
    else:
        return {
            'symbol': str(candidate),
            'final_score': getattr(candidate, 'final_score', 0),
            'error': 'unknown_type'
        }


class Trader:
    """
    Enhanced trading engine with comprehensive risk management
    Compatible with existing codebase
    """

    def __init__(self, broker: AlpacaBroker, state: StateStore, settings, logger: logging.Logger):
        self.broker = broker
        self.state = state
        self.settings = settings
        self.logger = logger

        # Get settings as dict for managers
        if hasattr(settings, 'as_dict'):
            settings_dict = settings.as_dict()
        elif isinstance(settings, dict):
            settings_dict = settings
        else:
            settings_dict = {}
            for attr in dir(settings):
                if not attr.startswith('_'):
                    val = getattr(settings, attr)
                    if isinstance(val, (dict, list, str, int, float, bool)):
                        settings_dict[attr] = val

        # Initialize Position Monitor
        self.position_monitor = PositionMonitor(
            broker=self.broker,
            settings=settings_dict,
            logger=self.logger
        )
        self.logger.info("✓ Position monitor initialized")

        if RISK_MANAGER_AVAILABLE:
            self.risk_manager = RiskManager(settings_dict, logger)
            self.logger.info("✓ Risk manager enabled")
        else:
            self.risk_manager = None

        if STRATEGY_MANAGER_AVAILABLE:
            self.strategy_manager = StrategyManager(settings_dict, logger)
            self.logger.info("✓ Strategy manager enabled")
        else:
            self.strategy_manager = None

        if ORDER_VALIDATOR_AVAILABLE:
            self.order_validator = OrderValidator(settings_dict, logger)
            self.logger.info("✓ Order validator enabled")
        else:
            self.order_validator = None

        # Initialize existing managers
        self.news_manager = NewsManager(settings, logger)
        self.earnings_calendar = EarningsCalendar(settings, logger)

        # State tracking
        self.universe = []
        self.stock_universe = []
        self.crypto_universe = []
        self.forex_universe = []
        self.etf_universe = []
        self.candidates = []
        self.news_articles = []  # Changed from news_counts to news_articles
        self.earnings_data = {}

        self.last_candidate_refresh = None
        self.last_news_refresh = None
        self.last_earnings_refresh = None
        self.last_health_check = None

        self.market_open = False
        self.market_close_time = None

        self.running = False
        self.daily_initialized = False

        # Simulation mode flag
        self.simulation_mode = settings_dict.get('backtest', {}).get('simulation_mode', False)
        if self.simulation_mode:
            self.logger.warning("⚠️  SIMULATION MODE ENABLED - Orders will be logged but NOT executed")

    def _get_primary_strategy(self, candidate) -> StrategyType:
        """
        Determine the primary strategy for a candidate based on active strategies.
        Returns the strategy with the highest score from the candidate's signals.
        """
        # Map strategy names to StrategyType enum
        strategy_map = {
            'momentum': StrategyType.MOMENTUM,
            'mean_reversion': StrategyType.MEAN_REVERSION,
            'news': StrategyType.NEWS,
            'volume': StrategyType.VOLUME,
            'earnings': StrategyType.EARNINGS,
            'longterm_trend': StrategyType.LONGTERM_TREND,
            'longterm_momentum': StrategyType.LONGTERM_MOMENTUM,
            'crypto': StrategyType.CRYPTO,
            'forex': StrategyType.FOREX,
            'etf': StrategyType.ETF,
        }

        # If candidate has signals, find the highest scoring strategy
        if hasattr(candidate, 'signals') and candidate.signals:
            best_signal = max(candidate.signals, key=lambda s: s.score * s.confidence)
            strategy_name = best_signal.strategy_name
            return strategy_map.get(strategy_name, StrategyType.MOMENTUM)

        # Fallback: check active_strategies list
        if hasattr(candidate, 'active_strategies') and candidate.active_strategies:
            first_strategy = candidate.active_strategies[0]
            return strategy_map.get(first_strategy, StrategyType.MOMENTUM)

        # Fallback: classify by symbol type
        symbol = candidate.symbol if hasattr(candidate, 'symbol') else candidate.get('symbol', '')

        # Check if forex pair
        if symbol in self.forex_universe:
            return StrategyType.FOREX

        # Check if crypto
        if symbol in self.crypto_universe:
            return StrategyType.CRYPTO

        # Check if ETF
        if symbol in self.etf_universe:
            return StrategyType.ETF

        # Default to momentum for stocks
        return StrategyType.MOMENTUM

    def initialize(self):
        """Initialize engine and load data"""
        self.logger.info("Initializing trading engine...")

        # Load universe
        try:
            self.universe = load_universe(self.settings)

            # Classify symbols into different asset types
            # Common forex currency codes
            forex_currencies = ['EUR', 'GBP', 'USD', 'JPY', 'CHF', 'AUD', 'NZD', 'CAD']

            # Common crypto symbols
            crypto_symbols = ['BTC', 'ETH', 'SOL', 'AVAX', 'MATIC', 'LINK', 'UNI', 'AAVE', 'DOT', 'DOGE']

            # Common ETF symbols
            etf_symbols = ['SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VEA', 'VWO', 'AGG', 'LQD', 'TLT',
                          'GLD', 'SLV', 'XLE', 'XLF', 'XLV', 'XLI', 'XLK', 'XLP', 'XLU', 'XLY']

            for symbol in self.universe:
                # Check if forex pair (e.g., EUR/USD, GBP/JPY)
                if '/' in symbol:
                    parts = symbol.split('/')
                    if len(parts) == 2 and all(part in forex_currencies for part in parts):
                        self.forex_universe.append(symbol)
                        continue

                    # Check if crypto pair (e.g., BTC/USD, ETH/USD)
                    if parts[0] in crypto_symbols and parts[1] in ['USD', 'USDT', 'USDC']:
                        self.crypto_universe.append(symbol)
                        continue

                # Check if ETF
                if symbol in etf_symbols:
                    self.etf_universe.append(symbol)
                    continue

                # Check if crypto by symbol ending (e.g., BTCUSD)
                if any(symbol.startswith(crypto) and symbol.endswith('USD') for crypto in crypto_symbols):
                    self.crypto_universe.append(symbol)
                    continue

                # Default to stock
                self.stock_universe.append(symbol)

            self.logger.info(
                f"Loaded {len(self.universe)} symbols: "
                f"{len(self.stock_universe)} stocks, "
                f"{len(self.crypto_universe)} crypto, "
                f"{len(self.forex_universe)} forex, "
                f"{len(self.etf_universe)} ETFs"
            )
        except Exception as e:
            self.logger.error(f"Failed to load universe: {e}")
            self.universe = []

        # Check market status
        self._update_market_status()

        # Get account info
        account = self.broker.get_account()
        if account:
            equity = float(account.get('equity', 0))
            buying_power = float(account.get('buying_power', 0))
            self.logger.info(f"Account: ${equity:,.2f} equity, ${buying_power:,.2f} buying power")

            # Initialize risk manager daily metrics
            if self.risk_manager:
                self.risk_manager.reset_daily_metrics(equity)
                self.daily_initialized = True
        else:
            self.logger.error("Failed to get account info")

        # Start Position Monitor
        settings_dict = self.settings.as_dict() if hasattr(self.settings, 'as_dict') else {}
        monitor_enabled = settings_dict.get('risk', {}).get('position_monitor_enabled', True)

        if monitor_enabled:
            try:
                self.position_monitor.start()
                interval = settings_dict.get('risk', {}).get('position_monitor_interval_sec', 30)
                self.logger.info(f"✓ Position monitor started (checking every {interval}s)")
            except Exception as e:
                self.logger.error(f"Failed to start position monitor: {e}", exc_info=True)
        else:
            self.logger.warning("⚠️  Position monitor disabled in settings")

        self.logger.info("✓ Engine initialized")

    def _update_market_status(self):
        """Update market open/close status"""
        try:
            clock = self.broker.get_clock()
            self.market_open = clock.get('is_open', False)

            if not self.market_open:
                next_open = clock.get('next_open')
                if next_open:
                    self.logger.debug(f"Market closed. Opens: {next_open}")
            else:
                next_close = clock.get('next_close')
                if next_close:
                    self.market_close_time = datetime.fromisoformat(next_close.replace('Z', '+00:00'))
                    self.logger.debug(f"Market open. Closes: {next_close}")

        except Exception as e:
            self.logger.error(f"Error checking market status: {e}")
            self.market_open = False

    def _convert_positions(self, positions):
        """Convert broker positions to risk manager format"""
        if not RISK_MANAGER_AVAILABLE:
            return []

        converted = []
        for pos in positions:
            try:
                # Determine position side from qty (positive = long, negative = short)
                qty = int(pos.get('qty', 0))
                side = 'long' if qty > 0 else 'short'

                converted.append(RiskPosition(
                    symbol=pos.get('symbol', ''),
                    qty=abs(qty),  # Use absolute value
                    avg_entry_price=float(pos.get('avg_entry_price', 0)),
                    current_price=float(pos.get('current_price', 0)),
                    market_value=float(pos.get('market_value', 0)),
                    unrealized_pl=float(pos.get('unrealized_pl', 0)),
                    unrealized_plpc=float(pos.get('unrealized_plpc', 0)),
                    side=side
                ))
            except Exception as e:
                self.logger.warning(f"Error converting position {pos.get('symbol')}: {e}")
        return converted

    def check_exits(self):
        """
        ✅ ENHANCED: Exit checking now handled by PositionMonitor

        PositionMonitor now handles:
        - Dynamic trailing stop-loss orders (broker-level protection)
        - Take-profit execution via market orders
        - Daily portfolio stop-loss

        This method is kept for backward compatibility but logic moved to
        position_monitor.py for better risk management
        """
        # Exit logic now handled by PositionMonitor background thread
        # which provides:
        # - Broker-level stop orders (instant execution, works if bot crashes)
        # - Dynamic trailing stops (lock in profits as trade becomes profitable)
        # - Take-profit via market orders (flexible execution)
        pass

    def _close_position(self, symbol: str, qty: int, current_price: float, reason: str):
        """Close a position with proper logging (handles both long and short positions)"""
        try:
            # Determine position side from qty
            # Positive qty = long position (close with sell)
            # Negative qty = short position (close with buy)
            is_long = qty > 0
            close_side = 'sell' if is_long else 'buy'
            abs_qty = abs(qty)
            position_type = 'LONG' if is_long else 'SHORT'

            if self.simulation_mode:
                self.logger.info(
                    f"[SIMULATION] Would close {position_type} {symbol}: {abs_qty} shares @ ${current_price:.2f}, "
                    f"reason={reason}"
                )
                return

            order = self.broker.place_order(
                symbol=symbol,
                side=close_side,
                qty=abs_qty,
                order_type='market',
                time_in_force='day'
            )

            if order:
                self.logger.info(f"✓ Closed {position_type} {symbol}: {abs_qty} shares, reason={reason}")

                # Log to state
                self.state.add_event(
                    'exit',
                    f"Closed {symbol}: {qty} shares, reason={reason}",
                    details=json.dumps({
                        'price': current_price,
                        'reason': reason
                    })
                )
            else:
                self.logger.error(f"Failed to close {symbol}")

        except Exception as e:
            self.logger.error(f"Error closing position {symbol}: {e}", exc_info=True)

    def check_entries(self):
        """
        ✅ COMPLETE: Check candidates for entry signals
        Now includes full entry logic with validation
        """
        if not self.candidates:
            return

        try:
            # Get current positions
            positions = None
            if hasattr(self.broker, 'list_positions'):
                positions = self.broker.list_positions()
            elif hasattr(self.broker, 'get_positions'):
                positions = self.broker.get_positions()

            if positions is None:
                positions = []

            # Get account info
            account = self.broker.get_account()
            if not account:
                return

            equity = float(account.get('equity', 0))
            buying_power = float(account.get('buying_power', 0))

            # Check risk limits before entering trades
            if self.risk_manager:
                risk_positions = self._convert_positions(positions)
                metrics = self.risk_manager.calculate_risk_metrics(risk_positions, equity)

                if metrics.violations:
                    self.logger.warning(f"Risk violations present: {metrics.violations}")
                    return

            # Look for entry opportunities in top candidates
            max_new_positions = 3  # Maximum new positions per cycle
            new_positions_opened = 0

            for candidate in self.candidates[:20]:  # Check top 20 candidates
                if new_positions_opened >= max_new_positions:
                    break

                symbol = candidate.symbol if hasattr(candidate, 'symbol') else candidate.get('symbol')
                if not symbol:
                    continue

                # Skip if already have position
                if any(p.get('symbol') == symbol for p in positions):
                    continue

                # Determine primary strategy and get its config
                primary_strategy = self._get_primary_strategy(candidate)
                strategy_config = get_strategy_config(primary_strategy)
                entry_threshold = strategy_config.get('entry_threshold', 0.62)

                # Get the signal score
                score = candidate.final_score if hasattr(candidate, 'final_score') else candidate.get('score', 0)

                # Determine position side based on score
                # High scores (> 0.6) = LONG (bullish)
                # Low scores (< 0.4) = SHORT (bearish)
                # Mid scores (0.4-0.6) = No trade (neutral)
                if score >= 0.6:
                    position_side = 'long'
                    order_side = 'buy'
                elif score <= 0.4:
                    position_side = 'short'
                    order_side = 'sell'
                else:
                    self.logger.debug(f"Skipping {symbol}: neutral score {score:.3f}")
                    continue

                # Check if signal is strong enough
                if self.strategy_manager:
                    should_enter, reason = self.strategy_manager.get_entry_signal(
                        candidate,
                        entry_threshold=entry_threshold,
                        strategy_config=strategy_config
                    )

                    if not should_enter:
                        self.logger.debug(f"Skipping {symbol}: {reason}")
                        continue

                # Get current market data
                snapshot = self.broker.snapshot(symbol)
                if not snapshot:
                    self.logger.debug(f"No snapshot available for {symbol}")
                    continue

                current_price = snapshot.get('latestTrade', {}).get('p', 0)
                if current_price <= 0:
                    self.logger.debug(f"Invalid price for {symbol}: {current_price}")
                    continue

                # Calculate stop loss using strategy-specific config
                # Convert stop_loss_pct (percentage) to basis points for compatibility
                stop_loss_pct = strategy_config.get('stop_loss_pct', 0.5)
                stop_loss_bps = stop_loss_pct * 100  # Convert % to basis points

                # Calculate stop loss based on position side
                if self.strategy_manager and hasattr(candidate, 'regime'):
                    is_crypto = getattr(candidate, 'is_crypto', False)
                    stop_loss_price = self.strategy_manager.calculate_stop_loss(
                        current_price,
                        candidate.regime,
                        stop_loss_bps,
                        is_crypto,
                        strategy_config=strategy_config
                    )
                    # Adjust for shorts: stop should be above entry
                    if position_side == 'short':
                        distance = abs(current_price - stop_loss_price)
                        stop_loss_price = current_price + distance
                else:
                    # For longs: stop below entry
                    # For shorts: stop above entry
                    if position_side == 'long':
                        stop_loss_price = current_price * (1 - stop_loss_pct / 100)
                    else:  # short
                        stop_loss_price = current_price * (1 + stop_loss_pct / 100)

                # Calculate position size
                qty = 0
                if self.risk_manager:
                    qty, size_details = self.risk_manager.calculate_position_size(
                        symbol=symbol,
                        current_price=current_price,
                        stop_loss_price=stop_loss_price,
                        account_value=equity,
                        existing_positions=len(positions)
                    )

                    if qty < 1:
                        self.logger.debug(f"Position size too small for {symbol}")
                        continue
                else:
                    # Fallback: simple 2% of equity per position
                    position_value = equity * 0.02
                    qty = int(position_value / current_price)

                    if qty < 1:
                        continue

                # Validate order before placing
                if self.order_validator:
                    validation = self.order_validator.validate_order(
                        symbol=symbol,
                        side=order_side,  # 'buy' for longs, 'sell' for shorts
                        qty=qty,
                        order_type='market',
                        price=current_price,
                        account_info=account,
                        current_positions=positions,
                        market_data=snapshot
                    )

                    if not validation.is_valid:
                        self.logger.warning(
                            f"Order validation failed for {symbol}:\n"
                            f"Errors: {validation.errors}"
                        )
                        continue

                    if validation.warnings:
                        self.logger.info(f"Order warnings for {symbol}: {validation.warnings}")

                # Place order
                strategies = candidate.active_strategies if hasattr(candidate, 'active_strategies') else []

                self.logger.info(
                    f"ENTRY SIGNAL ({position_side.upper()}): {symbol} score={score:.3f}, qty={qty}, "
                    f"price=${current_price:.2f}, stop=${stop_loss_price:.2f}, "
                    f"primary_strategy={primary_strategy.value}, strategies={strategies}"
                )

                if self.simulation_mode:
                    self.logger.info(
                        f"[SIMULATION] Would {order_side} {symbol}: {qty} shares @ ${current_price:.2f} ({position_side})"
                    )
                    new_positions_opened += 1
                    continue

                order = self.broker.place_order(
                    symbol=symbol,
                    side=order_side,  # 'buy' for longs, 'sell' for shorts
                    qty=qty,
                    order_type='market',
                    time_in_force='day'
                )

                if order:
                    self.logger.info(f"✓ Order placed: {symbol}")
                    new_positions_opened += 1

                    if self.risk_manager:
                        self.risk_manager.increment_trade_count()

                    # Place protective stop-loss order
                    # For longs: stop is a sell order (close long)
                    # For shorts: stop is a buy order (cover short)
                    stop_order_side = 'sell' if position_side == 'long' else 'buy'
                    stop_order = self.broker.place_order(
                        symbol=symbol,
                        qty=qty,
                        side=stop_order_side,
                        order_type='stop',
                        stop_price=stop_loss_price,
                        time_in_force='gtc'  # Good til cancelled
                    )

                    stop_order_id = None
                    if stop_order:
                        stop_order_id = stop_order.get('id', '')
                        self.logger.info(
                            f"✓ Stop-loss placed: {symbol} @ ${stop_loss_price:.2f} "
                            f"(order_id: {stop_order_id})"
                        )
                    else:
                        self.logger.error(
                            f"⚠ WARNING: Failed to place stop-loss for {symbol}! "
                            f"Position is unprotected."
                        )

                    # Log the decision
                    regime = candidate.regime.value if hasattr(candidate, 'regime') and hasattr(candidate.regime, 'value') else 'unknown'

                    take_profit_pct = strategy_config.get('take_profit_pct', 2.0)

                    self.state.add_event(
                        'entry',
                        f"Entered {symbol}: {qty} shares @ ${current_price:.2f}",
                        details=json.dumps({
                            'score': score,
                            'strategies': strategies,
                            'primary_strategy': primary_strategy.value,
                            'regime': regime,
                            'stop_loss': stop_loss_price,
                            'stop_order_id': stop_order_id,
                            'take_profit_pct': take_profit_pct,
                            'order_id': order.get('id', '')
                        })
                    )

                    # Register position metadata with position monitor for dynamic stop management
                    if self.position_monitor:
                        self.position_monitor.register_position(
                            symbol=symbol,
                            primary_strategy=primary_strategy.value,
                            entry_price=current_price,
                            stop_loss_pct=stop_loss_pct,
                            take_profit_pct=take_profit_pct,
                            side=position_side  # 'long' or 'short'
                        )

                else:
                    self.logger.error(f"Failed to place order for {symbol}")

        except Exception as e:
            self.logger.error(f"Error checking entries: {e}", exc_info=True)

    def refresh_candidates(self):
        """
        ✅ ENHANCED: Refresh candidate list with rate limit protection
        """
        interval_min = self.settings.get('scheduling', {}).get('candidate_refresh_min', 30)

        if self.last_candidate_refresh:
            elapsed = (datetime.now() - self.last_candidate_refresh).total_seconds() / 60
            if elapsed < interval_min:
                return

        self.logger.info("Refreshing candidates...")

        try:
            # Get batch snapshots with rate limit protection
            batch_size = self.settings.get('scheduling', {}).get('candidate_max_symbols', 100)

            # Check which strategies are enabled
            crypto_enabled = self.settings.get('strategies', {}).get('crypto', False)
            forex_enabled = self.settings.get('strategies', {}).get('forex', False)
            etf_enabled = self.settings.get('strategies', {}).get('etf', False)

            # Build symbol list based on market hours and enabled strategies
            symbols = []

            if self.market_open:
                # During market hours: trade stocks, ETFs, and optionally crypto/forex
                symbols.extend(self.stock_universe[:batch_size])
                if etf_enabled and len(symbols) < batch_size:
                    symbols.extend(self.etf_universe[:max(20, batch_size - len(symbols))])
                if crypto_enabled and len(symbols) < batch_size:
                    symbols.extend(self.crypto_universe[:max(20, batch_size - len(symbols))])
                if forex_enabled and len(symbols) < batch_size:
                    symbols.extend(self.forex_universe[:max(20, batch_size - len(symbols))])
            else:
                # After hours: only trade crypto and forex if enabled (24/7 markets)
                if crypto_enabled:
                    symbols.extend(self.crypto_universe[:50])
                if forex_enabled:
                    symbols.extend(self.forex_universe[:50])

                # If no 24/7 strategies enabled, check stocks anyway (for pre-market data)
                if not symbols:
                    symbols = self.stock_universe[:batch_size]

            if not symbols:
                self.logger.warning("No symbols in universe")
                return

            # Get snapshots
            snapshots = self.broker.get_batch_snapshots(symbols)

            if not snapshots:
                self.logger.warning("No snapshots returned")
                return

            # Score candidates using strategy manager
            if self.strategy_manager:
                self.candidates = self.strategy_manager.rank_candidates(
                    snapshots,
                    self.news_articles,  # Changed from news_counts to news_articles
                    self.earnings_data
                )
            else:
                # Basic scoring fallback
                self.candidates = []
                for symbol, snap in snapshots.items():
                    daily_bar = snap.get('dailyBar', {})
                    if daily_bar:
                        self.candidates.append({
                            'symbol': symbol,
                            'score': 0.5,
                            'snapshot': snap
                        })

            # Sort by score
            self.candidates.sort(
                key=lambda x: x.final_score if hasattr(x, 'final_score') else x.get('score', 0),
                reverse=True
            )

            self.last_candidate_refresh = datetime.now()
            self.logger.info(f"Found {len(self.candidates)} candidates")

            # Save candidates to database for UI access
            try:
                from .state import set_candidates
                serializable_candidates = [serialize_candidate(c) for c in self.candidates]
                set_candidates(serializable_candidates)
                self.logger.info(f"✓ Saved {len(serializable_candidates)} candidates to database")
            except Exception as save_error:
                self.logger.error(f"Failed to save candidates to database: {save_error}", exc_info=True)

        except Exception as e:
            self.logger.error(f"Error refreshing candidates: {e}", exc_info=True)

    def refresh_news(self):
        """Refresh news data with sentiment analysis"""
        interval_s = self.settings.get('news', {}).get('news_interval_s', 1800)

        if self.last_news_refresh:
            elapsed = (datetime.now() - self.last_news_refresh).total_seconds()
            if elapsed < interval_s:
                return

        self.logger.info("Refreshing news with sentiment analysis...")

        try:
            symbols_to_check = self.universe[:60]
            window_hours = self.settings.get('news', {}).get('window_hours', 6)
            provider_order = self.settings.get('news', {}).get('provider_order', ['alpaca', 'finnhub', 'newsapi'])

            # Try to get news articles with sentiment
            if hasattr(self.news_manager, '_news_module') and hasattr(self.news_manager._news_module, 'get_news_articles'):
                self.news_articles = self.news_manager._news_module.get_news_articles(
                    symbols_to_check,
                    window_hours=window_hours,
                    provider_order=provider_order
                )
            else:
                # Fallback to empty list if not available
                self.news_articles = []

            self.last_news_refresh = datetime.now()

            # Calculate statistics
            total_articles = len(self.news_articles)
            if total_articles > 0:
                symbols_with_news = len(set(a.symbol for a in self.news_articles))
                avg_sentiment = sum(a.sentiment_score or 0 for a in self.news_articles) / total_articles
                positive_count = sum(1 for a in self.news_articles if (a.sentiment_score or 0) > 0.1)
                negative_count = sum(1 for a in self.news_articles if (a.sentiment_score or 0) < -0.1)

                self.logger.info(
                    f"Found {total_articles} articles across {symbols_with_news} symbols. "
                    f"Avg sentiment: {avg_sentiment:.3f}, "
                    f"Positive: {positive_count}, Negative: {negative_count}"
                )
            else:
                self.logger.info("No news articles found")

        except Exception as e:
            self.logger.error(f"Error refreshing news: {e}", exc_info=True)
            self.news_articles = []

    def refresh_earnings(self):
        """Refresh earnings calendar"""
        interval_min = self.settings.get('scheduling', {}).get('earnings_refresh_min', 120)

        if self.last_earnings_refresh:
            elapsed = (datetime.now() - self.last_earnings_refresh).total_seconds() / 60
            if elapsed < interval_min:
                return

        self.logger.info("Refreshing earnings calendar...")

        try:
            self.earnings_data = self.earnings_calendar.get_upcoming_earnings(days_ahead=7)
            self.last_earnings_refresh = datetime.now()

            self.logger.info(f"Found {len(self.earnings_data)} upcoming earnings")

        except Exception as e:
            self.logger.error(f"Error refreshing earnings: {e}", exc_info=True)

    def run_health_checks(self):
        """Run health checks"""
        interval_min = self.settings.get('scheduling', {}).get('health_refresh_min', 15)

        if self.last_health_check:
            elapsed = (datetime.now() - self.last_health_check).total_seconds() / 60
            if elapsed < interval_min:
                return

        self.logger.debug("Running health checks...")

        checks = {}

        # Clock check
        self._update_market_status()
        checks['clock'] = {
            'status': 'OK',
            'is_open': self.market_open,
            'last_check': datetime.now().isoformat()
        }

        # Account check
        try:
            account = self.broker.get_account()
        except AttributeError:
            account = None

        if account:
            checks['account'] = {
                'status': 'OK',
                'equity': float(account.get('equity', 0)),
                'buying_power': float(account.get('buying_power', 0)),
                'cash': float(account.get('cash', 0))
            }
        else:
            checks['account'] = {'status': 'ERROR'}

        # Position check
        try:
            positions = self.broker.list_positions()
        except AttributeError:
            try:
                positions = self.broker.get_positions()
            except AttributeError:
                positions = None

        if positions is not None:
            checks['positions'] = {
                'status': 'OK',
                'count': len(positions)
            }

            from .state import set_kv
            try:
                set_kv('positions', positions)
            except Exception as e:
                self.logger.warning(f"Failed to store positions for UI: {e}")
        else:
            checks['positions'] = {'status': 'ERROR'}

        # Position Monitor status
        if hasattr(self, 'position_monitor'):
            monitor_status = self.position_monitor.get_status()
            checks['position_monitor'] = {
                'status': 'OK' if monitor_status['running'] else 'STOPPED',
                'trading_halted': monitor_status['trading_halted'],
                'halt_reason': monitor_status['halt_reason'],
                'stop_loss_events_today': monitor_status['stop_loss_events_today']
            }

        # Risk metrics
        if self.risk_manager and account and positions:
            risk_positions = self._convert_positions(positions)
            metrics = self.risk_manager.calculate_risk_metrics(
                risk_positions,
                float(account.get('equity', 0))
            )
            checks['risk'] = {
                'status': metrics.risk_level.value.upper(),
                'exposure_pct': metrics.total_exposure_pct,
                'daily_pl_pct': metrics.daily_pl_pct,
                'violations': metrics.violations
            }

        # Store health data
        self.state.update_health('system', json.dumps(checks))
        self.last_health_check = datetime.now()

    def run(self):
        """Main trading loop"""
        self.logger.info("="*60)
        self.logger.info("STARTING ENHANCED TRADING ENGINE")
        if self.simulation_mode:
            self.logger.warning("⚠️  SIMULATION MODE - Orders will be logged but NOT executed")
        self.logger.info("="*60)

        try:
            self.initialize()
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}", exc_info=True)
            return

        self.running = True
        loop_count = 0

        self.logger.info("Entering main loop...")

        crypto_enabled = self.settings.get('strategies', {}).get('crypto', False)
        forex_enabled = self.settings.get('strategies', {}).get('forex', False)

        if crypto_enabled:
            self.logger.info("🪙 Crypto trading ENABLED - will trade 24/7")
        if forex_enabled:
            self.logger.info("💱 Forex trading ENABLED - will trade 24/5")

        try:
            while self.running:
                loop_count += 1

                if loop_count % 10 == 1:
                    self.logger.info(f"Main loop iteration {loop_count}, market_open={self.market_open}")

                try:
                    # Health checks (always run)
                    self.run_health_checks()

                    # Check if trading is halted by position monitor
                    if hasattr(self, 'position_monitor') and self.position_monitor.trading_halted:
                        self.logger.warning(
                            f"⛔ Trading halted by position monitor: {self.position_monitor.halt_reason}"
                        )
                        time.sleep(60)
                        continue

                    # Initialize daily metrics if new day and market opens
                    if self.market_open and not self.daily_initialized:
                        account = self.broker.get_account()
                        if account and self.risk_manager:
                            equity = float(account.get('equity', 0))
                            self.risk_manager.reset_daily_metrics(equity)
                            self.daily_initialized = True
                            self.logger.info("Daily metrics initialized")

                    # Determine if we should trade
                    # Trade if: market is open OR crypto is enabled OR forex is enabled
                    should_trade = self.market_open or crypto_enabled or forex_enabled

                    if should_trade:
                        if not self.market_open:
                            active_247_strategies = []
                            if crypto_enabled:
                                active_247_strategies.append("crypto")
                            if forex_enabled:
                                active_247_strategies.append("forex")
                            self.logger.debug(f"Market closed but 24/7 trading enabled - trading {', '.join(active_247_strategies)}")
                        else:
                            self.logger.debug("Market is open - checking trades")

                        # Check exits first (risk management priority)
                        self.check_exits()

                        # Refresh data
                        self.refresh_candidates()
                        self.refresh_news()
                        self.refresh_earnings()

                        # Check for new entries
                        self.check_entries()
                    else:
                        if loop_count % 20 == 1:
                            self.logger.info("Market is closed, waiting... (Enable crypto/forex in settings to trade 24/7)")
                        self.daily_initialized = False

                except Exception as e:
                    self.logger.error(f"Error in loop iteration {loop_count}: {e}", exc_info=True)

                # Sleep between loops
                time.sleep(30)

        except KeyboardInterrupt:
            self.logger.info("Shutting down gracefully...")
        except Exception as e:
            self.logger.error(f"Fatal error in main loop: {e}", exc_info=True)
        finally:
            if hasattr(self, 'position_monitor'):
                try:
                    self.position_monitor.stop()
                    self.logger.info("Position monitor stopped")
                except Exception as e:
                    self.logger.error(f"Error stopping position monitor: {e}")

            self.running = False
            self.logger.info("Engine stopped")