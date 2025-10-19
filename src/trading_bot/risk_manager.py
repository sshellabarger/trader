"""
Risk Management Module
Handles position sizing, stop losses, exposure limits, and risk controls
"""
import logging
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
from enum import Enum


class RiskLevel(Enum):
    """Risk level enumeration"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Position:
    """Position data structure"""
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    side: str = "long"
    entry_time: Optional[datetime] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None


@dataclass
class RiskMetrics:
    """Risk metrics for portfolio"""
    total_exposure_pct: float
    positions_at_risk: int
    daily_pl_pct: float
    max_position_size_pct: float
    risk_level: RiskLevel
    violations: List[str]


class RiskManager:
    """
    Comprehensive risk management system
    Enforces stop losses, position sizing, and exposure limits
    """
    
    def __init__(self, settings: Dict, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
        
        # Risk parameters from settings
        self.max_position_size_pct = settings.get('risk', {}).get('max_position_size_pct', 5.0)
        self.max_total_exposure_pct = settings.get('risk', {}).get('max_total_exposure_pct', 80.0)
        self.max_positions = settings.get('risk', {}).get('max_positions', 10)
        self.risk_per_trade_pct = settings.get('risk', {}).get('risk_per_trade_pct', 1.0)
        
        # Stop loss settings
        self.trade_stop_loss_bps = settings.get('thresholds', {}).get('trade_stop_loss_bps', 50.0)
        self.daily_stop_loss_pct = settings.get('thresholds', {}).get('daily_stop_loss_pct', 2.0)
        self.trailing_stop_enabled = settings.get('risk', {}).get('trailing_stop_enabled', True)
        self.trailing_stop_pct = settings.get('risk', {}).get('trailing_stop_pct', 1.5)
        
        # Time-based exits
        self.max_hold_time_minutes = settings.get('risk', {}).get('max_hold_time_minutes', 240)
        self.close_all_eod = settings.get('risk', {}).get('close_all_eod', True)
        
        # Track daily metrics
        self.daily_start_value: Optional[float] = None
        self.daily_trades_count = 0
        self.daily_max_trades = settings.get('risk', {}).get('max_daily_trades', 50)
        
        # Track highest prices for trailing stops
        self.position_highs: Dict[str, float] = {}
    
    def reset_daily_metrics(self, account_value: float):
        """Reset daily tracking metrics"""
        self.daily_start_value = account_value
        self.daily_trades_count = 0
        self.position_highs.clear()
        self.logger.info(f"Reset daily metrics. Starting value: ${account_value:,.2f}")
    
    def calculate_position_size(
        self,
        symbol: str,
        current_price: float,
        stop_loss_price: float,
        account_value: float,
        existing_positions: int = 0
    ) -> Tuple[int, Dict]:
        """
        Calculate position size based on risk parameters
        
        Returns:
            Tuple of (shares, details_dict)
        """
        details = {
            'method': 'risk_based',
            'risk_amount': 0,
            'price_risk': 0,
            'max_shares': 0,
            'limited_by': []
        }
        
        # Calculate risk amount (% of account)
        risk_amount = account_value * (self.risk_per_trade_pct / 100.0)
        details['risk_amount'] = risk_amount
        
        # Calculate price risk per share
        price_risk = abs(current_price - stop_loss_price)
        if price_risk == 0:
            self.logger.warning(f"{symbol}: Stop loss equals entry price, using 1% default")
            price_risk = current_price * 0.01
        details['price_risk'] = price_risk
        
        # Shares based on risk
        risk_shares = int(risk_amount / price_risk)
        
        # Apply maximum position size constraint
        max_position_value = account_value * (self.max_position_size_pct / 100.0)
        max_shares = int(max_position_value / current_price)
        details['max_shares'] = max_shares
        
        shares = min(risk_shares, max_shares)
        
        if shares == max_shares:
            details['limited_by'].append('max_position_size')
        
        # Check if we're at max positions
        if existing_positions >= self.max_positions:
            details['limited_by'].append('max_positions_reached')
            self.logger.warning(f"At max positions ({self.max_positions}), cannot open new position")
            return 0, details
        
        # Minimum viable trade
        if shares < 1:
            details['limited_by'].append('minimum_shares')
            self.logger.debug(f"{symbol}: Calculated shares < 1, position too small")
            return 0, details
        
        # Check if position value is reasonable (at least $100)
        position_value = shares * current_price
        if position_value < 100:
            details['limited_by'].append('minimum_value')
            self.logger.debug(f"{symbol}: Position value ${position_value:.2f} below minimum")
            return 0, details
        
        self.logger.info(
            f"{symbol}: Position size={shares} shares, "
            f"value=${position_value:,.2f}, risk=${risk_amount:.2f}, "
            f"stop=${stop_loss_price:.2f}"
        )
        
        return shares, details
    
    def check_stop_losses(
        self,
        positions: List[Position],
        account_value: float
    ) -> List[Tuple[str, str, Dict]]:
        """
        Check all positions for stop loss violations
        
        Returns:
            List of (symbol, reason, details) tuples for positions to close
        """
        to_close = []
        
        if not self.daily_start_value:
            self.daily_start_value = account_value
        
        # Check daily stop loss
        daily_pl_pct = ((account_value - self.daily_start_value) / self.daily_start_value) * 100
        if daily_pl_pct <= -self.daily_stop_loss_pct:
            self.logger.error(
                f"DAILY STOP LOSS TRIGGERED: {daily_pl_pct:.2f}% "
                f"(limit: -{self.daily_stop_loss_pct}%)"
            )
            # Close all positions
            for pos in positions:
                to_close.append((
                    pos.symbol,
                    "daily_stop_loss",
                    {
                        'daily_pl_pct': daily_pl_pct,
                        'limit': -self.daily_stop_loss_pct,
                        'position_pl_pct': pos.unrealized_plpc
                    }
                ))
            return to_close
        
        # Check individual position stop losses
        for pos in positions:
            # Track highest price for trailing stops
            if pos.symbol not in self.position_highs:
                self.position_highs[pos.symbol] = pos.current_price
            else:
                self.position_highs[pos.symbol] = max(
                    self.position_highs[pos.symbol],
                    pos.current_price
                )
            
            # Fixed stop loss
            if pos.stop_loss_price and pos.current_price <= pos.stop_loss_price:
                self.logger.warning(
                    f"STOP LOSS: {pos.symbol} at ${pos.current_price:.2f} "
                    f"<= ${pos.stop_loss_price:.2f}"
                )
                to_close.append((
                    pos.symbol,
                    "fixed_stop_loss",
                    {
                        'current_price': pos.current_price,
                        'stop_price': pos.stop_loss_price,
                        'pl_pct': pos.unrealized_plpc
                    }
                ))
                continue
            
            # Percentage-based stop loss
            pl_pct = pos.unrealized_plpc
            stop_threshold = -(self.trade_stop_loss_bps / 100.0)
            if pl_pct <= stop_threshold:
                self.logger.warning(
                    f"STOP LOSS: {pos.symbol} P/L {pl_pct:.2f}% "
                    f"<= {stop_threshold:.2f}%"
                )
                to_close.append((
                    pos.symbol,
                    "percentage_stop_loss",
                    {
                        'pl_pct': pl_pct,
                        'threshold': stop_threshold,
                        'pl_amount': pos.unrealized_pl
                    }
                ))
                continue
            
            # Trailing stop loss
            if self.trailing_stop_enabled:
                high_price = self.position_highs[pos.symbol]
                trailing_stop_price = high_price * (1 - self.trailing_stop_pct / 100.0)
                
                if pos.current_price <= trailing_stop_price:
                    self.logger.info(
                        f"TRAILING STOP: {pos.symbol} at ${pos.current_price:.2f}, "
                        f"high was ${high_price:.2f}, stop at ${trailing_stop_price:.2f}"
                    )
                    to_close.append((
                        pos.symbol,
                        "trailing_stop",
                        {
                            'current_price': pos.current_price,
                            'high_price': high_price,
                            'trailing_stop_price': trailing_stop_price,
                            'pl_pct': pos.unrealized_plpc
                        }
                    ))
                    continue
            
            # Time-based exit
            if pos.entry_time:
                hold_time = datetime.now() - pos.entry_time
                if hold_time > timedelta(minutes=self.max_hold_time_minutes):
                    self.logger.info(
                        f"TIME EXIT: {pos.symbol} held for {hold_time.total_seconds()/60:.0f}min "
                        f"(max: {self.max_hold_time_minutes}min), P/L: {pl_pct:.2f}%"
                    )
                    to_close.append((
                        pos.symbol,
                        "time_exit",
                        {
                            'hold_minutes': hold_time.total_seconds() / 60,
                            'max_minutes': self.max_hold_time_minutes,
                            'pl_pct': pos.unrealized_plpc
                        }
                    ))
        
        return to_close
    
    def check_take_profit(
        self,
        positions: List[Position],
        take_profit_pct: float = 2.0
    ) -> List[Tuple[str, str, Dict]]:
        """
        Check positions for take profit targets
        
        Args:
            positions: List of current positions
            take_profit_pct: Profit percentage to trigger exit
            
        Returns:
            List of (symbol, reason, details) for positions to close
        """
        to_close = []
        
        for pos in positions:
            if pos.unrealized_plpc >= take_profit_pct:
                self.logger.info(
                    f"TAKE PROFIT: {pos.symbol} at {pos.unrealized_plpc:.2f}% "
                    f"(target: {take_profit_pct}%)"
                )
                to_close.append((
                    pos.symbol,
                    "take_profit",
                    {
                        'pl_pct': pos.unrealized_plpc,
                        'target_pct': take_profit_pct,
                        'pl_amount': pos.unrealized_pl
                    }
                ))
        
        return to_close
    
    def validate_order(
        self,
        symbol: str,
        qty: int,
        price: float,
        account_value: float,
        buying_power: float,
        current_positions: List[Position]
    ) -> Tuple[bool, str]:
        """
        Validate order before submission
        
        Returns:
            Tuple of (is_valid, reason)
        """
        # Basic validation
        if qty < 1:
            return False, "Quantity must be >= 1"
        
        if price <= 0:
            return False, "Invalid price"
        
        # Check daily trade limit
        if self.daily_trades_count >= self.daily_max_trades:
            return False, f"Daily trade limit reached ({self.daily_max_trades})"
        
        # Calculate order value
        order_value = qty * price
        
        # Check buying power
        if order_value > buying_power:
            return False, f"Insufficient buying power: ${buying_power:,.2f} < ${order_value:,.2f}"
        
        # Check position size limit
        position_size_pct = (order_value / account_value) * 100
        if position_size_pct > self.max_position_size_pct:
            return False, (
                f"Position size {position_size_pct:.1f}% exceeds "
                f"limit of {self.max_position_size_pct}%"
            )
        
        # Check max positions
        if len(current_positions) >= self.max_positions:
            # Check if we already have this position (adding to it is ok)
            has_position = any(p.symbol == symbol for p in current_positions)
            if not has_position:
                return False, f"Maximum positions ({self.max_positions}) reached"
        
        # Check total exposure
        current_exposure = sum(p.market_value for p in current_positions)
        new_exposure = current_exposure + order_value
        exposure_pct = (new_exposure / account_value) * 100
        
        if exposure_pct > self.max_total_exposure_pct:
            return False, (
                f"Total exposure {exposure_pct:.1f}% would exceed "
                f"limit of {self.max_total_exposure_pct}%"
            )
        
        return True, "Order validated"
    
    def calculate_risk_metrics(
        self,
        positions: List[Position],
        account_value: float
    ) -> RiskMetrics:
        """Calculate current portfolio risk metrics"""
        if not self.daily_start_value:
            self.daily_start_value = account_value
        
        total_exposure = sum(p.market_value for p in positions)
        total_exposure_pct = (total_exposure / account_value * 100) if account_value > 0 else 0
        
        positions_at_risk = sum(
            1 for p in positions
            if p.unrealized_plpc < -(self.trade_stop_loss_bps / 200.0)  # Half of stop loss
        )
        
        daily_pl_pct = (
            ((account_value - self.daily_start_value) / self.daily_start_value) * 100
            if self.daily_start_value > 0 else 0
        )
        
        max_position_value = max((p.market_value for p in positions), default=0)
        max_position_pct = (max_position_value / account_value * 100) if account_value > 0 else 0
        
        # Determine risk level
        violations = []
        risk_level = RiskLevel.LOW
        
        if total_exposure_pct > self.max_total_exposure_pct:
            violations.append(f"Exposure {total_exposure_pct:.1f}% > {self.max_total_exposure_pct}%")
            risk_level = RiskLevel.HIGH
        
        if daily_pl_pct < -self.daily_stop_loss_pct / 2:
            violations.append(f"Daily P/L {daily_pl_pct:.2f}% approaching stop")
            risk_level = max(risk_level, RiskLevel.MEDIUM, key=lambda x: x.value)
        
        if len(positions) > self.max_positions:
            violations.append(f"Positions {len(positions)} > {self.max_positions}")
            risk_level = RiskLevel.HIGH
        
        if positions_at_risk > len(positions) / 2:
            violations.append(f"{positions_at_risk} positions at risk")
            risk_level = max(risk_level, RiskLevel.HIGH, key=lambda x: x.value)
        
        if daily_pl_pct <= -self.daily_stop_loss_pct:
            violations.append("DAILY STOP LOSS TRIGGERED")
            risk_level = RiskLevel.CRITICAL
        
        return RiskMetrics(
            total_exposure_pct=total_exposure_pct,
            positions_at_risk=positions_at_risk,
            daily_pl_pct=daily_pl_pct,
            max_position_size_pct=max_position_pct,
            risk_level=risk_level,
            violations=violations
        )
    
    def should_close_eod(self, market_close_time: datetime) -> bool:
        """Check if we should close all positions at end of day"""
        if not self.close_all_eod:
            return False
        
        now = datetime.now()
        minutes_to_close = (market_close_time - now).total_seconds() / 60
        
        # Close all positions 15 minutes before market close
        return minutes_to_close <= 15
    
    def increment_trade_count(self):
        """Increment daily trade counter"""
        self.daily_trades_count += 1
        self.logger.debug(f"Trade count: {self.daily_trades_count}/{self.daily_max_trades}")
