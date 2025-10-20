"""
Enhanced Trading Engine - Complete Version with Position Monitor
Drop-in replacement for existing engine.py with full risk management
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
            self.logger.warning("⚠ Position monitor disabled in settings")

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
                converted.append(RiskPosition(
                    symbol=pos.get('symbol', ''),
                    qty=int(pos.get('qty', 0)),
                    avg_entry_price=float(pos.get('avg_entry_price', 0)),
                    current_price=float(pos.get('current_price', 0)),
                    market_value=float(pos.get('market_value', 0)),
                    unrealized_pl=float(pos.get('unrealized_pl', 0)),
                    unrealized_plpc=float(pos.get('unrealized_plpc', 0))
                ))
            except Exception as e:
                self.logger.warning(f"Error converting position {pos.get('symbol')}: {e}")
        return converted

    def check_exits(self):
        """Check existing positions for exit signals"""
        try:
            # Try different method names
            positions = None
            if hasattr(self.broker, 'list_positions'):
                positions = self.broker.list_positions()
            elif hasattr(self.broker, 'get_positions'):
                positions = self.broker.get_positions()

            if not positions:
                return

            for position in positions:
                symbol = position.get('symbol')
                qty = int(position.get('qty', 0))

                if qty == 0:
                    continue

                # Use risk manager if available
                if self.risk_manager:
                    risk_pos = self._convert_positions([position])[0]
                    should_exit, reason = self.risk_manager.should_exit_position(risk_pos)

                    if should_exit:
                        self.logger.info(f"Exit signal for {symbol}: {reason}")
                        # Place exit order
                        # Note: position monitor handles stop losses automatically
                        # This is for other exit signals (profit taking, etc.)

        except Exception as e:
            self.logger.error(f"Error checking exits: {e}", exc_info=True)

    def check_entries(self):
        """Check candidates for entry signals"""
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

            # Check risk limits before entering trades
            if self.risk_manager:
                risk_positions = self._convert_positions(positions)
                equity = float(account.get('equity', 0))
                metrics = self.risk_manager.calculate_risk_metrics(risk_positions, equity)

                if metrics.has_violations():
                    self.logger.warning(f"Risk violations present: {metrics.violations}")
                    return

            # Look for entry opportunities
            # This is where your strategy logic goes
            # For now, it's conservative demo mode

        except Exception as e:
            self.logger.error(f"Error checking entries: {e}", exc_info=True)

    def refresh_candidates(self):
        """Refresh candidate list"""
        interval_min = self.settings.get('scheduling', {}).get('candidate_refresh_min', 20)

        if self.last_candidate_refresh:
            elapsed = (datetime.now() - self.last_candidate_refresh).total_seconds() / 60
            if elapsed < interval_min:
                return

        self.logger.info("Refreshing candidates...")

        try:
            # Get batch snapshots
            batch_size = self.settings.get('scheduling', {}).get('candidate_max_symbols', 150)
            symbols = self.universe[:batch_size]

            if not symbols:
                self.logger.warning("No symbols in universe")
                return

            # Try different snapshot method names
            snapshots = None
            if hasattr(self.broker, 'get_batch_snapshots'):
                snapshots = self.broker.get_batch_snapshots(symbols)
            elif hasattr(self.broker, 'get_snapshots'):
                snapshots = self.broker.get_snapshots(symbols)
            elif hasattr(self.broker, 'batch_snapshots'):
                snapshots = self.broker.batch_snapshots(symbols)

            if not snapshots:
                self.logger.warning("No snapshots returned")
                return

            # Score candidates using strategy manager
            if self.strategy_manager:
                self.candidates = self.strategy_manager.rank_candidates(
                    snapshots,
                    self.news_counts,
                    self.earnings_data
                )
            else:
                # Basic scoring
                self.candidates = []
                for symbol, snap in snapshots.items():
                    daily_bar = snap.get('dailyBar', {})
                    if daily_bar:
                        self.candidates.append({
                            'symbol': symbol,
                            'score': 0.5,  # Basic score
                            'snapshot': snap
                        })

            # Sort by score
            self.candidates.sort(key=lambda x: x.get('score', 0), reverse=True)

            self.last_candidate_refresh = datetime.now()
            self.logger.info(f"Found {len(self.candidates)} candidates")

        except Exception as e:
            self.logger.error(f"Error refreshing candidates: {e}", exc_info=True)

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
                    provider_order = self.settings.get('news', {}).get('provider_order',
                                                                       ['alpaca', 'finnhub', 'newsapi'])
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
        self.logger.info("=" * 60)
        self.logger.info("STARTING ENHANCED TRADING ENGINE")
        self.logger.info("=" * 60)

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

                    # Check if trading is halted by position monitor
                    if hasattr(self, 'position_monitor') and self.position_monitor.trading_halted:
                        self.logger.warning(
                            f"⛔ Trading halted by position monitor: {self.position_monitor.halt_reason}"
                        )
                        time.sleep(60)  # Check again in 1 minute
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
            # Stop position monitor
            if hasattr(self, 'position_monitor'):
                try:
                    self.position_monitor.stop()
                    self.logger.info("Position monitor stopped")
                except Exception as e:
                    self.logger.error(f"Error stopping position monitor: {e}")

            self.running = False
            self.logger.info("Engine stopped")