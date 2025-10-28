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

        # Threading
        self._monitor_thread = None
        self._running = False
        self._stop_event = threading.Event()

    def register_position(
        self,
        symbol: str,
        primary_strategy: str,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float
    ):
        """
        Register position metadata for dynamic stop management

        Args:
            symbol: Trading symbol
            primary_strategy: Primary strategy name (e.g., 'momentum', 'crypto')
            entry_price: Entry price
            stop_loss_pct: Strategy-specific stop-loss percentage
            take_profit_pct: Strategy-specific take-profit percentage
        """
        self.position_metadata[symbol] = {
            'primary_strategy': primary_strategy,
            'entry_price': entry_price,
            'stop_loss_pct': stop_loss_pct,
            'take_profit_pct': take_profit_pct
        }
        self.logger.debug(
            f"Registered position metadata for {symbol}: "
            f"strategy={primary_strategy}, stop={stop_loss_pct}%, target={take_profit_pct}%"
        )

    def unregister_position(self, symbol: str):
        """Remove position metadata when position is closed"""
        if symbol in self.position_metadata:
            del self.position_metadata[symbol]
            self.logger.debug(f"Unregistered position metadata for {symbol}")

    def start(self):
        """Start the position monitoring loop"""
        if self._running:
            self.logger.warning("Position monitor already running")
            return

        self._running = True
        self._stop_event.clear()

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
        ENHANCED: Dynamic stop-loss management with trailing behavior

        Instead of just checking if stop hit, this method:
        1. Calculates optimal stop price based on current profit
        2. Replaces broker-level stop order when stop should move
        3. Executes take-profit when target reached

        Trailing Logic (Option B):
        - Losing/flat: Maintain original strategy stop distance
        - Profitable > 0.5%: Move stop to breakeven
        - Profitable > 1.5%: Trail at 50% of current profit
        - Profitable > take_profit_pct: Execute market sell

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

        # Only check long positions (qty > 0) for now
        if qty < 0:
            self.logger.debug(f"Skipping short position {symbol} - not implemented")
            return

        # Calculate current profit percentage
        profit_pct = ((current_price - entry_price) / entry_price) * 100

        # Get strategy-specific parameters for this position
        metadata = self.position_metadata.get(symbol, {})
        strategy_stop_loss_pct = metadata.get('stop_loss_pct', 0.8)  # Default 0.8%
        strategy_take_profit_pct = metadata.get('take_profit_pct', 2.5)  # Default 2.5%

        # Check take-profit first (manual execution via market order)
        if profit_pct >= strategy_take_profit_pct:
            self._execute_take_profit(
                symbol, qty, entry_price, current_price, profit_pct, strategy_take_profit_pct
            )
            return

        # Calculate new stop price based on profit level (Option B logic)
        new_stop_price = self._calculate_dynamic_stop_price(
            entry_price, current_price, profit_pct, strategy_stop_loss_pct
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
                    symbol, qty, old_order_id, old_stop_price, new_stop_price, profit_pct
                )
        else:
            # No stop order found - this shouldn't happen, but create one as safety
            self.logger.warning(
                f"No stop order found for {symbol}, creating protective stop"
            )
            self._create_missing_stop_order(symbol, qty, new_stop_price)

    def _calculate_dynamic_stop_price(
        self,
        entry_price: float,
        current_price: float,
        profit_pct: float,
        strategy_stop_loss_pct: float
    ) -> float:
        """
        Calculate dynamic stop price based on profit level (Option B logic)

        Logic:
        - Losing/flat (< 0.5% profit): Strategy's original stop distance
        - Small profit (0.5% - 1.5%): Breakeven (entry price)
        - Good profit (> 1.5%): Trail at 50% of current profit

        Args:
            entry_price: Original entry price
            current_price: Current market price
            profit_pct: Current profit percentage
            strategy_stop_loss_pct: Strategy-specific stop-loss percentage

        Returns:
            New stop price
        """
        if profit_pct < 0.5:
            # Losing or minimal profit: use strategy's original stop distance
            stop_price = current_price * (1 - strategy_stop_loss_pct / 100)

        elif profit_pct < 1.5:
            # Small profit: move to breakeven
            stop_price = entry_price

        else:
            # Good profit: trail at 50% of current profit
            # If entry=$100, current=$105 (5% profit), stop at $102.50 (lock in 2.5%)
            profit_per_share = current_price - entry_price
            stop_price = entry_price + (profit_per_share * 0.5)

        return stop_price

    def _find_stop_order(self, symbol: str) -> Optional[Dict]:
        """
        Find existing stop order for a symbol

        Returns:
            Order dict if found, None otherwise
        """
        try:
            # Get all open orders
            open_orders = self.broker.list_orders(status='open')

            # Find stop order for this symbol
            for order in open_orders:
                if (order.get('symbol') == symbol and
                    order.get('type') == 'stop' and
                    order.get('side') == 'sell'):
                    return order

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
        profit_pct: float
    ):
        """Replace existing stop order with new stop price"""
        try:
            # Determine stop movement direction
            if new_stop_price > old_stop_price:
                direction = "UP"
                emoji = "📈"
            else:
                direction = "DOWN"
                emoji = "📉"

            self.logger.info(
                f"{emoji} Trailing stop for {symbol} {direction}: "
                f"${old_stop_price:.2f} -> ${new_stop_price:.2f} "
                f"(P&L: {profit_pct:+.2f}%)"
            )

            # Use broker's replace method
            new_order = self.broker.replace_stop_loss_order(
                old_order_id=old_order_id,
                symbol=symbol,
                qty=qty,
                new_stop_price=new_stop_price,
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

    def _create_missing_stop_order(self, symbol: str, qty: int, stop_price: float):
        """Create stop order if one is missing (safety net)"""
        try:
            self.logger.warning(
                f"Creating missing stop order for {symbol} @ ${stop_price:.2f}"
            )

            stop_order = self.broker.place_stop_loss_order(
                symbol=symbol,
                qty=qty,
                stop_price=stop_price,
                time_in_force='gtc'
            )

            if stop_order:
                self.logger.info(f"Safety stop order created for {symbol}")
            else:
                self.logger.error(
                    f"CRITICAL: Failed to create safety stop for {symbol}! "
                    f"Position is unprotected."
                )

        except Exception as e:
            self.logger.error(
                f"Error creating safety stop for {symbol}: {e}",
                exc_info=True
            )

    def _execute_take_profit(
        self,
        symbol: str,
        qty: int,
        entry_price: float,
        current_price: float,
        profit_pct: float,
        target_pct: float
    ):
        """Execute take-profit via market sell order"""
        try:
            profit_amount = (current_price - entry_price) * qty

            self.logger.info(
                f"🎯 TAKE PROFIT: {symbol} at {profit_pct:.2f}% (target: {target_pct:.2f}%)\n"
                f"  Entry: ${entry_price:.2f}\n"
                f"  Exit: ${current_price:.2f}\n"
                f"  Profit: ${profit_amount:.2f}"
            )

            # Place market sell order
            order = self.broker.place_order(
                symbol=symbol,
                side='sell',
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