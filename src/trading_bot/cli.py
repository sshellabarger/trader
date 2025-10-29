"""
CLI entry point - Fixed to use correct working directory
"""
import os
import sys
import threading
from pathlib import Path
from dotenv import load_dotenv

# CRITICAL: Set working directory to project root
# This ensures templates/ and data/ are found in the right place
project_root = Path(__file__).parent.parent.parent
os.chdir(project_root)
print(f"Working directory set to: {project_root}")

# Load environment variables first
load_dotenv()

# Setup logging BEFORE any other imports
try:
    from trading_bot.logger import configure_logging
    configure_logging()
    print("✓ Logging configured (data/app.log)")
except ImportError:
    # Fallback to basic logging if logger.py not available
    import logging
    from pathlib import Path

    log_level = os.getenv('LOG_LEVEL', 'INFO')
    log_dir = Path('./data')
    log_dir.mkdir(parents=True, exist_ok=True)

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_dir / 'app.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    print(f"✓ Basic logging configured (data/app.log)")

import logging
logger = logging.getLogger(__name__)

# Now import the rest
from trading_bot.engine import Trader
from trading_bot.broker_alpaca import AlpacaBroker
from trading_bot.state import StateStore
from trading_bot.settings import Settings
from trading_bot.webapp import app, set_broker_instance, set_position_monitor_instance
import uvicorn


def run():
    """Main entry point"""
    logger.info("=" * 60)
    logger.info("STARTING ENHANCED TRADING BOT")
    logger.info("=" * 60)
    logger.info(f"Working directory: {os.getcwd()}")

    # Initialize components
    try:
        # Initialize broker
        broker = AlpacaBroker(logger=logger)
        logger.info("✓ Broker initialized")

        # Initialize state store
        db_path = os.getenv('TRADING_BOT_DB', './data/trading_bot.db')
        state = StateStore(db_path)
        logger.info(f"✓ State store initialized ({db_path})")

        # Initialize settings
        settings = Settings()
        logger.info("✓ Settings loaded")

        # Initialize trader
        trader = Trader(broker, state, settings, logger)
        logger.info("✓ Trader engine initialized")

        # Pass broker and position monitor to webapp for API endpoints
        set_broker_instance(broker)
        logger.info("✓ Broker instance linked to webapp")

        if hasattr(trader, 'position_monitor'):
            set_position_monitor_instance(trader.position_monitor)
            logger.info("✓ Position monitor instance linked to webapp")

    except Exception as e:
        logger.error(f"Failed to initialize: {e}", exc_info=True)
        sys.exit(1)

    # Start trader in background thread
    trader_thread = threading.Thread(target=trader.run, daemon=True)
    trader_thread.start()
    logger.info("✓ Trading engine started")

    # Start web server
    host = os.getenv('WEB_HOST', '0.0.0.0')
    port = int(os.getenv('WEB_PORT', 8000))

    logger.info(f"Starting web server on {host}:{port}")
    logger.info(f"Templates directory: {Path('./templates').absolute()}")
    logger.info(f"Data directory: {Path('./data').absolute()}")
    logger.info(f"Open http://localhost:{port} in your browser")

    try:
        # Suppress uvicorn access logs (they're too noisy)
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        trader.running = False
        trader_thread.join(timeout=5)
        logger.info("Goodbye!")


if __name__ == '__main__':
    run()