#!/usr/bin/env python3
"""
Test Script for After-Hours Crypto and FOREX Trading

This script tests all trade call types (market, limit, stop) for crypto and FOREX symbols
to verify they work correctly during after-hours trading.

Usage:
    # Dry run (no actual orders, just validation)
    python test_after_hours_trades.py --dry-run

    # Live test (places actual orders - use small quantities!)
    python test_after_hours_trades.py --live

    # Live test with custom symbols
    python test_after_hours_trades.py --live --crypto BTC/USD ETH/USD --forex EUR/USD

    # Cleanup mode (cancel all test orders)
    python test_after_hours_trades.py --cleanup
"""

import sys
import os
import argparse
import logging
import time
from datetime import datetime
from typing import List, Dict, Optional

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from trading_bot.broker_alpaca import AlpacaBroker


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TradeTestSuite:
    """Test suite for crypto and forex trading"""

    def __init__(self, broker: AlpacaBroker, dry_run: bool = True):
        self.broker = broker
        self.dry_run = dry_run
        self.results = {
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'orders_placed': [],
            'errors': []
        }

    def log_result(self, test_name: str, success: bool, message: str, order_id: Optional[str] = None):
        """Log test result"""
        status = "PASS" if success else "FAIL"
        logger.info(f"[{status}] {test_name}: {message}")

        if success:
            self.results['passed'] += 1
            if order_id:
                self.results['orders_placed'].append(order_id)
        else:
            self.results['failed'] += 1
            self.results['errors'].append(f"{test_name}: {message}")

    def test_market_status(self) -> Dict:
        """Test 1: Check market status"""
        logger.info("\n=== Test 1: Market Status ===")

        clock = self.broker.get_clock()
        if not clock:
            self.log_result("Market Status", False, "Failed to get clock data")
            return {}

        is_open = clock.get('is_open', False)
        timestamp = clock.get('timestamp', 'unknown')
        next_open = clock.get('next_open', 'unknown')
        next_close = clock.get('next_close', 'unknown')

        logger.info(f"Market is {'OPEN' if is_open else 'CLOSED'}")
        logger.info(f"Timestamp: {timestamp}")
        logger.info(f"Next open: {next_open}")
        logger.info(f"Next close: {next_close}")

        # After-hours test is meaningful when market is closed
        if is_open:
            logger.warning("Market is currently OPEN - this test is designed for after-hours")
        else:
            logger.info("Market is CLOSED - perfect for after-hours testing!")

        self.log_result("Market Status", True, f"Market is {'open' if is_open else 'closed'}")
        return clock

    def test_account_info(self) -> Optional[Dict]:
        """Test 2: Verify account access"""
        logger.info("\n=== Test 2: Account Information ===")

        account = self.broker.get_account()
        if not account:
            self.log_result("Account Info", False, "Failed to get account data")
            return None

        cash = float(account.get('cash', 0))
        buying_power = float(account.get('buying_power', 0))
        portfolio_value = float(account.get('portfolio_value', 0))

        logger.info(f"Cash: ${cash:,.2f}")
        logger.info(f"Buying Power: ${buying_power:,.2f}")
        logger.info(f"Portfolio Value: ${portfolio_value:,.2f}")

        self.log_result("Account Info", True, f"Cash: ${cash:,.2f}, Buying Power: ${buying_power:,.2f}")
        return account

    def test_symbol_snapshot(self, symbol: str) -> Optional[Dict]:
        """Test getting market data for a symbol"""
        snapshot = self.broker.snapshot(symbol)

        if not snapshot:
            self.log_result(f"Snapshot {symbol}", False, "No data available")
            return None

        mid_price = self.broker.mid_from_snapshot(snapshot)
        quote = snapshot.get('latestQuote', {})
        bid = quote.get('bp', 0)
        ask = quote.get('ap', 0)

        logger.info(f"  {symbol}: Bid=${bid:.2f}, Ask=${ask:.2f}, Mid=${mid_price:.2f}")
        self.log_result(f"Snapshot {symbol}", True, f"Mid price: ${mid_price:.2f}")

        return snapshot

    def test_market_order(self, symbol: str, qty: int, side: str) -> Optional[str]:
        """Test market order placement"""
        test_name = f"Market {side.upper()} {symbol}"
        logger.info(f"\nTesting: {test_name} (qty={qty})")

        if self.dry_run:
            logger.info(f"  [DRY RUN] Would place: {side} {qty} {symbol} @ market")
            self.log_result(test_name, True, "Dry run - no order placed")
            return None

        order = self.broker.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type='market',
            time_in_force='gtc'  # GTC for crypto/forex (24hr trading)
        )

        if order:
            order_id = order.get('id')
            status = order.get('status')
            self.log_result(test_name, True, f"Order placed (ID: {order_id}, Status: {status})", order_id)
            return order_id
        else:
            self.log_result(test_name, False, "Order placement failed")
            return None

    def test_limit_order(self, symbol: str, qty: int, side: str, limit_price: float) -> Optional[str]:
        """Test limit order placement"""
        test_name = f"Limit {side.upper()} {symbol}"
        logger.info(f"\nTesting: {test_name} (qty={qty}, limit=${limit_price:.2f})")

        if self.dry_run:
            logger.info(f"  [DRY RUN] Would place: {side} {qty} {symbol} @ limit ${limit_price:.2f}")
            self.log_result(test_name, True, "Dry run - no order placed")
            return None

        order = self.broker.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type='limit',
            limit_price=limit_price,
            time_in_force='gtc'
        )

        if order:
            order_id = order.get('id')
            status = order.get('status')
            self.log_result(test_name, True, f"Order placed (ID: {order_id}, Status: {status})", order_id)
            return order_id
        else:
            self.log_result(test_name, False, "Order placement failed")
            return None

    def test_stop_order(self, symbol: str, qty: int, side: str, stop_price: float) -> Optional[str]:
        """Test stop order placement"""
        test_name = f"Stop {side.upper()} {symbol}"
        logger.info(f"\nTesting: {test_name} (qty={qty}, stop=${stop_price:.2f})")

        if self.dry_run:
            logger.info(f"  [DRY RUN] Would place: {side} {qty} {symbol} @ stop ${stop_price:.2f}")
            self.log_result(test_name, True, "Dry run - no order placed")
            return None

        order = self.broker.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type='stop',
            stop_price=stop_price,
            time_in_force='gtc'
        )

        if order:
            order_id = order.get('id')
            status = order.get('status')
            self.log_result(test_name, True, f"Order placed (ID: {order_id}, Status: {status})", order_id)
            return order_id
        else:
            self.log_result(test_name, False, "Order placement failed")
            return None

    def test_crypto_trading(self, symbols: List[str], qty: int = 1):
        """Test crypto trading with all order types"""
        logger.info("\n" + "="*60)
        logger.info("=== CRYPTO TRADING TESTS ===")
        logger.info("="*60)

        for symbol in symbols:
            logger.info(f"\n--- Testing {symbol} ---")

            # Get market data
            snapshot = self.test_symbol_snapshot(symbol)
            if not snapshot:
                logger.warning(f"Skipping {symbol} - no market data")
                continue

            mid_price = self.broker.mid_from_snapshot(snapshot)
            if not mid_price or mid_price <= 0:
                logger.warning(f"Skipping {symbol} - invalid price")
                continue

            # Test market buy
            self.test_market_order(symbol, qty, 'buy')
            time.sleep(1)  # Rate limiting

            # Test limit buy (below market)
            limit_price = mid_price * 0.95  # 5% below market
            self.test_limit_order(symbol, qty, 'buy', limit_price)
            time.sleep(1)

            # Test stop sell (below market, for stop-loss)
            stop_price = mid_price * 0.90  # 10% below market
            self.test_stop_order(symbol, qty, 'sell', stop_price)
            time.sleep(1)

    def test_forex_trading(self, symbols: List[str], qty: int = 100):
        """Test forex trading with all order types"""
        logger.info("\n" + "="*60)
        logger.info("=== FOREX TRADING TESTS ===")
        logger.info("="*60)

        for symbol in symbols:
            logger.info(f"\n--- Testing {symbol} ---")

            # Get market data
            snapshot = self.test_symbol_snapshot(symbol)
            if not snapshot:
                logger.warning(f"Skipping {symbol} - no market data")
                logger.warning(f"Note: Alpaca may not support all forex pairs")
                continue

            mid_price = self.broker.mid_from_snapshot(snapshot)
            if not mid_price or mid_price <= 0:
                logger.warning(f"Skipping {symbol} - invalid price")
                continue

            # Test market buy
            self.test_market_order(symbol, qty, 'buy')
            time.sleep(1)

            # Test limit buy (below market)
            limit_price = round(mid_price * 0.9995, 4)  # Slightly below market
            self.test_limit_order(symbol, qty, 'buy', limit_price)
            time.sleep(1)

            # Test stop sell (below market)
            stop_price = round(mid_price * 0.999, 4)
            self.test_stop_order(symbol, qty, 'sell', stop_price)
            time.sleep(1)

    def check_order_status(self, order_id: str) -> Optional[Dict]:
        """Check status of an order"""
        order = self.broker.get_order(order_id)
        if order:
            status = order.get('status')
            symbol = order.get('symbol')
            side = order.get('side')
            qty = order.get('qty')
            order_type = order.get('type')
            logger.info(f"  Order {order_id}: {status} - {side} {qty} {symbol} @ {order_type}")
        return order

    def print_summary(self):
        """Print test summary"""
        logger.info("\n" + "="*60)
        logger.info("=== TEST SUMMARY ===")
        logger.info("="*60)
        logger.info(f"Tests Passed: {self.results['passed']}")
        logger.info(f"Tests Failed: {self.results['failed']}")
        logger.info(f"Tests Skipped: {self.results['skipped']}")

        if self.results['orders_placed']:
            logger.info(f"\nOrders Placed: {len(self.results['orders_placed'])}")
            for order_id in self.results['orders_placed']:
                self.check_order_status(order_id)

        if self.results['errors']:
            logger.info("\nErrors:")
            for error in self.results['errors']:
                logger.error(f"  - {error}")

        success_rate = (self.results['passed'] /
                       (self.results['passed'] + self.results['failed']) * 100
                       if (self.results['passed'] + self.results['failed']) > 0 else 0)
        logger.info(f"\nSuccess Rate: {success_rate:.1f}%")

        return success_rate >= 80  # Consider test suite successful if 80%+ pass


def cleanup_orders(broker: AlpacaBroker):
    """Cancel all open orders"""
    logger.info("\n=== CLEANUP: Canceling All Open Orders ===")

    orders = broker.list_orders(status='open')
    if not orders:
        logger.info("No open orders to cancel")
        return

    logger.info(f"Found {len(orders)} open orders")
    for order in orders:
        order_id = order.get('id')
        symbol = order.get('symbol')
        side = order.get('side')
        qty = order.get('qty')
        order_type = order.get('type')

        logger.info(f"Canceling: {order_id} - {side} {qty} {symbol} @ {order_type}")
        success = broker.cancel_order(order_id)
        if success:
            logger.info(f"  ✓ Canceled")
        else:
            logger.warning(f"  ✗ Failed to cancel")
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description='Test after-hours crypto and forex trading')
    parser.add_argument('--dry-run', action='store_true', default=False,
                       help='Dry run mode (no actual orders)')
    parser.add_argument('--live', action='store_true', default=False,
                       help='Live mode (places actual orders)')
    parser.add_argument('--cleanup', action='store_true', default=False,
                       help='Cleanup mode (cancel all open orders)')
    parser.add_argument('--crypto', nargs='+', default=['BTC/USD', 'ETH/USD', 'SOL/USD'],
                       help='Crypto symbols to test')
    parser.add_argument('--forex', nargs='+', default=['EUR/USD', 'GBP/USD'],
                       help='Forex symbols to test')
    parser.add_argument('--crypto-qty', type=int, default=1,
                       help='Quantity for crypto orders (default: 1)')
    parser.add_argument('--forex-qty', type=int, default=100,
                       help='Quantity for forex orders (default: 100)')

    args = parser.parse_args()

    # Validate mode
    if args.live and args.dry_run:
        logger.error("Cannot use both --live and --dry-run")
        sys.exit(1)

    if not args.live and not args.dry_run and not args.cleanup:
        logger.info("No mode specified, defaulting to --dry-run")
        args.dry_run = True

    # Initialize broker
    logger.info("Initializing Alpaca broker...")
    broker = AlpacaBroker(paper=True, logger=logger)

    if not broker.key or not broker.secret:
        logger.error("Alpaca API credentials not found!")
        logger.error("Please set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY environment variables")
        sys.exit(1)

    # Cleanup mode
    if args.cleanup:
        cleanup_orders(broker)
        return

    # Run tests
    mode = "LIVE" if args.live else "DRY RUN"
    logger.info(f"\n{'='*60}")
    logger.info(f"=== AFTER-HOURS TRADING TEST SUITE ({mode}) ===")
    logger.info(f"{'='*60}")
    logger.info(f"Mode: {mode}")
    logger.info(f"Crypto symbols: {args.crypto}")
    logger.info(f"Forex symbols: {args.forex}")
    logger.info(f"Crypto qty: {args.crypto_qty}")
    logger.info(f"Forex qty: {args.forex_qty}")

    if args.live:
        logger.warning("\n⚠️  WARNING: LIVE MODE - REAL ORDERS WILL BE PLACED ⚠️")
        logger.warning("Make sure you're using paper trading and small quantities!")
        response = input("\nType 'YES' to continue: ")
        if response != 'YES':
            logger.info("Aborted by user")
            sys.exit(0)

    # Create test suite
    test_suite = TradeTestSuite(broker, dry_run=args.dry_run)

    # Run basic tests
    test_suite.test_market_status()
    account = test_suite.test_account_info()

    if not account:
        logger.error("Cannot proceed without account access")
        sys.exit(1)

    # Run crypto tests
    if args.crypto:
        test_suite.test_crypto_trading(args.crypto, qty=args.crypto_qty)

    # Run forex tests
    if args.forex:
        test_suite.test_forex_trading(args.forex, qty=args.forex_qty)

    # Print summary
    success = test_suite.print_summary()

    # Offer to cleanup
    if args.live and test_suite.results['orders_placed']:
        logger.info("\n" + "="*60)
        response = input("\nCancel all test orders? (y/n): ")
        if response.lower() == 'y':
            cleanup_orders(broker)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
