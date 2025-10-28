"""
Alpaca Broker Wrapper - Complete Implementation
"""
import logging
import os
import requests
from typing import Dict, List, Optional
from datetime import datetime


class AlpacaBroker:
    """
    Alpaca broker wrapper for trading operations
    Renamed from BrokerAlpaca to match engine.py import
    """

    def __init__(self, key=None, secret=None, paper=True, timeout=6.0, data_feed='iex', logger=None):
        self.key = key or os.getenv('ALPACA_API_KEY_ID')
        self.secret = secret or os.getenv('ALPACA_API_SECRET_KEY')
        self.paper = paper
        self.timeout = timeout
        self.data_feed = data_feed
        self.logger = logger or logging.getLogger(__name__)

        # Set base URLs
        if self.paper:
            self.base_url = 'https://paper-api.alpaca.markets'
            self.data_url = 'https://data.alpaca.markets'
        else:
            self.base_url = 'https://api.alpaca.markets'
            self.data_url = 'https://data.alpaca.markets'

        # Set up headers
        self.headers = {
            'APCA-API-KEY-ID': self.key,
            'APCA-API-SECRET-KEY': self.secret
        }

        self.logger.info(f"Alpaca broker initialized (paper={paper}, feed={data_feed})")

    def _make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """Make HTTP request with error handling"""
        try:
            kwargs.setdefault('timeout', self.timeout)
            kwargs.setdefault('headers', self.headers)

            response = requests.request(method, url, **kwargs)
            response.raise_for_status()

            return response.json() if response.content else None

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                self.logger.warning(f"Bad request: {method} {url}: {e.response.text}")
            elif e.response.status_code == 403:
                self.logger.error(f"Access forbidden (check API permissions): {method} {url}")
            elif e.response.status_code == 422:
                self.logger.warning(f"Unprocessable entity: {method} {url}: {e.response.text}")
            else:
                self.logger.error(f"HTTP error {e.response.status_code}: {method} {url}: {e}")
            return None
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {method} {url}: {e}")
            return None

    def get_clock(self) -> Optional[Dict]:
        """Get market clock status"""
        url = f"{self.base_url}/v2/clock"
        return self._make_request('GET', url)

    def get_account(self) -> Optional[Dict]:
        """Get account information"""
        url = f"{self.base_url}/v2/account"
        return self._make_request('GET', url)

    def list_positions(self) -> List[Dict]:
        """List current positions"""
        url = f"{self.base_url}/v2/positions"
        result = self._make_request('GET', url)
        return result if result else []

    def get_positions(self) -> List[Dict]:
        """Alias for list_positions"""
        return self.list_positions()

    def snapshots_batch_stocks(self, symbols: List[str]) -> Dict:
        """
        Get batch snapshots for stock symbols
        Returns dict of {symbol: snapshot_data}
        """
        if not symbols:
            return {}

        # Alpaca limits batch size
        batch_size = 100
        all_snapshots = {}

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            symbols_str = ','.join(batch)

            url = f"{self.data_url}/v2/stocks/snapshots"
            params = {
                'symbols': symbols_str,
                'feed': self.data_feed
            }

            result = self._make_request('GET', url, params=params)

            if result and isinstance(result, dict):
                all_snapshots.update(result)

        return all_snapshots

    def snapshots_batch_crypto(self, symbols: List[str]) -> Dict:
        """
        Get batch snapshots for crypto symbols
        Returns dict of {symbol: snapshot_data}
        """
        if not symbols:
            return {}

        self.logger.debug(f"Getting crypto snapshots for: {symbols}")

        # Alpaca crypto API requires format: BTC/USD (with slash)
        # API expects: ^[A-Z]+/[A-Z]+$
        snapshots = {}

        for symbol in symbols:
            try:
                # Ensure symbol has slash format
                if '/' not in symbol:
                    # Convert BTCUSD -> BTC/USD
                    if symbol.endswith('USD'):
                        alpaca_symbol = symbol[:-3] + '/' + symbol[-3:]
                    else:
                        self.logger.warning(f"Invalid crypto symbol format: {symbol}")
                        continue
                else:
                    alpaca_symbol = symbol

                # Use v1beta3 latest quote endpoint for crypto
                url = f"{self.data_url}/v1beta3/crypto/us/latest/quotes"
                params = {'symbols': alpaca_symbol}

                result = self._make_request('GET', url, params=params)

                if result and 'quotes' in result and alpaca_symbol in result['quotes']:
                    quote_data = result['quotes'][alpaca_symbol]

                    # Convert to snapshot format similar to stocks
                    snapshot = {
                        'latestQuote': {
                            'ap': quote_data.get('ap', 0),  # ask price
                            'bp': quote_data.get('bp', 0),  # bid price
                            'as': quote_data.get('as', 0),  # ask size
                            'bs': quote_data.get('bs', 0),  # bid size
                            't': quote_data.get('t', '')    # timestamp
                        },
                        'latestTrade': {
                            'p': quote_data.get('ap', 0),   # use ask as last price fallback
                            't': quote_data.get('t', '')
                        }
                    }
                    snapshots[symbol] = snapshot
                    self.logger.debug(f"Got crypto snapshot for {symbol}")
                else:
                    self.logger.warning(f"No crypto data for {symbol}")

            except Exception as e:
                self.logger.error(f"Error getting crypto snapshot for {symbol}: {e}")
                continue

        return snapshots

    def snapshot(self, symbol: str) -> Optional[Dict]:
        """
        Get snapshot for a single symbol (compatibility wrapper).
        Routes to the appropriate batch method based on symbol type.

        Args:
            symbol: Stock symbol (e.g., 'AAPL') or crypto pair (e.g., 'BTC/USD')

        Returns:
            Snapshot data dict or None if not available
        """
        # Determine if it's crypto based on symbol format
        is_crypto = '/' in symbol or symbol.upper().endswith('USD')

        try:
            if is_crypto:
                # Use crypto batch method
                snapshots = self.snapshots_batch_crypto([symbol])
            else:
                # Use stock batch method
                snapshots = self.snapshots_batch_stocks([symbol])

            # Return the snapshot for this symbol if available
            if snapshots and symbol in snapshots:
                return snapshots[symbol]
            else:
                return None

        except Exception as e:
            self.logger.error(f"Error getting snapshot for {symbol}: {e}")
            return None

    def get_batch_snapshots(self, symbols: List[str]) -> Dict:
        """
        Get batch snapshots for mixed stock and crypto symbols
        Automatically routes to appropriate endpoint
        """
        if not symbols:
            return {}

        # Separate stocks and crypto
        stocks = []
        crypto = []

        for symbol in symbols:
            if '/' in symbol or symbol.upper().endswith('USD'):
                crypto.append(symbol)
            else:
                stocks.append(symbol)

        all_snapshots = {}

        # Get stock snapshots
        if stocks:
            stock_snaps = self.snapshots_batch_stocks(stocks)
            all_snapshots.update(stock_snaps)

        # Get crypto snapshots
        if crypto:
            crypto_snaps = self.snapshots_batch_crypto(crypto)
            all_snapshots.update(crypto_snaps)

        return all_snapshots

    def mid_from_snapshot(self, snapshot: Dict) -> Optional[float]:
        """
        Calculate mid price from snapshot data.
        """
        if not snapshot:
            return None

        quote = snapshot.get('latestQuote', {})
        bid = quote.get('bp', 0) or quote.get('bidPrice', 0)
        ask = quote.get('ap', 0) or quote.get('askPrice', 0)

        if bid > 0 and ask > 0:
            return (bid + ask) / 2

        # Fallback to latest trade
        trade = snapshot.get('latestTrade', {})
        return trade.get('p', 0) or trade.get('price', 0)

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = 'market',
        time_in_force: str = 'day',
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Optional[Dict]:
        """
        Place an order

        Args:
            symbol: Symbol to trade
            qty: Quantity
            side: 'buy' or 'sell'
            order_type: 'market', 'limit', 'stop', 'stop_limit'
            time_in_force: 'day', 'gtc', 'ioc', 'fok'
            limit_price: Limit price (for limit orders)
            stop_price: Stop price (for stop orders)
        """
        url = f"{self.base_url}/v2/orders"

        data = {
            'symbol': symbol,
            'qty': qty,
            'side': side,
            'type': order_type,
            'time_in_force': time_in_force
        }

        if limit_price:
            data['limit_price'] = str(limit_price)
        if stop_price:
            data['stop_price'] = str(stop_price)

        result = self._make_request('POST', url, json=data)

        if result:
            self.logger.info(f"Order placed: {side} {qty} {symbol} @ {order_type}")
        else:
            self.logger.error(f"Failed to place order: {side} {qty} {symbol}")

        return result

    def submit_order(self, **kwargs) -> Optional[Dict]:
        """Alias for place_order"""
        return self.place_order(**kwargs)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        url = f"{self.base_url}/v2/orders/{order_id}"
        result = self._make_request('DELETE', url)
        return result is not None

    def get_order(self, order_id: str) -> Optional[Dict]:
        """Get order details"""
        url = f"{self.base_url}/v2/orders/{order_id}"
        return self._make_request('GET', url)

    def list_orders(self, status: str = 'open') -> List[Dict]:
        """List orders"""
        url = f"{self.base_url}/v2/orders"
        params = {'status': status}
        result = self._make_request('GET', url, params=params)
        return result if result else []

    def close_position(self, symbol: str) -> Optional[Dict]:
        """Close a position"""
        url = f"{self.base_url}/v2/positions/{symbol}"
        return self._make_request('DELETE', url)

    def close_all_positions(self) -> List[Dict]:
        """Close all positions"""
        url = f"{self.base_url}/v2/positions"
        result = self._make_request('DELETE', url)
        return result if result else []

    def place_stop_loss_order(
        self,
        symbol: str,
        qty: int,
        stop_price: float,
        time_in_force: str = 'gtc'
    ) -> Optional[Dict]:
        """
        Place a stop-loss order for an existing position

        Args:
            symbol: Symbol to protect
            qty: Quantity (must match position size)
            stop_price: Stop-loss trigger price
            time_in_force: 'gtc' (good til cancelled) or 'day'

        Returns:
            Order dict with order_id if successful
        """
        return self.place_order(
            symbol=symbol,
            qty=qty,
            side='sell',
            order_type='stop',
            stop_price=stop_price,
            time_in_force=time_in_force
        )

    def replace_stop_loss_order(
        self,
        old_order_id: str,
        symbol: str,
        qty: int,
        new_stop_price: float,
        time_in_force: str = 'gtc'
    ) -> Optional[Dict]:
        """
        Replace an existing stop-loss order with a new stop price
        Cancels old order and places new one atomically

        Args:
            old_order_id: ID of existing stop order to cancel
            symbol: Symbol
            qty: Quantity
            new_stop_price: New stop-loss trigger price
            time_in_force: 'gtc' or 'day'

        Returns:
            New order dict if successful, None if failed
        """
        # First, try to cancel the old order
        if old_order_id:
            cancel_success = self.cancel_order(old_order_id)
            if not cancel_success:
                self.logger.warning(
                    f"Failed to cancel old stop order {old_order_id}, "
                    f"attempting to place new order anyway"
                )

        # Place new stop-loss order
        new_order = self.place_stop_loss_order(
            symbol=symbol,
            qty=qty,
            stop_price=new_stop_price,
            time_in_force=time_in_force
        )

        if new_order:
            self.logger.info(
                f"Replaced stop-loss for {symbol}: "
                f"old_order={old_order_id}, new_stop=${new_stop_price:.2f}"
            )

        return new_order


# Alias for backward compatibility
BrokerAlpaca = AlpacaBroker