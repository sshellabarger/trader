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
            conn.execute("""
                INSERT OR REPLACE INTO kv (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, value, datetime.now().isoformat()))
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
        finally:
            conn.close()
    
    def update_health(self, component: str, status: str, details: str = None):
        """Update health status for a component"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO health (component, status, last_check, details)
                VALUES (?, ?, ?, ?)
            """, (component, status, datetime.now().isoformat(), details))
            conn.commit()
        finally:
            conn.close()
    
    def get_health(self) -> Dict[str, Dict]:
        """Get health status for all components"""
        conn = sqlite3.connect(self.db_path)
        try:
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


# Backward compatibility functions (if your existing code uses these)
_default_store = None

def _get_store():
    """Get or create default store instance"""
    global _default_store
    if _default_store is None:
        db_path = os.environ.get('TRADING_BOT_DB', './data/trading_bot.db')
        _default_store = StateStore(db_path)
    return _default_store


def get_kv(key: str) -> Optional[str]:
    """Backward compatible function"""
    return _get_store().get_kv(key)


def set_kv(key: str, value: str):
    """Backward compatible function"""
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
            return json.loads(candidates_json)
        except:
            return []
    return []


def set_candidates(candidates: List[Dict]):
    """Set candidates in KV store (backward compatibility)"""
    import json
    set_kv('candidates', json.dumps(candidates))