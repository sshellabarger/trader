"""
Enhanced State Store with Class Wrapper
Maintains backward compatibility with function-based state
"""
import sqlite3
import os
from datetime import datetime
from typing import Optional, Dict, List, Any
import json


class StateStore:
    """
    Object-oriented wrapper for state management
    Provides backward-compatible interface
    """
    
    def __init__(self, db_path: str = './data/trading_bot.db'):
        self.db_path = db_path
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """Ensure database and tables exist"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        try:
            # Create base tables if they don't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    details TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS health (
                    component TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    last_check TEXT NOT NULL,
                    details TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    price REAL NOT NULL,
                    order_id TEXT,
                    strategy TEXT,
                    details TEXT
                )
            """)
            
            conn.commit()
        finally:
            conn.close()
    
    def get_kv(self, key: str) -> Optional[str]:
        """Get value from key-value store"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    
    def set_kv(self, key: str, value: str):
        """Set value in key-value store"""
        conn = sqlite3.connect(self.db_path)
        try:
            # Check what columns exist
            cursor = conn.execute("PRAGMA table_info(kv)")
            columns = {row[1] for row in cursor.fetchall()}

            if 'updated_at' in columns:
                # New schema with timestamp
                conn.execute("""
                    INSERT OR REPLACE INTO kv (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, value, datetime.now().isoformat()))
            else:
                # Old schema without timestamp
                conn.execute("""
                    INSERT OR REPLACE INTO kv (key, value)
                    VALUES (?, ?)
                """, (key, value))

            conn.commit()
        finally:
            conn.close()

    def delete_kv(self, key: str):
        """Delete key from key-value store"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()

    def add_event(self, event_type: str, message: str, details: str = None):
        """Add event to event log"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO events (timestamp, event_type, message, details)
                VALUES (?, ?, ?, ?)
            """, (datetime.now().isoformat(), event_type, message, details))
            conn.commit()
        finally:
            conn.close()

    def get_recent_events(self, limit: int = 50) -> List[Dict]:
        """Get recent events"""
        conn = sqlite3.connect(self.db_path)
        try:
            # Check what columns exist in events table
            cursor = conn.execute("PRAGMA table_info(events)")
            columns = {row[1] for row in cursor.fetchall()}

            if not columns:
                # Table doesn't exist or is empty
                return []

            # Adapt query to available columns
            if 'timestamp' in columns:
                cursor = conn.execute("""
                    SELECT timestamp, event_type, message, details
                    FROM events
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))

                events = []
                for row in cursor.fetchall():
                    events.append({
                        'timestamp': row[0],
                        'event_type': row[1],
                        'message': row[2],
                        'details': row[3]
                    })
                return events
            elif 'ts' in columns:
                # Alternative timestamp column name
                cursor = conn.execute("""
                    SELECT ts, event_type, message, details
                    FROM events
                    ORDER BY ts DESC
                    LIMIT ?
                """, (limit,))

                events = []
                for row in cursor.fetchall():
                    events.append({
                        'timestamp': row[0],
                        'event_type': row[1],
                        'message': row[2],
                        'details': row[3]
                    })
                return events
            else:
                # No timestamp column - return empty
                return []
        except Exception as e:
            # If query fails, return empty list
            return []
        finally:
            conn.close()

    def update_health(self, component: str, status: str, details: str = None):
        """Update health status for a component"""
        conn = sqlite3.connect(self.db_path)
        try:
            # Check what columns exist in health table
            cursor = conn.execute("PRAGMA table_info(health)")
            columns = {row[1] for row in cursor.fetchall()}

            if 'component' in columns:
                # New schema
                conn.execute("""
                    INSERT OR REPLACE INTO health (component, status, last_check, details)
                    VALUES (?, ?, ?, ?)
                """, (component, status, datetime.now().isoformat(), details))
            else:
                # Old schema - store as KV instead
                import json
                health_data = self.get_kv('health') or '{}'
                try:
                    health = json.loads(health_data)
                except:
                    health = {}

                health[component] = {
                    'status': status,
                    'last_check': datetime.now().isoformat(),
                    'details': details
                }
                self.set_kv('health', json.dumps(health))

            conn.commit()
        finally:
            conn.close()

    def get_health(self) -> Dict[str, Dict]:
        """Get health status for all components"""
        conn = sqlite3.connect(self.db_path)
        try:
            # Check if health table exists and has correct schema
            cursor = conn.execute("PRAGMA table_info(health)")
            columns = {row[1] for row in cursor.fetchall()}

            if 'component' in columns:
                # New schema
                cursor = conn.execute("""
                    SELECT component, status, last_check, details
                    FROM health
                """)

                health = {}
                for row in cursor.fetchall():
                    health[row[0]] = {
                        'status': row[1],
                        'last_check': row[2],
                        'details': row[3]
                    }
                return health
            else:
                # Old schema - get from KV
                import json
                health_data = self.get_kv('health')
                if health_data:
                    try:
                        return json.loads(health_data)
                    except:
                        return {}
                return {}
        finally:
            conn.close()

    def record_trade(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        order_id: str = '',
        strategy: str = '',
        details: str = None
    ):
        """Record a trade"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO trades (timestamp, symbol, side, qty, price, order_id, strategy, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (datetime.now().isoformat(), symbol, side, qty, price, order_id, strategy, details))
            conn.commit()
        finally:
            conn.close()

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        """Get recent trades"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("""
                SELECT timestamp, symbol, side, qty, price, order_id, strategy, details
                FROM trades
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))

            trades = []
            for row in cursor.fetchall():
                trades.append({
                    'timestamp': row[0],
                    'symbol': row[1],
                    'side': row[2],
                    'qty': row[3],
                    'price': row[4],
                    'order_id': row[5],
                    'strategy': row[6],
                    'details': row[7]
                })
            return trades
        finally:
            conn.close()

    def get_trades_by_symbol(self, symbol: str) -> List[Dict]:
        """Get all trades for a specific symbol"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute("""
                SELECT timestamp, side, qty, price, order_id, strategy, details
                FROM trades
                WHERE symbol = ?
                ORDER BY timestamp DESC
            """, (symbol,))

            trades = []
            for row in cursor.fetchall():
                trades.append({
                    'timestamp': row[0],
                    'side': row[1],
                    'qty': row[2],
                    'price': row[3],
                    'order_id': row[4],
                    'strategy': row[5],
                    'details': row[6]
                })
            return trades
        finally:
            conn.close()

    def get_todays_realized_pnl(self) -> Dict[str, Any]:
        """
        Calculate realized P/L from positions closed today.
        Returns a dict with total P/L and breakdown by symbol.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            # Get today's date at market open (assume 9:30 AM ET start)
            from datetime import datetime, timedelta
            import pytz

            # Get current time in ET timezone
            et_tz = pytz.timezone('America/New_York')
            now_et = datetime.now(et_tz)

            # Market day starts at 9:30 AM ET
            # If before 9:30 AM, use previous trading day
            if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30):
                market_day_start = now_et.replace(hour=9, minute=30, second=0, microsecond=0) - timedelta(days=1)
            else:
                market_day_start = now_et.replace(hour=9, minute=30, second=0, microsecond=0)

            # Convert to ISO format for database query
            start_time = market_day_start.isoformat()

            # Get all trades since market open today
            cursor = conn.execute("""
                SELECT symbol, side, qty, price, timestamp
                FROM trades
                WHERE timestamp >= ?
                ORDER BY symbol, timestamp
            """, (start_time,))

            trades = cursor.fetchall()

            # Calculate P/L by matching buys and sells
            positions = {}  # symbol -> {qty, cost_basis}
            realized_pnl = {}  # symbol -> realized_pnl

            for row in trades:
                symbol, side, qty, price, timestamp = row

                if symbol not in positions:
                    positions[symbol] = {'qty': 0, 'cost_basis': 0}

                if side.lower() == 'buy':
                    # Add to position
                    positions[symbol]['cost_basis'] += qty * price
                    positions[symbol]['qty'] += qty
                elif side.lower() == 'sell':
                    # Close position (full or partial)
                    if positions[symbol]['qty'] > 0:
                        # Calculate average entry price
                        avg_entry = positions[symbol]['cost_basis'] / positions[symbol]['qty']

                        # Calculate P/L for this sale
                        pnl = (price - avg_entry) * qty

                        if symbol not in realized_pnl:
                            realized_pnl[symbol] = 0
                        realized_pnl[symbol] += pnl

                        # Update position
                        positions[symbol]['qty'] -= qty
                        if positions[symbol]['qty'] > 0:
                            positions[symbol]['cost_basis'] = positions[symbol]['cost_basis'] * (positions[symbol]['qty'] / (positions[symbol]['qty'] + qty))
                        else:
                            positions[symbol]['cost_basis'] = 0

            # Calculate total
            total_realized_pnl = sum(realized_pnl.values())

            return {
                'total': total_realized_pnl,
                'by_symbol': realized_pnl,
                'market_day_start': start_time
            }

        except Exception as e:
            # If any error (e.g., missing pytz), return zero
            return {
                'total': 0,
                'by_symbol': {},
                'error': str(e)
            }
        finally:
            conn.close()


# Backward compatibility functions (if your existing code uses these)
_default_store = None

def _get_store():
    """Get or create default store instance"""
    global _default_store
    if _default_store is None:
        db_path = os.environ.get('TRADING_BOT_DB', './data/trading_bot.db')
        _default_store = StateStore(db_path)
    return _default_store


def get_kv(key: str, default=None) -> Optional[str]:
    """
    Backward compatible function with optional default parameter

    Args:
        key: Key to retrieve
        default: Default value if key not found (for webapp compatibility)
    """
    result = _get_store().get_kv(key)
    if result is None and default is not None:
        # If default is provided and key not found, return default
        # But we need to handle the case where webapp expects JSON parsing
        if isinstance(default, dict):
            import json
            return json.dumps(default)
        return default
    return result


def set_kv(key: str, value):
    """
    Backward compatible function
    Accepts both string and dict values (converts dict to JSON)
    """
    if isinstance(value, (dict, list)):
        import json
        value = json.dumps(value)
    elif not isinstance(value, str):
        value = str(value)

    _get_store().set_kv(key, value)


def delete_kv(key: str):
    """Backward compatible function"""
    _get_store().delete_kv(key)


def add_event(event_type: str, message: str, details: str = None):
    """Backward compatible function"""
    _get_store().add_event(event_type, message, details)


def get_recent_events(limit: int = 50) -> List[Dict]:
    """Backward compatible function"""
    return _get_store().get_recent_events(limit)


def update_health(component: str, status: str, details: str = None):
    """Backward compatible function"""
    _get_store().update_health(component, status, details)


def get_health() -> Dict[str, Dict]:
    """Backward compatible function"""
    return _get_store().get_health()


def record_trade(
    symbol: str,
    side: str,
    qty: int,
    price: float,
    order_id: str = '',
    strategy: str = '',
    details: str = None
):
    """Backward compatible function"""
    _get_store().record_trade(symbol, side, qty, price, order_id, strategy, details)


def get_recent_trades(limit: int = 50) -> List[Dict]:
    """Backward compatible function"""
    return _get_store().get_recent_trades(limit)


def get_trades_by_symbol(symbol: str) -> List[Dict]:
    """Backward compatible function"""
    return _get_store().get_trades_by_symbol(symbol)


def get_todays_realized_pnl() -> Dict[str, Any]:
    """Backward compatible function"""
    return _get_store().get_todays_realized_pnl()


def list_events(limit: int = 50) -> List[Dict]:
    """Alias for get_recent_events (backward compatibility)"""
    return get_recent_events(limit)


def get_positions() -> List[Dict]:
    """Get positions from KV store (backward compatibility)"""
    positions_json = get_kv('positions')
    if positions_json:
        try:
            import json
            return json.loads(positions_json)
        except:
            return []
    return []


def set_positions(positions: List[Dict]):
    """Set positions in KV store (backward compatibility)"""
    import json
    set_kv('positions', json.dumps(positions))


def get_candidates() -> List[Dict]:
    """Get candidates from KV store (backward compatibility)"""
    candidates_json = get_kv('candidates')
    if candidates_json:
        try:
            import json
            result = json.loads(candidates_json)
            # Ensure it returns a list
            if isinstance(result, dict):
                return result.get('candidates', [])
            return result if isinstance(result, list) else []
        except:
            return []
    return []


def set_candidates(candidates: List[Dict]):
    """Set candidates in KV store (backward compatibility)"""
    import json
    set_kv('candidates', json.dumps(candidates))