"""
Position Monitor Module - Synchronous Version
Monitors open positions and enforces stop loss rules
Works with threading-based engines (non-async)
"""
import logging
import time
import threading
from typing import Dict, List, Optional
from datetime import datetime, date
from dataclasses import dataclass


@dataclass
class StopLossEvent:
    """Record of a stop loss trigger"""
    timestamp: str
    symbol: str
    entry_price: float
    exit_price: float
    loss_bps: float
    threshold_bps: float
    qty: int
    loss_amount: float
    reason: str  # 'trade' or 'daily'


class PositionMonitor:
    """
    Monitors positions and enforces stop loss rules (synchronous version)
    - Trade-level stop loss (in basis points)
    - Daily portfolio stop loss (in percentage)
    """

    def __init__(
        self,
        broker,
        settings: Dict,
        logger: Optional[logging.Logger] = None
    ):
        self.broker = broker
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

        # State tracking
        self.starting_daily_equity = None
        self.last_equity_reset_date = None
        self.stop_loss_events: List[StopLossEvent] = []
        self.trading_halted = False
        self.halt_reason = None

        # Position metadata: symbol -> strategy info
        self.position_metadata: Dict[str, Dict] = {}

        # Track symbols where stop order creation has failed (to avoid repeated attempts)
        self.failed_stop_order_symbols: set = set()

        # Threading
        self._monitor_thread = None
        self._running = False
        self._stop_event = threading.Event()

        # Load persisted position metadata on initialization
        self._load_position_metadata()

    def _load_position_metadata(self):
        """Load position metadata from persistent storage"""
        try:
            from .state import get_kv
            metadata_json = get_kv('position_metadata')
            if metadata_json:
                import json
                self.position_metadata = json.loads(metadata_json)
                self.logger.info(f"Loaded position metadata for {len(self.position_metadata)} positions from storage")
                for symbol, meta in self.position_metadata.items():
                    self.logger.debug(
                        f"  {symbol}: strategy={meta.get('primary_strategy')}, "
                        f"entry=${meta.get('entry_price'):.2f}"
                    )
        except Exception as e:
            self.logger.warning(f"Could not load position metadata: {e}")
            self.position_metadata = {}

    def _save_position_metadata(self):
        """Save position metadata to persistent storage"""
        try:
            from .state import set_kv
            import json
            set_kv('position_metadata', json.dumps(self.position_metadata))
            self.logger.debug(f"Saved position metadata for {len(self.position_metadata)} positions")
        except Exception as e:
            self.logger.warning(f"Could not save position metadata: {e}")

    def register_position(
        self,
        symbol: str,
        primary_strategy: str,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        side: str = 'long'
    ):
        """
        Register position metadata for dynamic stop management

        Args:
            symbol: Trading symbol
            primary_strategy: Primary strategy name (e.g., 'momentum', 'crypto')
            entry_price: Entry price
            stop_loss_pct: Strategy-specific stop-loss percentage
            take_profit_pct: Strategy-specific take-profit percentage
            side: Position side - 'long' or 'short'
        """
        self.position_metadata[symbol] = {
            'primary_strategy': primary_strategy,
            'entry_price': entry_price,
            'stop_loss_pct': stop_loss_pct,
            'take_profit_pct': take_profit_pct,
            'side': side
        }
        self.logger.debug(
            f"Registered position metadata for {symbol} ({side}): "
            f"strategy={primary_strategy}, stop={stop_loss_pct}%, target={take_profit_pct}%"
        )

        # Persist to storage
        self._save_position_metadata()

    def unregister_position(self, symbol: str):
        """Remove position metadata when position is closed"""
        if symbol in self.position_metadata:
            del self.position_metadata[symbol]
            self.logger.debug(f"Unregistered position metadata for {symbol}")
            # Persist to storage
            self._save_position_metadata()
        # Also remove from failed stop order tracking
        self.failed_stop_order_symbols.discard(symbol)

    def register_existing_positions(self):
        """
        Register metadata for existing open positions that don't have it yet.
        This is useful when the bot starts with open positions.
        Uses default/conservative values for positions without registered metadata.
        """
        try:
            positions = self._get_positions()
            if not positions:
                self.logger.debug("No existing positions to register")
                return

            registered_count = 0
            for position in positions:
                symbol = position.get('symbol')
                if not symbol:
                    continue

                # Skip if already registered
                if symbol in self.position_metadata:
                    self.logger.debug(f"{symbol} already has metadata, skipping")
                    continue

                # Register with conservative default values
                entry_price = float(position.get('avg_entry_price', 0))
                if entry_price <= 0:
                    self.logger.warning(f"Invalid entry price for {symbol}, skipping registration")
                    continue

                # Use conservative defaults for unknown positions
                default_stop_loss_pct = self.settings.get('thresholds', {}).get('default_stop_loss_pct', 1.0)
                default_take_profit_pct = self.settings.get('thresholds', {}).get('default_take_profit_pct', 3.0)

                self.register_position(
                    symbol=symbol,
                    primary_strategy='manual',  # Mark as manually opened (outside bot control)
                    entry_price=entry_price,
                    stop_loss_pct=default_stop_loss_pct,
                    take_profit_pct=default_take_profit_pct
                )
                registered_count += 1

                self.logger.info(
                    f"Registered untracked position {symbol} with default parameters: "
                    f"entry=${entry_price:.2f}, stop={default_stop_loss_pct}%, target={default_take_profit_pct}% "
                    f"(marked as 'manual' - opened outside bot)"
                )

            if registered_count > 0:
                self.logger.info(f"Registered {registered_count} existing position(s) with default metadata")

        except Exception as e:
            self.logger.error(f"Error registering existing positions: {e}", exc_info=True)

    def start(self):
        """Start the position monitoring loop"""
        if self._running:
            self.logger.warning("Position monitor already running")
            return

        self._running = True
        self._stop_event.clear()

        # Register any existing positions before starting monitoring
        try:
            loaded_count = len(self.position_metadata)
            if loaded_count > 0:
                self.logger.info(
                    f"Restored metadata for {loaded_count} position(s) from storage: "
                    f"{', '.join(self.position_metadata.keys())}"
                )

            self.logger.info("Checking for any untracked open positions...")
            self.register_existing_positions()
        except Exception as e:
            self.logger.error(f"Error during initial position registration: {e}", exc_info=True)

        # Start monitoring thread
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            name="PositionMonitor",
            daemon=True
        )
        self._monitor_thread.start()

        self.logger.info("Position monitor started")

    def stop(self):
        """Stop the position monitoring loop"""
        self._running = False
        self._stop_event.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)

        self.logger.info("Position monitor stopped")

    def _monitoring_loop(self):
        """Main monitoring loop - runs continuously in background thread"""
        interval = self.settings.get('risk', {}).get('position_monitor_interval_sec', 30)
        self.logger.info(f"Position monitoring interval: {interval} seconds")

        while self._running and not self._stop_event.is_set():
            try:
                self._check_positions()
            except Exception as e:
                self.logger.error(f"Error in position monitoring loop: {e}", exc_info=True)

            # Wait before next check (with interruptible sleep)
            self._stop_event.wait(timeout=interval)

    def _check_positions(self):
        """Check all positions for stop loss triggers"""
        if self.trading_halted:
            # If trading halted, skip monitoring (but keep loop running)
            return

        try:
            # Reset daily equity if new day
            self._reset_daily_equity_if_needed()

            # Get current positions
            positions = self._get_positions()
            if not positions:
                return

            # Get account info for daily stop loss
            account_info = self._get_account_info()

            # Check daily stop loss first
            if self._check_daily_stop_loss(account_info):
                # Daily stop loss hit - close all positions and halt
                self._execute_daily_stop_loss(positions)
                return

            # Check individual position stop losses
            for position in positions:
                self._check_position_stop_loss(position)

        except Exception as e:
            self.logger.error(f"Error checking positions: {e}", exc_info=True)

    def _reset_daily_equity_if_needed(self):
        """Reset starting daily equity at market open each day"""
        today = date.today()

        # Check if we need to reset
        if self.last_equity_reset_date != today:
            try:
                account_info = self._get_account_info()
                equity = float(account_info.get('equity', 0))

                if equity > 0:
                    self.starting_daily_equity = equity
                    self.last_equity_reset_date = today
                    self.logger.info(
                        f"Daily equity reset for {today}: ${equity:,.2f}"
                    )

                    # Reset halt if it was for daily stop loss
                    if self.trading_halted and self.halt_reason == 'daily_stop_loss':
                        self.trading_halted = False
                        self.halt_reason = None
                        self.logger.info("Trading resumed - new trading day")

            except Exception as e:
                self.logger.error(f"Error resetting daily equity: {e}")

    def _get_positions(self) -> List[Dict]:
        """Get current positions from broker"""
        try:
            # Try different method names
            if hasattr(self.broker, 'get_positions'):
                positions = self.broker.get_positions()
            elif hasattr(self.broker, 'list_positions'):
                positions = self.broker.list_positions()
            else:
                self.logger.warning("Broker has no get_positions or list_positions method")
                return []

            return positions if positions else []
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
            return []

    def _get_account_info(self) -> Dict:
        """Get account information from broker"""
        try:
            return self.broker.get_account()
        except Exception as e:
            self.logger.error(f"Error fetching account info: {e}")
            return {}

    def _check_daily_stop_loss(self, account_info: Dict) -> bool:
        """
        Check if daily portfolio stop loss has been hit

        Returns:
            True if daily stop loss exceeded
        """
        if not account_info or not self.starting_daily_equity:
            return False

        # Get threshold from settings
        daily_threshold_pct = self.settings.get('thresholds', {}).get(
            'daily_stop_loss_pct', 2.0
        )

        # Skip if threshold is 0 or negative (disabled)
        if daily_threshold_pct <= 0:
            return False

        # Calculate current equity
        current_equity = float(account_info.get('equity', 0))
        if current_equity <= 0:
            return False

        # Calculate daily loss percentage
        daily_loss_pct = (
            (self.starting_daily_equity - current_equity) / self.starting_daily_equity
        ) * 100

        # Log if approaching threshold
        if daily_loss_pct >= daily_threshold_pct * 0.8:  # 80% of threshold
            self.logger.warning(
                f"Daily loss approaching threshold: {daily_loss_pct:.2f}% "
                f"(threshold: {daily_threshold_pct:.2f}%)"
            )

        # Check if threshold exceeded
        if daily_loss_pct >= daily_threshold_pct:
            self.logger.critical(
                f"DAILY STOP LOSS HIT: Portfolio down {daily_loss_pct:.2f}% "
                f"(threshold: {daily_threshold_pct:.2f}%)\n"
                f"Starting equity: ${self.starting_daily_equity:,.2f}\n"
                f"Current equity: ${current_equity:,.2f}\n"
                f"Loss: ${self.starting_daily_equity - current_equity:,.2f}"
            )
            return True

        return False

    def _execute_daily_stop_loss(self, positions: List[Dict]):
        """Execute daily stop loss - close all positions and halt trading"""
        self.logger.critical("Executing daily stop loss - closing all positions")

        # Halt trading
        self.trading_halted = True
        self.halt_reason = 'daily_stop_loss'

        # Close all positions
        for position in positions:
            try:
                symbol = position.get('symbol')
                qty = abs(int(position.get('qty', 0)))

                if qty <= 0:
                    continue

                self.logger.info(f"Daily stop loss: Closing {symbol} position (qty: {qty})")

                # Place market sell order
                self.broker.place_order(
                    symbol=symbol,
                    side='sell',
                    qty=qty,
                    order_type='market',
                    time_in_force='ioc'
                )

                # Record event
                event = StopLossEvent(
                    timestamp=datetime.now().isoformat(),
                    symbol=symbol,
                    entry_price=float(position.get('avg_entry_price', 0)),
                    exit_price=float(position.get('current_price', 0)),
                    loss_bps=0,  # Not calculated for daily stop
                    threshold_bps=0,
                    qty=qty,
                    loss_amount=float(position.get('unrealized_pl', 0)),
                    reason='daily'
                )
                self.stop_loss_events.append(event)

            except Exception as e:
                self.logger.error(f"Failed to close position {symbol} for daily stop loss: {e}")

        self.logger.critical(
            f"Daily stop loss executed. Trading halted for today. "
            f"Will resume on next trading day."
        )

    def _check_position_stop_loss(self, position: Dict):
        """
        ENHANCED: Dynamic stop-loss management with trailing behavior for BOTH long and short positions

        Instead of just checking if stop hit, this method:
        1. Calculates optimal stop price based on current profit
        2. Replaces broker-level stop order when stop should move
        3. Executes take-profit when target reached

        Trailing Logic (Option B):
        - Losing/flat: Maintain original strategy stop distance
        - Profitable > 0.5%: Move stop to breakeven
        - Profitable > 1.5%: Trail at 50% of current profit
        - Profitable > take_profit_pct: Execute market sell/buy (depending on position side)

        Args:
            position: Position dict with symbol, qty, avg_entry_price, current_price
        """
        symbol = position.get('symbol')
        entry_price = float(position.get('avg_entry_price', 0))
        current_price = float(position.get('current_price', 0))
        qty = int(position.get('qty', 0))

        # Validate data
        if not symbol or entry_price <= 0 or current_price <= 0 or qty == 0:
            return

        # Determine if this is a long or short position
        # Positive qty = long, negative qty = short
        is_long = qty > 0
        qty = abs(qty)  # Work with absolute quantity

        # Get position metadata to confirm side
        metadata = self.position_metadata.get(symbol, {})
        metadata_side = metadata.get('side', 'long')

        # Use metadata side if available, otherwise infer from qty
        if metadata_side == 'short':
            is_long = False

        # Calculate current profit percentage
        # For longs: profit when price goes up
        # For shorts: profit when price goes down
        if is_long:
            profit_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            profit_pct = ((entry_price - current_price) / entry_price) * 100

        # Get strategy-specific parameters for this position
        metadata = self.position_metadata.get(symbol, {})
        strategy_stop_loss_pct = metadata.get('stop_loss_pct', 0.8)  # Default 0.8%
        strategy_take_profit_pct = metadata.get('take_profit_pct', 2.5)  # Default 2.5%

        # Check take-profit first (manual execution via market order)
        if profit_pct >= strategy_take_profit_pct:
            self._execute_take_profit(
                symbol, qty, entry_price, current_price, profit_pct, strategy_take_profit_pct, is_long
            )
            return

        # Calculate new stop price based on profit level (Option B logic)
        new_stop_price = self._calculate_dynamic_stop_price(
            entry_price, current_price, profit_pct, strategy_stop_loss_pct, is_long
        )

        # Find existing stop order for this symbol
        existing_stop_order = self._find_stop_order(symbol)

        if existing_stop_order:
            old_stop_price = float(existing_stop_order.get('stop_price', 0))
            old_order_id = existing_stop_order.get('id', '')

            # Only replace if stop moved significantly (>0.1% difference)
            price_diff_pct = abs(new_stop_price - old_stop_price) / old_stop_price * 100

            if price_diff_pct >= 0.1:
                self._replace_stop_order(
                    symbol, qty, old_order_id, old_stop_price, new_stop_price, profit_pct, is_long
                )
        else:
            # No stop order found - check if we've already tried and failed for this symbol
            if symbol not in self.failed_stop_order_symbols:
                self.logger.warning(
                    f"No stop order found for {symbol}, creating protective stop"
                )
                self._create_missing_stop_order(symbol, qty, new_stop_price, is_long)

    def _calculate_dynamic_stop_price(
        self,
        entry_price: float,
        current_price: float,
        profit_pct: float,
        strategy_stop_loss_pct: float,
        is_long: bool = True
    ) -> float:
        """
        Calculate dynamic stop price based on profit level (Option B logic)
        Works for both long and short positions

        Logic:
        - Losing/flat (< 0.5% profit): Strategy's original stop distance
        - Small profit (0.5% - 1.5%): Breakeven (entry price)
        - Good profit (> 1.5%): Trail at 50% of current profit

        Args:
            entry_price: Original entry price
            current_price: Current market price
            profit_pct: Current profit percentage (already calculated correctly for long/short)
            strategy_stop_loss_pct: Strategy-specific stop-loss percentage
            is_long: True for long positions, False for short positions

        Returns:
            New stop price
        """
        if profit_pct < 0.5:
            # Losing or minimal profit: use strategy's original stop distance
            if is_long:
                stop_price = current_price * (1 - strategy_stop_loss_pct / 100)
            else:
                # For shorts, stop is above current price
                stop_price = current_price * (1 + strategy_stop_loss_pct / 100)

        elif profit_pct < 1.5:
            # Small profit: move to breakeven
            stop_price = entry_price

        else:
            # Good profit: trail at 50% of current profit
            if is_long:
                # Long: If entry=$100, current=$105 (5% profit), stop at $102.50 (lock in 2.5%)
                profit_per_share = current_price - entry_price
                stop_price = entry_price + (profit_per_share * 0.5)
            else:
                # Short: If entry=$100, current=$95 (5% profit), stop at $97.50 (lock in 2.5%)
                profit_per_share = entry_price - current_price
                stop_price = entry_price - (profit_per_share * 0.5)

        return stop_price

    def _find_stop_order(self, symbol: str) -> Optional[Dict]:
        """
        Find existing stop order for a symbol
        Works for both long (sell stop) and short (buy stop) positions

        Returns:
            Order dict if found, None otherwise
        """
        try:
            # Get all open orders (includes pending_new, accepted, etc.)
            open_orders = self.broker.list_orders(status='open')

            # Find stop order for this symbol
            # Check for both 'stop' and 'stop_limit' order types
            # For longs: stop side is 'sell'
            # For shorts: stop side is 'buy'
            for order in open_orders:
                order_symbol = order.get('symbol')
                order_type = order.get('type')
                order_side = order.get('side')
                order_status = order.get('status', 'unknown')

                if order_symbol == symbol:
                    self.logger.debug(
                        f"Found order for {symbol}: type={order_type}, side={order_side}, "
                        f"status={order_status}, id={order.get('id', 'N/A')}"
                    )

                    # Match stop orders (both 'stop' and 'stop_limit')
                    # Accept both buy and sell stops (for long and short positions)
                    if order_type in ['stop', 'stop_limit']:
                        self.logger.debug(f"Matched stop order for {symbol}: {order.get('id')}")
                        return order

            # If we didn't find any order for this symbol, log that too
            self.logger.debug(f"No orders found for {symbol} in {len(open_orders)} open orders")
            return None

        except Exception as e:
            self.logger.error(f"Error finding stop order for {symbol}: {e}")
            return None

    def _replace_stop_order(
        self,
        symbol: str,
        qty: int,
        old_order_id: str,
        old_stop_price: float,
        new_stop_price: float,
        profit_pct: float,
        is_long: bool = True
    ):
        """Replace existing stop order with new stop price (works for both long and short)"""
        try:
            # Determine stop movement direction
            if new_stop_price > old_stop_price:
                direction = "UP"
                emoji = "📈"
            else:
                direction = "DOWN"
                emoji = "📉"

            position_type = "LONG" if is_long else "SHORT"
            self.logger.info(
                f"{emoji} Trailing stop for {symbol} ({position_type}) {direction}: "
                f"${old_stop_price:.2f} -> ${new_stop_price:.2f} "
                f"(P&L: {profit_pct:+.2f}%)"
            )

            # Cancel old order and place new one
            # For longs: stop side is 'sell'
            # For shorts: stop side is 'buy'
            stop_side = 'sell' if is_long else 'buy'

            # Cancel old order
            cancel_success = self.broker.cancel_order(old_order_id)

            # Place new stop order
            new_order = self.broker.place_order(
                symbol=symbol,
                qty=qty,
                side=stop_side,
                order_type='stop',
                stop_price=new_stop_price,
                time_in_force='gtc'
            )

            if not new_order:
                self.logger.error(
                    f"Failed to replace stop order for {symbol}, "
                    f"position may be unprotected!"
                )

        except Exception as e:
            self.logger.error(
                f"Error replacing stop order for {symbol}: {e}",
                exc_info=True
            )

    def _create_missing_stop_order(self, symbol: str, qty: int, stop_price: float, is_long: bool = True):
        """Create stop order if one is missing (safety net) - works for both long and short"""
        try:
            position_type = "LONG" if is_long else "SHORT"
            self.logger.warning(
                f"Creating missing stop order for {symbol} ({position_type}) @ ${stop_price:.2f}"
            )

            # For longs: stop side is 'sell'
            # For shorts: stop side is 'buy'
            stop_side = 'sell' if is_long else 'buy'

            stop_order = self.broker.place_order(
                symbol=symbol,
                qty=qty,
                side=stop_side,
                order_type='stop',
                stop_price=stop_price,
                time_in_force='gtc'
            )

            if stop_order:
                self.logger.info(f"Safety stop order created for {symbol}")
            else:
                # Check if this is a wash trade error (order already exists)
                # The broker will have logged the detailed error
                self.logger.warning(
                    f"Could not create stop order for {symbol} - may already exist. "
                    f"Check broker logs for details. Position monitoring will continue."
                )
                # Mark this symbol as failed to avoid repeated attempts
                self.failed_stop_order_symbols.add(symbol)

        except Exception as e:
            self.logger.error(
                f"Error creating safety stop for {symbol}: {e}",
                exc_info=True
            )
            # Mark this symbol as failed to avoid repeated attempts
            self.failed_stop_order_symbols.add(symbol)

    def _execute_take_profit(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        current_price: float,
        profit_pct: float,
        target_pct: float,
        is_long: bool = True
    ):
        """Execute take-profit via market order (works for both long and short)"""
        try:
            # Calculate profit amount
            # For longs: (current - entry) * qty
            # For shorts: (entry - current) * qty
            if is_long:
                profit_amount = (current_price - entry_price) * qty
            else:
                profit_amount = (entry_price - current_price) * qty

            position_type = "LONG" if is_long else "SHORT"
            self.logger.info(
                f"🎯 TAKE PROFIT ({position_type}): {symbol} at {profit_pct:.2f}% (target: {target_pct:.2f}%)\n"
                f"  Entry: ${entry_price:.2f}\n"
                f"  Exit: ${current_price:.2f}\n"
                f"  Profit: ${profit_amount:.2f}"
            )

            # Place market order to close position
            # For longs: sell to close
            # For shorts: buy to cover
            close_side = 'sell' if is_long else 'buy'

            order = self.broker.place_order(
                symbol=symbol,
                side=close_side,
                qty=qty,
                order_type='market',
                time_in_force='day'
            )

            if order:
                self.logger.info(f"✓ Take-profit order executed for {symbol}")

                # Cancel any existing stop order (position closed)
                existing_stop = self._find_stop_order(symbol)
                if existing_stop:
                    self.broker.cancel_order(existing_stop.get('id', ''))

                # Unregister position metadata
                self.unregister_position(symbol)

            else:
                self.logger.error(f"Failed to execute take-profit for {symbol}")

        except Exception as e:
            self.logger.error(
                f"Error executing take-profit for {symbol}: {e}",
                exc_info=True
            )

    def _execute_position_stop_loss(
        self,
        position: Dict,
        loss_bps: float,
        threshold_bps: float
    ):
        """Execute stop loss for a specific position"""
        symbol = position.get('symbol')
        qty = abs(int(position.get('qty', 0)))
        entry_price = float(position.get('avg_entry_price', 0))
        current_price = float(position.get('current_price', 0))

        self.logger.warning(
            f"STOP LOSS TRIGGERED for {symbol}:\n"
            f"  Entry price: ${entry_price:.2f}\n"
            f"  Current price: ${current_price:.2f}\n"
            f"  Loss: {loss_bps:.1f} bps ({(loss_bps/100):.2f}%)\n"
            f"  Threshold: {threshold_bps:.1f} bps\n"
            f"  Quantity: {qty}\n"
            f"  Estimated loss: ${(entry_price - current_price) * qty:.2f}"
        )

        try:
            # Place market sell order (IOC = immediate or cancel)
            self.broker.place_order(
                symbol=symbol,
                side='sell',
                qty=qty,
                order_type='market',
                time_in_force='ioc'
            )

            self.logger.info(f"Stop loss order placed for {symbol}")

            # Record event
            event = StopLossEvent(
                timestamp=datetime.now().isoformat(),
                symbol=symbol,
                entry_price=entry_price,
                exit_price=current_price,
                loss_bps=loss_bps,
                threshold_bps=threshold_bps,
                qty=qty,
                loss_amount=(entry_price - current_price) * qty,
                reason='trade'
            )
            self.stop_loss_events.append(event)

        except Exception as e:
            self.logger.error(
                f"CRITICAL: Failed to execute stop loss for {symbol}: {e}",
                exc_info=True
            )

    def get_status(self) -> Dict:
        """Get current monitor status"""
        return {
            'running': self._running,
            'trading_halted': self.trading_halted,
            'halt_reason': self.halt_reason,
            'starting_daily_equity': self.starting_daily_equity,
            'last_equity_reset_date': (
                self.last_equity_reset_date.isoformat()
                if self.last_equity_reset_date else None
            ),
            'stop_loss_events_today': len([
                e for e in self.stop_loss_events
                if e.timestamp.startswith(date.today().isoformat())
            ]),
            'total_stop_loss_events': len(self.stop_loss_events)
        }

    def get_stop_loss_events(self, limit: int = 20) -> List[Dict]:
        """Get recent stop loss events"""
        events = sorted(
            self.stop_loss_events,
            key=lambda e: e.timestamp,
            reverse=True
        )[:limit]

        return [
            {
                'timestamp': e.timestamp,
                'symbol': e.symbol,
                'entry_price': e.entry_price,
                'exit_price': e.exit_price,
                'loss_bps': e.loss_bps,
                'threshold_bps': e.threshold_bps,
                'qty': e.qty,
                'loss_amount': e.loss_amount,
                'reason': e.reason
            }
            for e in events
        ]