"""
Order Validation Module
Pre-flight checks before submitting orders to broker
"""
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ValidationResult:
    """Result of order validation"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    checks_passed: List[str]
    
    def add_error(self, error: str):
        self.errors.append(error)
        self.is_valid = False
    
    def add_warning(self, warning: str):
        self.warnings.append(warning)
    
    def add_check(self, check: str):
        self.checks_passed.append(check)


class OrderValidator:
    """
    Comprehensive order validation
    Catches issues before sending to broker
    """
    
    def __init__(self, settings: Dict, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)
    
    def validate_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None,
        time_in_force: str = 'day',
        account_info: Optional[Dict] = None,
        current_positions: Optional[List[Dict]] = None,
        market_data: Optional[Dict] = None
    ) -> ValidationResult:
        """
        Validate an order before submission
        
        Args:
            symbol: Stock symbol
            side: 'buy' or 'sell'
            qty: Quantity
            order_type: 'market', 'limit', 'stop', 'stop_limit'
            price: Current/estimated fill price
            stop_price: Stop price for stop orders
            limit_price: Limit price for limit orders
            time_in_force: Order duration
            account_info: Account data (equity, buying_power, etc.)
            current_positions: List of current positions
            market_data: Current market data for symbol
            
        Returns:
            ValidationResult with details
        """
        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            checks_passed=[]
        )
        
        # Basic validation
        self._validate_basic_params(symbol, side, qty, order_type, result)
        
        # Symbol validation
        self._validate_symbol(symbol, market_data, result)
        
        # Quantity validation
        self._validate_quantity(qty, result)
        
        # Price validation
        self._validate_prices(
            order_type, price, stop_price, limit_price,
            market_data, side, result
        )
        
        # Time in force validation
        self._validate_time_in_force(time_in_force, order_type, result)
        
        # Account validation
        if account_info:
            self._validate_account(
                side, qty, price or 0, account_info, result
            )
        
        # Position validation
        if current_positions is not None:
            self._validate_positions(
                symbol, side, qty, price or 0,
                current_positions, account_info, result
            )
        
        # Market data validation
        if market_data:
            self._validate_market_conditions(
                symbol, side, qty, price, market_data, result
            )
        
        return result
    
    def _validate_basic_params(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str,
        result: ValidationResult
    ):
        """Validate basic order parameters"""
        if not symbol or not isinstance(symbol, str):
            result.add_error("Symbol must be a non-empty string")
        else:
            result.add_check("Symbol format valid")
        
        if side not in ['buy', 'sell']:
            result.add_error(f"Side must be 'buy' or 'sell', got '{side}'")
        else:
            result.add_check(f"Side '{side}' is valid")
        
        if not isinstance(qty, int) or qty < 1:
            result.add_error(f"Quantity must be positive integer, got {qty}")
        else:
            result.add_check(f"Quantity {qty} is valid")
        
        valid_types = ['market', 'limit', 'stop', 'stop_limit']
        if order_type not in valid_types:
            result.add_error(f"Order type must be one of {valid_types}, got '{order_type}'")
        else:
            result.add_check(f"Order type '{order_type}' is valid")
    
    def _validate_symbol(
        self,
        symbol: str,
        market_data: Optional[Dict],
        result: ValidationResult
    ):
        """Validate symbol format and tradability"""
        # Check for invalid characters
        exclude_patterns = self.settings.get('data', {}).get('exclude_patterns', [])
        for pattern in exclude_patterns:
            if pattern in symbol:
                result.add_warning(f"Symbol contains '{pattern}' - may not be tradeable")
        
        # Check minimum price
        if market_data:
            price = market_data.get('latestTrade', {}).get('p', 0)
            min_price = self.settings.get('data', {}).get('min_price', 5.0)
            max_price = self.settings.get('data', {}).get('max_price', 500.0)
            
            if price < min_price:
                result.add_warning(f"Price ${price:.2f} below minimum ${min_price:.2f}")
            elif price > max_price:
                result.add_warning(f"Price ${price:.2f} above maximum ${max_price:.2f}")
            else:
                result.add_check(f"Price ${price:.2f} within acceptable range")
        
        result.add_check("Symbol format validated")
    
    def _validate_quantity(self, qty: int, result: ValidationResult):
        """Validate order quantity"""
        if qty < 1:
            result.add_error("Quantity must be at least 1")
        elif qty > 10000:
            result.add_warning(f"Large quantity {qty} - verify intentional")
        else:
            result.add_check(f"Quantity {qty} is reasonable")
    
    def _validate_prices(
        self,
        order_type: str,
        price: Optional[float],
        stop_price: Optional[float],
        limit_price: Optional[float],
        market_data: Optional[Dict],
        side: str,
        result: ValidationResult
    ):
        """Validate price parameters"""
        if order_type == 'limit':
            if not limit_price or limit_price <= 0:
                result.add_error("Limit orders require positive limit_price")
                return
            result.add_check(f"Limit price ${limit_price:.2f} is valid")
        
        if order_type in ['stop', 'stop_limit']:
            if not stop_price or stop_price <= 0:
                result.add_error("Stop orders require positive stop_price")
                return
            result.add_check(f"Stop price ${stop_price:.2f} is valid")
        
        if order_type == 'stop_limit':
            if not limit_price or limit_price <= 0:
                result.add_error("Stop-limit orders require positive limit_price")
                return
        
        # Validate against current market price
        if market_data and price:
            current_price = market_data.get('latestTrade', {}).get('p', 0)
            
            if current_price > 0:
                deviation_pct = abs(price - current_price) / current_price * 100
                
                if deviation_pct > 5:
                    result.add_warning(
                        f"Price ${price:.2f} differs from market ${current_price:.2f} "
                        f"by {deviation_pct:.1f}%"
                    )
                else:
                    result.add_check("Price is close to market")
        
        # Check spread
        if market_data:
            quote = market_data.get('latestQuote', {})
            bid = quote.get('bp', 0)
            ask = quote.get('ap', 0)
            
            if bid > 0 and ask > 0:
                spread_bps = ((ask - bid) / bid) * 10000
                min_spread = self.settings.get('thresholds', {}).get('min_spread_bps', 25.0)
                
                if spread_bps > min_spread:
                    result.add_warning(
                        f"Wide spread: {spread_bps:.1f} bps "
                        f"(threshold: {min_spread:.1f} bps)"
                    )
                else:
                    result.add_check(f"Spread {spread_bps:.1f} bps is acceptable")
    
    def _validate_time_in_force(
        self,
        time_in_force: str,
        order_type: str,
        result: ValidationResult
    ):
        """Validate time in force parameter"""
        valid_tif = ['day', 'gtc', 'ioc', 'fok']
        
        if time_in_force not in valid_tif:
            result.add_error(f"Time in force must be one of {valid_tif}")
        else:
            result.add_check(f"Time in force '{time_in_force}' is valid")
        
        # Market orders should typically be day or IOC
        if order_type == 'market' and time_in_force == 'gtc':
            result.add_warning("Market orders with GTC may not be ideal")
    
    def _validate_account(
        self,
        side: str,
        qty: int,
        price: float,
        account_info: Dict,
        result: ValidationResult
    ):
        """Validate against account constraints"""
        if side == 'buy':
            buying_power = float(account_info.get('buying_power', 0))
            order_value = qty * price
            
            # Add estimated commission
            commission = self.settings.get('backtest', {}).get('commission_per_trade', 1.0)
            total_cost = order_value + commission
            
            if total_cost > buying_power:
                result.add_error(
                    f"Insufficient buying power: ${buying_power:,.2f} "
                    f"< ${total_cost:,.2f}"
                )
            elif total_cost > buying_power * 0.95:
                result.add_warning(
                    f"Order uses {(total_cost/buying_power)*100:.1f}% "
                    "of buying power"
                )
            else:
                result.add_check(
                    f"Sufficient buying power (${buying_power:,.2f})"
                )
            
            # Check minimum trade value
            min_value = self.settings.get('risk', {}).get('min_trade_value', 100)
            if order_value < min_value:
                result.add_warning(
                    f"Order value ${order_value:.2f} below minimum ${min_value:.2f}"
                )
        
        elif side == 'sell':
            # For sell orders, just log account state
            cash = float(account_info.get('cash', 0))
            result.add_check(f"Account cash: ${cash:,.2f}")
    
    def _validate_positions(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        current_positions: List[Dict],
        account_info: Optional[Dict],
        result: ValidationResult
    ):
        """Validate against current positions"""
        # Check if position exists
        existing_pos = next(
            (p for p in current_positions if p.get('symbol') == symbol),
            None
        )
        
        if side == 'buy':
            if existing_pos:
                result.add_warning(f"Already have position in {symbol}")
            
            # Check max positions
            max_positions = self.settings.get('risk', {}).get('max_positions', 10)
            if len(current_positions) >= max_positions:
                result.add_error(
                    f"Maximum positions ({max_positions}) reached. "
                    f"Currently holding {len(current_positions)} positions."
                )
            else:
                result.add_check(
                    f"Position count {len(current_positions)}/{max_positions} OK"
                )
            
            # Check position size limits
            if account_info:
                equity = float(account_info.get('equity', 0))
                position_value = qty * price
                position_pct = (position_value / equity * 100) if equity > 0 else 0
                
                max_position_pct = self.settings.get('risk', {}).get(
                    'max_position_size_pct', 5.0
                )
                
                if position_pct > max_position_pct:
                    result.add_error(
                        f"Position size {position_pct:.1f}% exceeds "
                        f"limit of {max_position_pct:.1f}%"
                    )
                else:
                    result.add_check(
                        f"Position size {position_pct:.1f}% within limits"
                    )
                
                # Check total exposure
                current_exposure = sum(
                    float(p.get('market_value', 0))
                    for p in current_positions
                )
                new_exposure = current_exposure + position_value
                exposure_pct = (new_exposure / equity * 100) if equity > 0 else 0
                
                max_exposure = self.settings.get('risk', {}).get(
                    'max_total_exposure_pct', 80.0
                )
                
                if exposure_pct > max_exposure:
                    result.add_error(
                        f"Total exposure {exposure_pct:.1f}% would exceed "
                        f"limit of {max_exposure:.1f}%"
                    )
                else:
                    result.add_check(
                        f"Total exposure {exposure_pct:.1f}% within limits"
                    )
        
        elif side == 'sell':
            if not existing_pos:
                result.add_error(f"No position in {symbol} to sell")
            else:
                pos_qty = abs(float(existing_pos.get('qty', 0)))
                if qty > pos_qty:
                    result.add_error(
                        f"Sell quantity {qty} exceeds position size {pos_qty}"
                    )
                else:
                    result.add_check(f"Sell quantity {qty} <= position {pos_qty}")
    
    def _validate_market_conditions(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: Optional[float],
        market_data: Dict,
        result: ValidationResult
    ):
        """Validate market conditions are suitable for trading"""
        # Check volume
        daily_bar = market_data.get('dailyBar', {})
        volume = daily_bar.get('v', 0)
        
        min_volume = self.settings.get('data', {}).get('min_volume', 100000)
        if volume < min_volume:
            result.add_warning(
                f"Low volume: {volume:,} < {min_volume:,}"
            )
        else:
            result.add_check(f"Volume {volume:,} is adequate")
        
        # Check if trying to trade more than reasonable % of volume
        if volume > 0:
            volume_pct = (qty / volume) * 100
            if volume_pct > 1.0:
                result.add_warning(
                    f"Order is {volume_pct:.2f}% of daily volume - "
                    "may have price impact"
                )
        
        # Check quote freshness
        quote = market_data.get('latestQuote', {})
        quote_time = quote.get('t')
        if quote_time:
            try:
                quote_dt = datetime.fromisoformat(quote_time.replace('Z', '+00:00'))
                age_seconds = (datetime.now() - quote_dt.replace(tzinfo=None)).total_seconds()
                
                if age_seconds > 60:
                    result.add_warning(
                        f"Quote is {age_seconds:.0f} seconds old - may be stale"
                    )
                else:
                    result.add_check("Quote is fresh")
            except Exception:
                result.add_warning("Could not verify quote freshness")
        
        # Check for halts or trading restrictions
        # (This would require additional market status data)
        result.add_check("Market conditions checked")
    
    def format_validation_report(self, result: ValidationResult) -> str:
        """Format validation result as human-readable report"""
        lines = []
        lines.append("=" * 60)
        lines.append("ORDER VALIDATION REPORT")
        lines.append("=" * 60)
        
        if result.is_valid:
            lines.append("✓ VALIDATION PASSED")
        else:
            lines.append("✗ VALIDATION FAILED")
        
        if result.errors:
            lines.append("\nERRORS:")
            for error in result.errors:
                lines.append(f"  ✗ {error}")
        
        if result.warnings:
            lines.append("\nWARNINGS:")
            for warning in result.warnings:
                lines.append(f"  ⚠ {warning}")
        
        if result.checks_passed:
            lines.append("\nCHECKS PASSED:")
            for check in result.checks_passed:
                lines.append(f"  ✓ {check}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


def validate_and_log(
    validator: OrderValidator,
    logger: logging.Logger,
    **order_params
) -> Tuple[bool, str]:
    """
    Convenience function to validate and log results
    
    Returns:
        Tuple of (is_valid, formatted_report)
    """
    result = validator.validate_order(**order_params)
    report = validator.format_validation_report(result)
    
    if result.is_valid:
        if result.warnings:
            logger.warning(f"Order validation passed with warnings:\n{report}")
        else:
            logger.info("Order validation passed")
            logger.debug(report)
    else:
        logger.error(f"Order validation failed:\n{report}")
    
    return result.is_valid, report
