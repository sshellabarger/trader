"""
Alpaca Broker Wrapper - FIXED VERSION
Add this helper method to your BrokerAlpaca class in broker_alpaca.py
"""


class BrokerAlpaca:
    """Your existing BrokerAlpaca class"""

    def __init__(self, key, secret, paper=True, timeout=6.0, data_feed='iex'):
        self.key = key
        self.secret = secret
        self.paper = paper
        self.timeout = timeout
        self.data_feed = data_feed
        # ... rest of your init code

    # ADD THIS METHOD to your existing class
    def snapshot(self, symbol):
        """
        Get snapshot for a single symbol (compatibility wrapper).
        Routes to the appropriate batch method based on symbol type.

        Args:
            symbol: Stock symbol (e.g., 'AAPL') or crypto pair (e.g., 'BTC/USD')

        Returns:
            Snapshot data dict or None if not available
        """
        # Determine if it's crypto based on symbol format
        is_crypto = '/' in symbol or symbol.endswith('USD')

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
            # Log the error if you have a logger
            if hasattr(self, 'logger'):
                self.logger.error(f"Error getting snapshot for {symbol}: {e}")
            return None

    def mid_from_snapshot(self, snapshot):
        """
        Calculate mid price from snapshot data.
        This method should already exist in your class.
        """
        if not snapshot:
            return None

        quote = snapshot.get('latestQuote', {})
        bid = quote.get('bp', 0)
        ask = quote.get('ap', 0)

        if bid > 0 and ask > 0:
            return (bid + ask) / 2

        # Fallback to latest trade
        trade = snapshot.get('latestTrade', {})
        return trade.get('p', 0)

    # Your existing methods...
    # snapshots_batch_stocks, snapshots_batch_crypto, etc.