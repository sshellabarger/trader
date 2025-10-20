"""
Enhanced Trading Engine - Complete Version
Drop-in replacement for existing engine.py with full risk management
"""
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

from .broker_alpaca import AlpacaBroker
from .state import StateStore  # Now available from our new state.py

# Import settings - handle different possible structures
try:
    from .settings import Settings
except ImportError:
    # If Settings class doesn't exist, create a wrapper
    from . import settings as settings_module

    class Settings:
        """Wrapper for function-based settings"""
        def __init__(self):
            self._settings = settings_module

        def get(self, *args, **kwargs):
            if hasattr(self._settings, 'get'):
                return self._settings.get(*args, **kwargs)
            # Fallback to module-level dictionaries
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

# Import news - handle different possible structures
try:
    from .news import NewsManager
except ImportError:
    from . import news as news_module

    class NewsManager:
        """Wrapper for function-based news"""
        def __init__(self, settings, logger):
            self.settings = settings
            self.logger = logger
            self._news_module = news_module

        def get_news_counts(self, symbols):
            """Get news counts for symbols"""
            if hasattr(self._news_module, 'get_news_counts'):
                return self._news_module.get_news_counts(symbols)
            # Return empty dict if not available
            return {}

# Import earnings - handle different possible structures
try:
    from .earnings import EarningsCalendar
except ImportError:
    from . import earnings as earnings_module

    class EarningsCalendar:
        """Wrapper for function-based earnings"""
        def __init__(self, settings, logger):
            self.settings = settings
            self.logger = logger
            self._earnings_module = earnings_module

        def get_upcoming_earnings(self, days_ahead=7):
            """Get upcoming earnings"""
            if hasattr(self._earnings_module, 'get_upcoming_earnings'):
                return self._earnings_module.get_upcoming_earnings(days_ahead)
            # Return empty dict if not available
            return {}

from .universe import load_universe

# Import enhanced modules (these will be created separately)
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
            # Try to convert module to dict
            settings_dict = {}
            for attr in dir(settings):
                if not attr.startswith('_'):
                    val = getattr(settings, attr)
                    if isinstance(val, (dict, list, str, int, float, bool)):
                        settings_dict[attr] = val

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
        self.candidates = []
        self.news_counts = {}
        self.earnings_data = {}

        self.last_candidate_refresh = None
        self.last_news_refresh = None
        self.last_earnings_refresh = None
        self.last_health_check = None

        self.market_open = False
        self.market_close_time = None

        self.running = False
        self.daily_initialized = False

    def initialize(self):
        """Initialize engine and load data"""
        self.logger.info("Initializing trading engine...")

        # Log available broker methods for debugging
        snapshot_methods = [m for m in dir(self.broker) if 'snapshot' in m.lower()]
        self.logger.debug(f"Broker snapshot methods: {snapshot_methods}")

        # Load universe
        try:
            self.universe = load_universe(self.settings)
            self.logger.info(f"Loaded {len(self.universe)} symbols")
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

        self.logger.info("✓ Engine initialized")

    def _update_market_status(self):
        """Update market open/close status"""
        clock = self.broker.get_clock()
        if clock:
            self.market_open = clock.get('is_open', False)
            if 'next_close' in clock:
                try:
                    self.market_close_time = datetime.fromisoformat(
                        clock['next_close'].replace('Z', '+00:00')
                    ).replace(tzinfo=None)
                except:
                    self.market_close_time = None

            self.logger.debug(f"Market {'OPEN' if self.market_open else 'CLOSED'}")
        else:
            self.logger.warning("Failed to get market clock")

    def refresh_candidates(self):
        """Refresh candidate list with rankings"""
        interval_min = self.settings.get('scheduling', {}).get('candidate_refresh_min', 20)

        if self.last_candidate_refresh:
            elapsed = (datetime.now() - self.last_candidate_refresh).total_seconds() / 60
            if elapsed < interval_min:
                return

        self.logger.info("Refreshing candidates...")

        # Determine which universe to use
        crypto_enabled = self.settings.get('strategies', {}).get('crypto', False)

        if crypto_enabled:
            # Get crypto universe
            crypto_universe = self.settings.get('crypto', {}).get('universe', ['BTC/USD', 'ETH/USD'])
            symbols_subset = self.universe[:100] + crypto_universe  # Mix stocks and crypto
            self.logger.info(f"Including {len(crypto_universe)} crypto symbols")
        else:
            # Get batch snapshots for stocks only
            max_symbols = self.settings.get('scheduling', {}).get('candidate_max_symbols', 150)
            symbols_subset = self.universe[:max_symbols]

        try:
            # Use correct broker method name
            snapshots = None

            if hasattr(self.broker, 'get_batch_snapshots'):
                snapshots = self.broker.get_batch_snapshots(symbols_subset)
            elif hasattr(self.broker, 'get_snapshots'):
                snapshots = self.broker.get_snapshots(symbols_subset)
            elif hasattr(self.broker, 'batch_snapshots'):
                snapshots = self.broker.batch_snapshots(symbols_subset)
            elif hasattr(self.broker, 'snapshots'):
                snapshots = self.broker.snapshots(symbols_subset)
            else:
                self.logger.error("Broker has no snapshot method available")
                self.logger.info("Available methods: " +
                               ', '.join([m for m in dir(self.broker) if not m.startswith('_')]))
                # Fall back to basic mode without snapshots
                self.candidates = []
                self.last_candidate_refresh = datetime.now()
                return

            if not snapshots:
                self.logger.warning("No snapshots returned")
                return

            self.logger.debug(f"Got {len(snapshots)} snapshots")

            # Rank using strategy manager if available
            if self.strategy_manager:
                candidates = self.strategy_manager.rank_candidates(
                    snapshots,
                    self.news_counts,
                    self.earnings_data,
                    min_score=0.5
                )
                self.candidates = candidates[:20]  # Top 20

                if self.candidates:
                    self.logger.info(
                        f"Ranked {len(self.candidates)} candidates, "
                        f"top: {self.candidates[0].symbol} ({self.candidates[0].final_score:.3f})"
                    )

                # Store for UI
                import json
                from .state import set_kv
                candidates_data = [
                    {
                        'symbol': c.symbol,
                        'final_score': c.final_score,
                        'confidence': c.confidence,
                        'regime': c.regime.value if hasattr(c.regime, 'value') else str(c.regime),
                        'active_strategies': c.active_strategies,
                        'metadata': c.metadata
                    }
                    for c in self.candidates
                ]
                set_kv('candidates', candidates_data)
            else:
                # Fallback to basic ranking
                self.candidates = self._basic_ranking(snapshots)

            self.last_candidate_refresh = datetime.now()

        except Exception as e:
            self.logger.error(f"Error refreshing candidates: {e}", exc_info=True)

    def _basic_ranking(self, snapshots: Dict) -> List:
        """Basic ranking when strategy manager not available"""
        from .strategies import score_momentum

        ranked = []
        for symbol, snapshot in snapshots.items():
            try:
                score = score_momentum(snapshot, self.settings)
                if score > 0.5:
                    ranked.append({'symbol': symbol, 'score': score, 'snapshot': snapshot})
            except:
                continue

        ranked.sort(key=lambda x: x['score'], reverse=True)
        return ranked[:20]

    def check_entries(self):
        """Check for entry opportunities"""
        if not self.market_open:
            return

        if not self.candidates:
            self.logger.debug("No candidates to check")
            return

        # Get current state - use correct broker method names
        try:
            positions = self.broker.list_positions() or []
        except AttributeError:
            positions = self.broker.get_positions() or []

        try:
            account = self.broker.get_account()
        except AttributeError:
            account = None

        if not account:
            self.logger.error("Failed to get account info")
            return

        account_value = float(account.get('equity', 0))
        buying_power = float(account.get('buying_power', 0))

        # Convert positions for risk manager
        risk_positions = self._convert_positions(positions)

        # Check entry threshold
        entry_threshold = self.settings.get('thresholds', {}).get('enter', 0.62)

        # Check top candidates
        for candidate in self.candidates[:5]:
            try:
                # Skip if already have position
                if self.strategy_manager:
                    symbol = candidate.symbol
                    score = candidate.final_score
                else:
                    symbol = candidate['symbol']
                    score = candidate['score']

                if any(p.get('symbol') == symbol for p in positions):
                    continue

                if score < entry_threshold:
                    continue

                # Get current price
                if self.strategy_manager:
                    current_price = candidate.metadata.get('current_price', 0)
                    regime = candidate.regime
                else:
                    snapshot = candidate.get('snapshot', {})
                    current_price = snapshot.get('latestTrade', {}).get('p', 0)
                    regime = None

                if current_price <= 0:
                    continue

                # Calculate stop loss
                if self.strategy_manager and regime:
                    stop_loss = self.strategy_manager.calculate_stop_loss(
                        current_price,
                        regime,
                        base_stop_bps=self.settings.get('thresholds', {}).get('trade_stop_loss_bps', 50)
                    )
                else:
                    # Basic stop loss
                    stop_loss = current_price * 0.995

                # Calculate position size
                if self.risk_manager:
                    qty, size_details = self.risk_manager.calculate_position_size(
                        symbol,
                        current_price,
                        stop_loss,
                        account_value,
                        len(risk_positions)
                    )
                else:
                    # Basic position sizing - 2% of account
                    position_value = account_value * 0.02
                    qty = int(position_value / current_price)

                if qty < 1:
                    continue

                # Validate order
                if self.order_validator:
                    result = self.order_validator.validate_order(
                        symbol=symbol,
                        side='buy',
                        qty=qty,
                        order_type='market',
                        price=current_price,
                        account_info=account,
                        current_positions=positions
                    )

                    if not result.is_valid:
                        self.logger.warning(f"Order validation failed for {symbol}: {result.errors}")
                        continue

                # Place order
                self.logger.info(
                    f"BUY SIGNAL: {symbol} qty={qty} @ ${current_price:.2f} "
                    f"(score={score:.3f}, stop=${stop_loss:.2f})"
                )

                order = self.broker.place_order(
                    symbol=symbol,
                    qty=qty,
                    side='buy',
                    order_type='market'
                )

                if order:
                    self.logger.info(f"✓ Order placed: {order.get('id')}")

                    # Record trade
                    self.state.record_trade(
                        symbol=symbol,
                        side='buy',
                        qty=qty,
                        price=current_price,
                        order_id=order.get('id', ''),
                        strategy='enhanced',
                        details=json.dumps({
                            'score': score,
                            'stop_loss': stop_loss,
                            'entry_threshold': entry_threshold
                        })
                    )

                    # Increment trade count
                    if self.risk_manager:
                        self.risk_manager.increment_trade_count()
                else:
                    self.logger.error(f"Failed to place order for {symbol}")

            except Exception as e:
                self.logger.error(f"Error checking entry for candidate: {e}", exc_info=True)

    def check_exits(self):
        """Check all positions for exit conditions"""
        if not self.market_open:
            return

        try:
            positions = self.broker.list_positions() or []
        except AttributeError:
            positions = self.broker.get_positions() or []

        if not positions:
            return

        try:
            account = self.broker.get_account()
        except AttributeError:
            account = None
        if not account:
            return

        account_value = float(account.get('equity', 0))

        # Convert positions
        risk_positions = self._convert_positions(positions)

        # Check stop losses and exits
        to_close = []

        if self.risk_manager:
            # Use risk manager for comprehensive checks
            to_close.extend(self.risk_manager.check_stop_losses(risk_positions, account_value))

            take_profit_pct = self.settings.get('thresholds', {}).get('take_profit_pct', 2.0)
            to_close.extend(self.risk_manager.check_take_profit(risk_positions, take_profit_pct))

            # Check end of day
            if self.market_close_time and self.risk_manager.should_close_eod(self.market_close_time):
                self.logger.warning("Closing all positions - end of day approaching")
                for pos in risk_positions:
                    to_close.append((pos.symbol, 'eod_close', {}))
        else:
            # Basic exit logic
            for pos in positions:
                symbol = pos.get('symbol')
                unrealized_plpc = float(pos.get('unrealized_plpc', 0))

                # Stop loss at -0.5%
                if unrealized_plpc <= -0.5:
                    to_close.append((symbol, 'stop_loss', {'pl_pct': unrealized_plpc}))
                # Take profit at +2%
                elif unrealized_plpc >= 2.0:
                    to_close.append((symbol, 'take_profit', {'pl_pct': unrealized_plpc}))

        # Execute exits
        for symbol, reason, details in to_close:
            try:
                pos = next((p for p in positions if p.get('symbol') == symbol), None)
                if not pos:
                    continue

                qty = abs(float(pos.get('qty', 0)))
                current_price = float(pos.get('current_price', 0))

                self.logger.info(f"EXIT SIGNAL: {symbol} reason={reason} qty={qty}")

                order = self.broker.place_order(
                    symbol=symbol,
                    qty=qty,
                    side='sell',
                    order_type='market'
                )

                if order:
                    self.logger.info(f"✓ Exit order placed: {order.get('id')}")

                    # Record trade
                    self.state.record_trade(
                        symbol=symbol,
                        side='sell',
                        qty=qty,
                        price=current_price,
                        order_id=order.get('id', ''),
                        strategy='exit',
                        details=json.dumps({
                            'reason': reason,
                            'details': details
                        })
                    )

                    # Clear from position highs
                    if self.risk_manager and hasattr(self.risk_manager, 'position_highs'):
                        self.risk_manager.position_highs.pop(symbol, None)

            except Exception as e:
                self.logger.error(f"Error closing {symbol}: {e}", exc_info=True)

    def _convert_positions(self, positions: List[Dict]) -> List:
        """Convert broker positions to risk manager format"""
        if not RISK_MANAGER_AVAILABLE:
            return []

        risk_positions = []
        for pos in positions:
            try:
                risk_positions.append(RiskPosition(
                    symbol=pos.get('symbol'),
                    qty=float(pos.get('qty', 0)),
                    avg_entry_price=float(pos.get('avg_entry_price', 0)),
                    current_price=float(pos.get('current_price', 0)),
                    market_value=float(pos.get('market_value', 0)),
                    unrealized_pl=float(pos.get('unrealized_pl', 0)),
                    unrealized_plpc=float(pos.get('unrealized_plpc', 0))
                ))
            except Exception as e:
                self.logger.error(f"Error converting position: {e}")

        return risk_positions

    def refresh_news(self):
        """Refresh news data"""
        interval_s = self.settings.get('news', {}).get('news_interval_s', 1200)

        if self.last_news_refresh:
            elapsed = (datetime.now() - self.last_news_refresh).total_seconds()
            if elapsed < interval_s:
                return

        self.logger.info("Refreshing news...")

        try:
            # Get news for subset of symbols
            symbols_to_check = self.universe[:60]  # Batch of 60

            # Check if news_manager has the right method signature
            if hasattr(self.news_manager, 'get_news_counts'):
                try:
                    # Try with just symbols
                    self.news_counts = self.news_manager.get_news_counts(symbols_to_check)
                except TypeError:
                    # Need additional parameters
                    window_hours = self.settings.get('news', {}).get('window_hours', 6)
                    provider_order = self.settings.get('news', {}).get('provider_order', ['alpaca', 'finnhub', 'newsapi'])
                    self.news_counts = self.news_manager._news_module.get_news_counts(
                        symbols_to_check,
                        window_hours=window_hours,
                        provider_order=provider_order
                    )
            else:
                self.news_counts = {}

            self.last_news_refresh = datetime.now()

            total_articles = sum(self.news_counts.values())
            self.logger.info(f"Found {total_articles} articles across {len(self.news_counts)} symbols")

        except Exception as e:
            self.logger.error(f"Error refreshing news: {e}", exc_info=True)
            self.news_counts = {}  # Set empty dict on error

    def refresh_earnings(self):
        """Refresh earnings calendar"""
        interval_min = self.settings.get('scheduling', {}).get('earnings_refresh_min', 60)

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
        interval_min = self.settings.get('scheduling', {}).get('health_refresh_min', 10)

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

        # Position check - and store for UI
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

            # Store positions for UI to access - convert to JSON
            from .state import set_kv
            try:
                set_kv('positions', positions)  # set_kv now handles dict/list conversion
            except Exception as e:
                self.logger.warning(f"Failed to store positions for UI: {e}")

        else:
            checks['positions'] = {'status': 'ERROR'}

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
        self.logger.info("="*60)

        try:
            self.initialize()
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}", exc_info=True)
            return

        self.running = True
        loop_count = 0

        self.logger.info("Entering main loop...")

        # Check if crypto trading is enabled
        crypto_enabled = self.settings.get('strategies', {}).get('crypto', False)
        if crypto_enabled:
            self.logger.info("🪙 Crypto trading ENABLED - will trade 24/7")

        try:
            while self.running:
                loop_count += 1

                if loop_count % 10 == 1:  # Log every 10 loops
                    self.logger.info(f"Main loop iteration {loop_count}, market_open={self.market_open}")

                try:
                    # Health checks (always run)
                    self.run_health_checks()

                    # Initialize daily metrics if new day and market opens
                    if self.market_open and not self.daily_initialized:
                        account = self.broker.get_account()
                        if account and self.risk_manager:
                            equity = float(account.get('equity', 0))
                            self.risk_manager.reset_daily_metrics(equity)
                            self.daily_initialized = True
                            self.logger.info("Daily metrics initialized")

                    # Determine if we should trade
                    should_trade = self.market_open or crypto_enabled

                    if should_trade:
                        if not self.market_open:
                            self.logger.debug("Market closed but crypto enabled - trading crypto")
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
                        # Market closed and no crypto
                        if loop_count % 20 == 1:  # Log occasionally
                            self.logger.info("Market is closed, waiting... (Enable crypto in settings to trade 24/7)")
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
            self.running = False
            self.logger.info("Engine stopped")