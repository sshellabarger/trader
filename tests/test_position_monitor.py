"""
Test Suite for Position Monitor
Tests stop loss functionality with various scenarios
"""
import pytest
import asyncio
from datetime import datetime, date
from unittest.mock import Mock, AsyncMock, patch
from trading_bot.position_monitor import PositionMonitor, StopLossEvent


class MockBroker:
    """Mock broker for testing"""
    
    def __init__(self):
        self.positions = []
        self.account = {'equity': 10000.0, 'cash': 5000.0, 'buying_power': 5000.0}
        self.orders_placed = []
    
    async def get_positions(self):
        return self.positions
    
    async def get_account(self):
        return self.account
    
    async def place_order(self, symbol, side, qty, order_type, time_in_force):
        order = {
            'symbol': symbol,
            'side': side,
            'qty': qty,
            'order_type': order_type,
            'time_in_force': time_in_force,
            'timestamp': datetime.now().isoformat()
        }
        self.orders_placed.append(order)
        return order


@pytest.fixture
def mock_broker():
    return MockBroker()


@pytest.fixture
def settings():
    return {
        'risk': {
            'position_monitor_interval_sec': 1,  # Fast for testing
            'position_monitor_enabled': True
        },
        'thresholds': {
            'trade_stop_loss_bps': 50.0,  # 0.50%
            'daily_stop_loss_pct': 2.0     # 2.00%
        }
    }


@pytest.fixture
def monitor(mock_broker, settings):
    return PositionMonitor(mock_broker, settings)


# ============================================================================
# Test Basic Functionality
# ============================================================================

def test_monitor_initialization(monitor):
    """Test monitor initializes correctly"""
    assert monitor._running is False
    assert monitor.trading_halted is False
    assert monitor.halt_reason is None
    assert len(monitor.stop_loss_events) == 0


def test_get_status(monitor):
    """Test status reporting"""
    status = monitor.get_status()
    assert 'running' in status
    assert 'trading_halted' in status
    assert 'total_stop_loss_events' in status
    assert status['running'] is False
    assert status['trading_halted'] is False


# ============================================================================
# Test Stop Loss Calculation
# ============================================================================

@pytest.mark.asyncio
async def test_position_at_stop_loss(monitor, mock_broker):
    """Test position that should trigger stop loss"""
    # Create position with 0.50% loss (exactly at threshold)
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': 10,
        'avg_entry_price': 100.0,
        'current_price': 99.50,  # 0.50% loss = 50 bps
        'unrealized_pl': -5.0
    }]
    
    # Check position
    await monitor._check_positions()
    
    # Should have triggered stop loss
    assert len(mock_broker.orders_placed) == 1
    assert mock_broker.orders_placed[0]['symbol'] == 'AAPL'
    assert mock_broker.orders_placed[0]['side'] == 'sell'
    assert mock_broker.orders_placed[0]['qty'] == 10


@pytest.mark.asyncio
async def test_position_below_stop_loss(monitor, mock_broker):
    """Test position that is profitable (should not trigger)"""
    # Create position with profit
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': 10,
        'avg_entry_price': 100.0,
        'current_price': 102.0,  # +2% profit
        'unrealized_pl': 20.0
    }]
    
    # Check position
    await monitor._check_positions()
    
    # Should NOT trigger stop loss
    assert len(mock_broker.orders_placed) == 0


@pytest.mark.asyncio
async def test_position_small_loss(monitor, mock_broker):
    """Test position with small loss (below threshold)"""
    # Create position with 0.25% loss (below 0.50% threshold)
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': 10,
        'avg_entry_price': 100.0,
        'current_price': 99.75,  # 0.25% loss = 25 bps
        'unrealized_pl': -2.5
    }]
    
    # Check position
    await monitor._check_positions()
    
    # Should NOT trigger stop loss (below threshold)
    assert len(mock_broker.orders_placed) == 0


@pytest.mark.asyncio
async def test_position_large_loss(monitor, mock_broker):
    """Test position with large loss (well above threshold)"""
    # Create position with 1.5% loss (above 0.50% threshold)
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': 10,
        'avg_entry_price': 100.0,
        'current_price': 98.50,  # 1.5% loss = 150 bps
        'unrealized_pl': -15.0
    }]
    
    # Check position
    await monitor._check_positions()
    
    # Should trigger stop loss
    assert len(mock_broker.orders_placed) == 1
    assert len(monitor.stop_loss_events) == 1
    assert monitor.stop_loss_events[0].loss_bps >= 50.0


# ============================================================================
# Test Multiple Positions
# ============================================================================

@pytest.mark.asyncio
async def test_multiple_positions_mixed(monitor, mock_broker):
    """Test multiple positions, some triggering stop loss"""
    mock_broker.positions = [
        {
            'symbol': 'AAPL',
            'qty': 10,
            'avg_entry_price': 100.0,
            'current_price': 99.40,  # 0.60% loss - triggers
            'unrealized_pl': -6.0
        },
        {
            'symbol': 'MSFT',
            'qty': 5,
            'avg_entry_price': 200.0,
            'current_price': 201.0,  # profit - no trigger
            'unrealized_pl': 5.0
        },
        {
            'symbol': 'GOOGL',
            'qty': 3,
            'avg_entry_price': 150.0,
            'current_price': 149.30,  # 0.47% loss - below threshold
            'unrealized_pl': -2.1
        }
    ]
    
    # Check positions
    await monitor._check_positions()
    
    # Only AAPL should trigger
    assert len(mock_broker.orders_placed) == 1
    assert mock_broker.orders_placed[0]['symbol'] == 'AAPL'


# ============================================================================
# Test Daily Stop Loss
# ============================================================================

@pytest.mark.asyncio
async def test_daily_stop_loss_not_triggered(monitor, mock_broker):
    """Test daily stop loss not triggered"""
    # Set starting equity
    monitor.starting_daily_equity = 10000.0
    monitor.last_equity_reset_date = date.today()
    
    # Current equity down 1% (below 2% threshold)
    mock_broker.account['equity'] = 9900.0
    
    # Check
    result = await monitor._check_daily_stop_loss(mock_broker.account)
    
    assert result is False
    assert monitor.trading_halted is False


@pytest.mark.asyncio
async def test_daily_stop_loss_triggered(monitor, mock_broker):
    """Test daily stop loss triggered"""
    # Set starting equity
    monitor.starting_daily_equity = 10000.0
    monitor.last_equity_reset_date = date.today()
    
    # Current equity down 2.5% (exceeds 2% threshold)
    mock_broker.account['equity'] = 9750.0
    
    # Add some positions to close
    mock_broker.positions = [
        {
            'symbol': 'AAPL',
            'qty': 10,
            'avg_entry_price': 100.0,
            'current_price': 95.0,
            'unrealized_pl': -50.0
        }
    ]
    
    # Check
    result = await monitor._check_daily_stop_loss(mock_broker.account)
    
    assert result is True


@pytest.mark.asyncio
async def test_daily_stop_loss_closes_all_positions(monitor, mock_broker):
    """Test daily stop loss closes all positions"""
    monitor.starting_daily_equity = 10000.0
    monitor.last_equity_reset_date = date.today()
    
    # Multiple positions
    mock_broker.positions = [
        {'symbol': 'AAPL', 'qty': 10, 'avg_entry_price': 100.0, 'current_price': 95.0, 'unrealized_pl': -50.0},
        {'symbol': 'MSFT', 'qty': 5, 'avg_entry_price': 200.0, 'current_price': 195.0, 'unrealized_pl': -25.0},
        {'symbol': 'GOOGL', 'qty': 3, 'avg_entry_price': 150.0, 'current_price': 145.0, 'unrealized_pl': -15.0}
    ]
    
    # Trigger daily stop loss
    await monitor._execute_daily_stop_loss(mock_broker.positions)
    
    # All positions should be closed
    assert len(mock_broker.orders_placed) == 3
    assert monitor.trading_halted is True
    assert monitor.halt_reason == 'daily_stop_loss'


# ============================================================================
# Test Event Recording
# ============================================================================

@pytest.mark.asyncio
async def test_stop_loss_event_recorded(monitor, mock_broker):
    """Test that stop loss events are properly recorded"""
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': 10,
        'avg_entry_price': 100.0,
        'current_price': 99.40,
        'unrealized_pl': -6.0
    }]
    
    await monitor._check_positions()
    
    # Check event recorded
    assert len(monitor.stop_loss_events) == 1
    event = monitor.stop_loss_events[0]
    assert event.symbol == 'AAPL'
    assert event.entry_price == 100.0
    assert event.exit_price == 99.40
    assert event.reason == 'trade'
    assert event.qty == 10


def test_get_stop_loss_events(monitor):
    """Test retrieving stop loss events"""
    # Add some events
    for i in range(5):
        monitor.stop_loss_events.append(
            StopLossEvent(
                timestamp=datetime.now().isoformat(),
                symbol=f'TEST{i}',
                entry_price=100.0,
                exit_price=99.0,
                loss_bps=100.0,
                threshold_bps=50.0,
                qty=10,
                loss_amount=-10.0,
                reason='trade'
            )
        )
    
    # Get events
    events = monitor.get_stop_loss_events(limit=3)
    assert len(events) == 3


# ============================================================================
# Test Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_zero_threshold_disables_stop_loss(monitor, mock_broker):
    """Test that setting threshold to 0 disables stop loss"""
    monitor.settings['thresholds']['trade_stop_loss_bps'] = 0
    
    # Position with large loss
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': 10,
        'avg_entry_price': 100.0,
        'current_price': 90.0,  # 10% loss!
        'unrealized_pl': -100.0
    }]
    
    await monitor._check_positions()
    
    # Should NOT trigger (disabled)
    assert len(mock_broker.orders_placed) == 0


@pytest.mark.asyncio
async def test_invalid_position_data(monitor, mock_broker):
    """Test handling of invalid position data"""
    mock_broker.positions = [
        {'symbol': 'AAPL', 'qty': 0, 'avg_entry_price': 100.0, 'current_price': 99.0},  # Zero qty
        {'symbol': 'MSFT', 'qty': 10, 'avg_entry_price': 0, 'current_price': 99.0},     # Zero entry
        {'symbol': 'GOOGL', 'qty': 10, 'avg_entry_price': 100.0, 'current_price': 0},   # Zero current
    ]
    
    # Should not crash
    await monitor._check_positions()
    
    # Should not place any orders
    assert len(mock_broker.orders_placed) == 0


@pytest.mark.asyncio
async def test_short_position_skipped(monitor, mock_broker):
    """Test that short positions are skipped (not implemented yet)"""
    # Short position (negative qty)
    mock_broker.positions = [{
        'symbol': 'AAPL',
        'qty': -10,  # Short position
        'avg_entry_price': 100.0,
        'current_price': 105.0,  # Loss on short
        'unrealized_pl': -50.0
    }]
    
    await monitor._check_positions()
    
    # Should skip (not implemented)
    assert len(mock_broker.orders_placed) == 0


# ============================================================================
# Test Daily Equity Reset
# ============================================================================

@pytest.mark.asyncio
async def test_daily_equity_reset():
    """Test that daily equity resets properly"""
    broker = MockBroker()
    settings = {
        'risk': {'position_monitor_interval_sec': 1},
        'thresholds': {'daily_stop_loss_pct': 2.0}
    }
    monitor = PositionMonitor(broker, settings)
    
    # Initial reset
    await monitor._reset_daily_equity_if_needed()
    assert monitor.starting_daily_equity == 10000.0
    assert monitor.last_equity_reset_date == date.today()
    
    # Same day - should not reset
    old_equity = monitor.starting_daily_equity
    await monitor._reset_daily_equity_if_needed()
    assert monitor.starting_daily_equity == old_equity


# ============================================================================
# Integration Test
# ============================================================================

@pytest.mark.asyncio
async def test_full_monitoring_cycle(monitor, mock_broker):
    """Test a full monitoring cycle"""
    # Setup positions
    mock_broker.positions = [
        {'symbol': 'AAPL', 'qty': 10, 'avg_entry_price': 100.0, 'current_price': 99.40, 'unrealized_pl': -6.0},
        {'symbol': 'MSFT', 'qty': 5, 'avg_entry_price': 200.0, 'current_price': 199.80, 'unrealized_pl': -1.0}
    ]
    
    # Run check
    await monitor._check_positions()
    
    # AAPL should trigger (0.60% loss), MSFT should not (0.10% loss)
    assert len(mock_broker.orders_placed) == 1
    assert mock_broker.orders_placed[0]['symbol'] == 'AAPL'
    
    # Check events
    assert len(monitor.stop_loss_events) == 1
    assert monitor.stop_loss_events[0].symbol == 'AAPL'
    
    # Monitor should still be running (not halted)
    assert monitor.trading_halted is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
