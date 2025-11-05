"""
FastAPI Web Application for Trading Bot
Provides REST API and serves the web UI
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Dict, Any, Optional
from dataclasses import asdict
import json
import logging

from .state import (
    get_kv, set_kv, get_positions as state_get_positions,
    get_health, get_recent_events, get_candidates, get_todays_realized_pnl
)
from .settings import get_settings, update_settings

# Initialize FastAPI
app = FastAPI(title="Trading Bot API", version="2.0")

# Setup templates (if templates directory exists)
try:
    templates = Jinja2Templates(directory="templates")
except:
    templates = None

# Setup static files (if static directory exists)
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except:
    pass

logger = logging.getLogger(__name__)


# Helper function to serialize dataclasses
def serialize_candidate(candidate):
    """Convert CombinedSignal dataclass to dict"""
    try:
        if hasattr(candidate, '__dataclass_fields__'):
            # It's a dataclass - convert it
            result = asdict(candidate)
            # Convert enum to string
            if 'regime' in result and hasattr(result['regime'], 'value'):
                result['regime'] = result['regime'].value
            return result
        elif isinstance(candidate, dict):
            # Already a dict
            return candidate
        else:
            # Try to convert to dict
            return dict(candidate)
    except Exception as e:
        logger.warning(f"Error serializing candidate: {e}")
        # Return basic info as fallback
        return {
            'symbol': getattr(candidate, 'symbol', 'UNKNOWN'),
            'final_score': getattr(candidate, 'final_score', 0),
            'error': 'serialization_failed'
        }


# Pydantic models for request validation
class SettingsUpdate(BaseModel):
    """Settings update model"""
    thresholds: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    strategies: Optional[Dict[str, Any]] = None
    scheduling: Optional[Dict[str, Any]] = None
    news: Optional[Dict[str, Any]] = None
    crypto: Optional[Dict[str, Any]] = None
    forex: Optional[Dict[str, Any]] = None
    etf: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    backtest: Optional[Dict[str, Any]] = None


# UI Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main UI"""
    if templates:
        return templates.TemplateResponse("index.html", {"request": request})
    else:
        # Return inline HTML if no templates directory
        with open("templates/index.html", "r") as f:
            return HTMLResponse(content=f.read())


# API Routes
@app.get("/api/health")
async def api_health():
    """
    Get system health status
    Returns clock status, account info, positions count, etc.
    """
    try:
        health_data = get_health()

        # Parse health data if it's stored as JSON strings
        parsed_health = {}
        for key, value in health_data.items():
            if isinstance(value, dict):
                parsed_health[key] = value
            elif isinstance(value, str):
                try:
                    parsed_health[key] = json.loads(value)
                except:
                    parsed_health[key] = {"status": value}
            else:
                parsed_health[key] = {"status": str(value)}

        return parsed_health
    except Exception as e:
        logger.error(f"Error getting health: {e}")
        return {
            "error": str(e),
            "clock": {"is_open": False},
            "account": {}
        }


@app.get("/api/positions")
async def api_positions():
    """
    Get current positions
    Returns list of open positions with P/L and strategy metadata
    """
    try:
        # Try to get from broker directly if available
        if _broker_instance:
            try:
                positions = _broker_instance.list_positions() if hasattr(_broker_instance, 'list_positions') else _broker_instance.get_positions()
                if positions:
                    logger.debug(f"Got {len(positions)} positions from broker")
                    # Enrich all positions with strategy metadata and ensure required fields
                    for pos in positions:
                        symbol = pos.get('symbol')

                        # Initialize with defaults
                        if 'primary_strategy' not in pos:
                            pos['primary_strategy'] = 'manual'  # Default for positions without metadata
                        if 'entry_price' not in pos:
                            pos['entry_price'] = pos.get('avg_entry_price', 0)
                        if 'stop_loss_pct' not in pos:
                            pos['stop_loss_pct'] = 0
                        if 'take_profit_pct' not in pos:
                            pos['take_profit_pct'] = 0

                        # Enrich with strategy metadata if available
                        if _position_monitor_instance and hasattr(_position_monitor_instance, 'position_metadata'):
                            if symbol and symbol in _position_monitor_instance.position_metadata:
                                metadata = _position_monitor_instance.position_metadata[symbol]
                                pos['primary_strategy'] = metadata.get('primary_strategy', 'manual')
                                # Use metadata entry_price if available, otherwise keep avg_entry_price
                                if metadata.get('entry_price'):
                                    pos['entry_price'] = metadata.get('entry_price')
                                pos['stop_loss_pct'] = metadata.get('stop_loss_pct', 0)
                                pos['take_profit_pct'] = metadata.get('take_profit_pct', 0)
                                logger.debug(f"Enriched {symbol} with strategy: {pos['primary_strategy']}")
                            else:
                                logger.debug(f"No metadata found for {symbol}, using defaults")

                    return {"positions": positions if isinstance(positions, list) else []}
            except Exception as e:
                logger.warning(f"Couldn't get positions from broker: {e}")

        # Fallback to state
        positions = state_get_positions()

        if not positions:
            # Try KV as last resort
            positions_json = get_kv('positions')
            if positions_json:
                try:
                    positions = json.loads(positions_json)
                except:
                    positions = []

        # Ensure it's a list
        if not isinstance(positions, list):
            positions = []

        # Ensure all positions have required fields
        for pos in positions:
            if 'primary_strategy' not in pos:
                pos['primary_strategy'] = 'manual'
            if 'entry_price' not in pos:
                pos['entry_price'] = pos.get('avg_entry_price', 0)
            if 'stop_loss_pct' not in pos:
                pos['stop_loss_pct'] = 0
            if 'take_profit_pct' not in pos:
                pos['take_profit_pct'] = 0

        logger.debug(f"Returning {len(positions)} positions to UI")
        return {"positions": positions}

    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        return {"positions": [], "error": str(e)}


@app.get("/api/pnl")
async def api_pnl():
    """
    Get comprehensive P/L data including both realized and unrealized
    Returns:
    - realized_pnl: P/L from positions closed today
    - unrealized_pnl: P/L from current open positions
    - total_pnl: Combined daily P/L
    """
    try:
        # Get realized P/L from closed positions today
        realized_data = get_todays_realized_pnl()
        realized_pnl = realized_data.get('total', 0)

        # Get unrealized P/L from open positions
        unrealized_pnl = 0
        if _broker_instance:
            try:
                positions = _broker_instance.list_positions() if hasattr(_broker_instance, 'list_positions') else _broker_instance.get_positions()
                if positions:
                    for pos in positions:
                        unrealized_pnl += float(pos.get('unrealized_pl', 0))
            except Exception as e:
                logger.warning(f"Couldn't get positions for P/L: {e}")

        # Calculate total
        total_pnl = realized_pnl + unrealized_pnl

        return {
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": total_pnl,
            "realized_by_symbol": realized_data.get('by_symbol', {}),
            "market_day_start": realized_data.get('market_day_start')
        }

    except Exception as e:
        logger.error(f"Error calculating P/L: {e}", exc_info=True)
        return {
            "realized_pnl": 0,
            "unrealized_pnl": 0,
            "total_pnl": 0,
            "error": str(e)
        }


@app.get("/api/candidates")
async def api_candidates():
    """
    Get ranked trading candidates
    Returns list of potential trades with scores
    """
    try:
        candidates = get_candidates()

        if not candidates:
            # Try KV fallback
            candidates_json = get_kv('candidates')
            if candidates_json:
                try:
                    parsed = json.loads(candidates_json)
                    if isinstance(parsed, dict) and 'candidates' in parsed:
                        candidates = parsed['candidates']
                    elif isinstance(parsed, list):
                        candidates = parsed
                except:
                    candidates = []

        # Serialize candidates (handle dataclasses)
        serialized_candidates = []
        if candidates:
            for candidate in candidates:
                try:
                    serialized = serialize_candidate(candidate)
                    serialized_candidates.append(serialized)
                except Exception as e:
                    logger.error(f"Error serializing candidate: {e}")
                    # Add a basic version
                    serialized_candidates.append({
                        'symbol': str(candidate),
                        'error': 'serialization_error'
                    })

        return {"candidates": serialized_candidates}
    except Exception as e:
        logger.error(f"Error getting candidates: {e}", exc_info=True)
        return {"candidates": [], "error": str(e)}


@app.get("/api/settings")
async def api_get_settings():
    """
    Get current bot settings
    Returns all configuration including thresholds, risk, strategies
    """
    try:
        settings = get_settings()
        return settings
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        return {"error": str(e)}


@app.post("/api/settings")
async def api_update_settings(settings: SettingsUpdate):
    """
    Update bot settings
    Accepts partial updates for any settings category
    """
    try:
        # Convert to dict and remove None values
        updates = {k: v for k, v in settings.dict().items() if v is not None}

        if not updates:
            return {"status": "error", "message": "No settings provided"}

        result = update_settings(updates)
        return result
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        return {"status": "error", "message": str(e)}


@app.get("/api/status")
async def api_status():
    """
    Get system status and recent events
    Returns event log for monitoring
    """
    try:
        events = get_recent_events(200)
        return {"events": events}
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return {"events": [], "error": str(e)}


@app.get("/api/events")
async def api_events(limit: int = 100):
    """
    Get recent events/logs
    Alternative endpoint name for status
    """
    try:
        events = get_recent_events(limit)
        return {"events": events}
    except Exception as e:
        logger.error(f"Error getting events: {e}")
        return {"events": [], "error": str(e)}


# Global broker instance (will be set by CLI)
_broker_instance = None
_position_monitor_instance = None

def set_broker_instance(broker):
    """Set the global broker instance for API endpoints to use"""
    global _broker_instance
    _broker_instance = broker

def set_position_monitor_instance(position_monitor):
    """Set the global position monitor instance for API endpoints to use"""
    global _position_monitor_instance
    _position_monitor_instance = position_monitor


# Trading action endpoints
@app.post("/api/positions/{symbol}/close")
async def close_position(symbol: str):
    """Close a specific position"""
    if not _broker_instance:
        return {
            "status": "error",
            "message": "Broker not available. Cannot close positions."
        }

    try:
        # Get current positions
        positions = _broker_instance.list_positions() if hasattr(_broker_instance, 'list_positions') else _broker_instance.get_positions()

        # Find the position
        position = next((p for p in positions if p.get('symbol') == symbol), None)
        if not position:
            return {"status": "error", "message": f"No position found for {symbol}"}

        qty = abs(float(position.get('qty', 0)))

        # Place sell order
        order = _broker_instance.place_order(
            symbol=symbol,
            qty=qty,
            side='sell',
            order_type='market'
        )

        if order:
            logger.info(f"Closed position {symbol}: {qty} shares")
            return {
                "status": "success",
                "message": f"Closed {qty} shares of {symbol}",
                "order_id": order.get('id')
            }
        else:
            return {"status": "error", "message": "Failed to place close order"}

    except Exception as e:
        logger.error(f"Error closing position {symbol}: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/positions/close-all")
async def close_all_positions():
    """Close all open positions"""
    if not _broker_instance:
        return {
            "status": "error",
            "message": "Broker not available. Cannot close positions."
        }

    try:
        positions = _broker_instance.list_positions() if hasattr(_broker_instance, 'list_positions') else _broker_instance.get_positions()

        if not positions:
            return {"status": "success", "message": "No positions to close"}

        closed = []
        failed = []

        for position in positions:
            symbol = position.get('symbol')
            qty = abs(float(position.get('qty', 0)))

            try:
                order = _broker_instance.place_order(
                    symbol=symbol,
                    qty=qty,
                    side='sell',
                    order_type='market'
                )

                if order:
                    closed.append(symbol)
                    logger.info(f"Closed position {symbol}: {qty} shares")
                else:
                    failed.append(symbol)

            except Exception as e:
                logger.error(f"Failed to close {symbol}: {e}")
                failed.append(symbol)

        return {
            "status": "success",
            "message": f"Closed {len(closed)} positions. Failed: {len(failed)}",
            "closed": closed,
            "failed": failed
        }

    except Exception as e:
        logger.error(f"Error closing all positions: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/trading/pause")
async def pause_trading():
    """
    Pause the trading engine

    TODO: Implement this by setting a flag the engine checks
    """
    return {
        "status": "error",
        "message": "Trading pause not implemented yet"
    }


@app.post("/api/trading/resume")
async def resume_trading():
    """
    Resume the trading engine

    TODO: Implement this
    """
    return {
        "status": "error",
        "message": "Trading resume not implemented yet"
    }


# Health check endpoint
@app.get("/api/ping")
async def ping():
    """Simple health check"""
    return {"status": "ok", "message": "Trading bot API is running"}


# Exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)