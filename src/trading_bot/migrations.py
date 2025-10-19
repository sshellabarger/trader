"""
Database Migration System
Handles schema changes safely with version tracking
"""
import sqlite3
import logging
from typing import List, Callable
from datetime import datetime


class Migration:
    """Single database migration"""
    
    def __init__(self, version: int, description: str, up: Callable, down: Callable = None):
        self.version = version
        self.description = description
        self.up = up  # Function to apply migration
        self.down = down  # Function to rollback migration (optional)
    
    def __repr__(self):
        return f"Migration(v{self.version}: {self.description})"


class MigrationManager:
    """Manages database schema migrations"""
    
    def __init__(self, db_path: str, logger: logging.Logger = None):
        self.db_path = db_path
        self.logger = logger or logging.getLogger(__name__)
        self.migrations: List[Migration] = []
        
        # Initialize migration tracking table
        self._init_migration_table()
    
    def _init_migration_table(self):
        """Create migration tracking table if it doesn't exist"""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.commit()
            self.logger.info("Migration table initialized")
        finally:
            conn.close()
    
    def register_migration(self, migration: Migration):
        """Register a migration"""
        self.migrations.append(migration)
        self.migrations.sort(key=lambda m: m.version)
    
    def get_current_version(self) -> int:
        """Get current database schema version"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT MAX(version) FROM schema_migrations WHERE success = 1"
            )
            result = cursor.fetchone()
            return result[0] if result[0] is not None else 0
        finally:
            conn.close()
    
    def get_pending_migrations(self) -> List[Migration]:
        """Get migrations that haven't been applied"""
        current_version = self.get_current_version()
        return [m for m in self.migrations if m.version > current_version]
    
    def migrate(self, target_version: int = None):
        """
        Apply pending migrations up to target version
        If target_version is None, apply all pending migrations
        """
        current_version = self.get_current_version()
        self.logger.info(f"Current database version: {current_version}")
        
        pending = self.get_pending_migrations()
        
        if target_version:
            pending = [m for m in pending if m.version <= target_version]
        
        if not pending:
            self.logger.info("No pending migrations")
            return
        
        self.logger.info(f"Applying {len(pending)} migrations...")
        
        for migration in pending:
            self._apply_migration(migration)
    
    def _apply_migration(self, migration: Migration):
        """Apply a single migration"""
        self.logger.info(f"Applying {migration}")
        
        conn = sqlite3.connect(self.db_path)
        try:
            # Execute migration
            migration.up(conn)
            
            # Record success
            conn.execute(
                """
                INSERT INTO schema_migrations (version, description, applied_at, success)
                VALUES (?, ?, ?, 1)
                """,
                (migration.version, migration.description, datetime.now().isoformat())
            )
            conn.commit()
            
            self.logger.info(f"✓ Migration v{migration.version} applied successfully")
            
        except Exception as e:
            conn.rollback()
            
            # Record failure
            try:
                conn.execute(
                    """
                    INSERT INTO schema_migrations (version, description, applied_at, success)
                    VALUES (?, ?, ?, 0)
                    """,
                    (migration.version, f"FAILED: {migration.description}", 
                     datetime.now().isoformat())
                )
                conn.commit()
            except:
                pass
            
            self.logger.error(f"✗ Migration v{migration.version} failed: {e}")
            raise
        finally:
            conn.close()
    
    def rollback(self, target_version: int):
        """Rollback to a specific version"""
        current_version = self.get_current_version()
        
        if target_version >= current_version:
            self.logger.warning("Target version is not lower than current version")
            return
        
        # Get migrations to rollback (in reverse order)
        to_rollback = [
            m for m in reversed(self.migrations)
            if target_version < m.version <= current_version
        ]
        
        self.logger.info(f"Rolling back {len(to_rollback)} migrations...")
        
        for migration in to_rollback:
            if not migration.down:
                self.logger.error(
                    f"Cannot rollback v{migration.version} - no down migration defined"
                )
                raise RuntimeError(f"Migration v{migration.version} is not reversible")
            
            self._rollback_migration(migration)
    
    def _rollback_migration(self, migration: Migration):
        """Rollback a single migration"""
        self.logger.info(f"Rolling back {migration}")
        
        conn = sqlite3.connect(self.db_path)
        try:
            # Execute rollback
            migration.down(conn)
            
            # Remove from migrations table
            conn.execute(
                "DELETE FROM schema_migrations WHERE version = ?",
                (migration.version,)
            )
            conn.commit()
            
            self.logger.info(f"✓ Migration v{migration.version} rolled back")
            
        except Exception as e:
            conn.rollback()
            self.logger.error(f"✗ Rollback v{migration.version} failed: {e}")
            raise
        finally:
            conn.close()


# Define migrations for the trading bot

def migration_001_create_base_tables(conn):
    """Create base tables if they don't exist"""
    
    # Helper function to check if table exists
    def table_exists(table_name):
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,)
        )
        return cursor.fetchone() is not None
    
    # Helper function to get table columns
    def get_columns(table_name):
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}
    
    # Create kv table (key-value store)
    if not table_exists('kv'):
        conn.execute("""
            CREATE TABLE kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
    
    # Create events table
    if not table_exists('events'):
        conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            )
        """)
    
    # Create health table
    if not table_exists('health'):
        conn.execute("""
            CREATE TABLE health (
                component TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                last_check TEXT NOT NULL,
                details TEXT
            )
        """)
    
    # Create trades table
    if not table_exists('trades'):
        conn.execute("""
            CREATE TABLE trades (
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
    
    # Create indexes only if tables have the right columns
    if table_exists('events'):
        columns = get_columns('events')
        if 'timestamp' in columns:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp 
                ON events(timestamp)
            """)
    
    if table_exists('trades'):
        columns = get_columns('trades')
        if 'timestamp' in columns:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp 
                ON trades(timestamp)
            """)
        if 'symbol' in columns:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_symbol 
                ON trades(symbol)
            """)


def migration_002_add_risk_metrics(conn):
    """Add risk metrics tracking table"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            account_value REAL NOT NULL,
            total_exposure_pct REAL NOT NULL,
            positions_at_risk INTEGER NOT NULL,
            daily_pl_pct REAL NOT NULL,
            risk_level TEXT NOT NULL,
            details TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_risk_metrics_timestamp 
        ON risk_metrics(timestamp)
    """)


def migration_003_add_trade_details(conn):
    """Add more fields to trades table"""
    # Check if table exists first
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
    )
    if not cursor.fetchone():
        # Table doesn't exist, skip this migration
        return
    
    # Check if columns exist first
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'stop_loss' not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL")
    if 'take_profit' not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN take_profit REAL")
    if 'exit_reason' not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
    if 'regime' not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN regime TEXT")
    if 'signal_score' not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN signal_score REAL")


def migration_004_add_decision_log(conn):
    """Add comprehensive decision logging table"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            decision TEXT NOT NULL,
            score REAL,
            details TEXT,
            executed INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_decision_log_timestamp 
        ON decision_log(timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_decision_log_symbol 
        ON decision_log(symbol)
    """)


def migration_005_add_performance_metrics(conn):
    """Add daily performance tracking"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_performance (
            date TEXT PRIMARY KEY,
            starting_equity REAL NOT NULL,
            ending_equity REAL NOT NULL,
            total_pnl REAL NOT NULL,
            total_pnl_pct REAL NOT NULL,
            trades_count INTEGER NOT NULL,
            winning_trades INTEGER NOT NULL,
            losing_trades INTEGER NOT NULL,
            largest_win REAL NOT NULL,
            largest_loss REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            details TEXT
        )
    """)


def migration_006_add_backtest_results(conn):
    """Add backtest results storage"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            ending_capital REAL NOT NULL,
            total_return_pct REAL NOT NULL,
            total_trades INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            sharpe_ratio REAL NOT NULL,
            max_drawdown_pct REAL NOT NULL,
            settings TEXT,
            metrics TEXT
        )
    """)


def create_migration_manager(db_path: str, logger: logging.Logger = None) -> MigrationManager:
    """Create and configure migration manager with all migrations"""
    manager = MigrationManager(db_path, logger)
    
    # Register all migrations in order
    manager.register_migration(Migration(
        version=1,
        description="Create base tables (kv, events, health, trades)",
        up=migration_001_create_base_tables
    ))
    
    manager.register_migration(Migration(
        version=2,
        description="Add risk metrics tracking",
        up=migration_002_add_risk_metrics
    ))
    
    manager.register_migration(Migration(
        version=3,
        description="Add trade details fields",
        up=migration_003_add_trade_details
    ))
    
    manager.register_migration(Migration(
        version=4,
        description="Add decision log table",
        up=migration_004_add_decision_log
    ))
    
    manager.register_migration(Migration(
        version=5,
        description="Add daily performance tracking",
        up=migration_005_add_performance_metrics
    ))
    
    manager.register_migration(Migration(
        version=6,
        description="Add backtest results storage",
        up=migration_006_add_backtest_results
    ))
    
    return manager


# Example usage:
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    manager = create_migration_manager('./data/trading_bot.db', logger)
    
    # Apply all pending migrations
    manager.migrate()
    
    # Or migrate to specific version
    # manager.migrate(target_version=3)
    
    # Check current version
    print(f"Current database version: {manager.get_current_version()}")
