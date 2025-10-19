"""
CLI entry point - compatible with enhanced engine
"""
import os
import sys
import logging
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
log_level = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create logger
logger = logging.getLogger(__name__)

# Try to use custom logger setup if available
try:
    from . import logger as logger_module
    if hasattr(logger_module, 'setup_logging'):
        logger = logger_module.setup_logging(log_level)
except (ImportError, AttributeError) as e:
    logger.debug(f"Using basic logging (custom logger not available: {e})")

from .engine import Trader
from .broker_alpaca import AlpacaBroker
from .state import StateStore
from .settings import Settings
from .webapp import app, set_broker_instance  # Import the setter function
import uvicorn


def run():
    """Main entry point"""
    logger.info("="*60)
    logger.info("STARTING ENHANCED TRADING BOT")
    logger.info("="*60)

    # Initialize components
    try:
        # Initialize broker (check if it accepts logger argument)
        try:
            broker = AlpacaBroker(logger=logger)
        except TypeError:
            # Older broker version doesn't accept logger
            broker = AlpacaBroker()
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
    logger.info(f"Open http://localhost:{port} in your browser")

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        trader.running = False
        trader_thread.join(timeout=5)
        logger.info("Goodbye!")


if __name__ == '__main__':
    run()