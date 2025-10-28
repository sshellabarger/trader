"""
Comprehensive Strategy Testing Framework

This module provides tools for testing individual trading strategies in both
live and backtest modes, with detailed metrics collection for AI-driven analysis
and optimization.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
import statistics

from .strategies import (
    score_momentum, score_mean_reversion, score_news, score_volume,
    score_earnings, score_longterm_trend, score_longterm_momentum, score_crypto
)
from .strategy_manager import MarketRegime
from .broker_alpaca import AlpacaBroker
from .news import NewsArticle

logger = logging.getLogger(__name__)


def convert_earnings_calendar(earnings_dates: Dict[str, str]) -> Dict[str, Dict]:
    """
    Convert earnings calendar from {symbol: 'YYYY-MM-DD'} format
    to {symbol: {'days_until': N, 'date': 'YYYY-MM-DD'}} format

    Args:
        earnings_dates: Dictionary mapping symbols to date strings

    Returns:
        Dictionary mapping symbols to earnings info dicts
    """
    result = {}
    today = datetime.now().date()

    for symbol, date_str in earnings_dates.items():
        try:
            # Parse the date string
            earnings_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            # Calculate days until earnings
            days_until = (earnings_date - today).days

            result[symbol] = {
                'days_until': days_until,
                'date': date_str
            }
        except (ValueError, AttributeError) as e:
            logger.warning(f"Error parsing earnings date for {symbol}: {date_str} - {e}")
            continue

    return result


class StrategyType(Enum):
    """Available strategy types for testing"""
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    NEWS = "news"
    VOLUME = "volume"
    EARNINGS = "earnings"
    LONGTERM_TREND = "longterm_trend"
    LONGTERM_MOMENTUM = "longterm_momentum"
    CRYPTO = "crypto"


@dataclass
class StrategySignal:
    """Individual strategy signal details"""
    timestamp: str
    symbol: str
    strategy: str
    score: float
    confidence: float
    details: Dict[str, Any]
    market_data: Dict[str, Any]
    regime: Optional[str] = None


@dataclass
class StrategyTrade:
    """Trade executed based on strategy signal"""
    entry_time: str
    symbol: str
    strategy: str
    entry_price: float
    qty: int
    entry_score: float
    entry_details: Dict[str, Any]
    stop_loss: float
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    hold_time_minutes: Optional[int] = None
    regime: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


@dataclass
class DetailedStrategyMetrics:
    """Comprehensive metrics for strategy performance analysis"""

    # Basic Information
    strategy_name: str
    test_mode: str  # "backtest" or "live"
    test_start: str
    test_end: str
    total_test_duration_hours: float

    # Trade Statistics
    total_signals: int = 0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0

    # P&L Metrics
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0

    # Risk Metrics
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Timing Metrics
    avg_hold_time_minutes: float = 0.0
    median_hold_time_minutes: float = 0.0
    min_hold_time_minutes: float = 0.0
    max_hold_time_minutes: float = 0.0

    # Signal Quality Metrics
    avg_entry_score: float = 0.0
    median_entry_score: float = 0.0
    avg_winning_score: float = 0.0
    avg_losing_score: float = 0.0
    score_predictive_power: float = 0.0  # correlation between score and outcome

    # Exit Reason Breakdown
    stop_loss_exits: int = 0
    take_profit_exits: int = 0
    time_exits: int = 0
    signal_exits: int = 0

    # Regime Performance
    regime_performance: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Time-based Performance
    hourly_performance: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    daily_performance: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Score Distribution
    score_distribution: Dict[str, int] = field(default_factory=dict)  # 0-0.3, 0.3-0.5, etc.

    # Equity Curve
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)

    # Strategy-Specific Metrics (dynamic based on strategy type)
    strategy_specific_metrics: Dict[str, Any] = field(default_factory=dict)

    # Configuration used for this test
    test_parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)

        # Recursively convert enums to strings for JSON serialization
        def convert_enums(obj):
            if isinstance(obj, dict):
                return {k: convert_enums(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_enums(item) for item in obj]
            elif isinstance(obj, tuple):
                return tuple(convert_enums(item) for item in obj)
            elif hasattr(obj, 'value'):  # Enum
                return obj.value
            else:
                return obj

        return convert_enums(data)

    def to_json(self, filepath: str):
        """Export metrics to JSON file"""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Metrics exported to {filepath}")

    def print_summary(self):
        """Print human-readable summary"""
        print("\n" + "="*80)
        print(f"STRATEGY TEST RESULTS: {self.strategy_name.upper()}")
        print("="*80)
        print(f"\nTest Mode: {self.test_mode}")
        print(f"Duration: {self.test_start} to {self.test_end} ({self.total_test_duration_hours:.1f} hours)")

        print(f"\n{'SIGNAL & TRADE STATISTICS':-^80}")
        print(f"Total Signals Generated: {self.total_signals}")
        print(f"Total Trades Executed: {self.total_trades}")
        print(f"Signal-to-Trade Conversion: {(self.total_trades/self.total_signals*100 if self.total_signals > 0 else 0):.1f}%")

        print(f"\n{'WIN/LOSS BREAKDOWN':-^80}")
        print(f"Winning Trades: {self.winning_trades} ({self.win_rate:.1f}%)")
        print(f"Losing Trades: {self.losing_trades}")
        print(f"Breakeven Trades: {self.breakeven_trades}")

        print(f"\n{'PROFIT & LOSS':-^80}")
        print(f"Total P&L: ${self.total_pnl:,.2f} ({self.total_pnl_pct:+.2f}%)")
        print(f"Average Winner: ${self.avg_win:,.2f}")
        print(f"Average Loser: ${self.avg_loss:,.2f}")
        print(f"Largest Winner: ${self.largest_win:,.2f}")
        print(f"Largest Loser: ${self.largest_loss:,.2f}")
        print(f"Profit Factor: {self.profit_factor:.2f}")

        print(f"\n{'RISK METRICS':-^80}")
        print(f"Max Drawdown: ${self.max_drawdown:,.2f} ({self.max_drawdown_pct:.2f}%)")
        print(f"Sharpe Ratio: {self.sharpe_ratio:.2f}")
        print(f"Sortino Ratio: {self.sortino_ratio:.2f}")
        print(f"Calmar Ratio: {self.calmar_ratio:.2f}")

        print(f"\n{'TIMING METRICS':-^80}")
        print(f"Average Hold Time: {self.avg_hold_time_minutes:.1f} minutes")
        print(f"Median Hold Time: {self.median_hold_time_minutes:.1f} minutes")
        print(f"Hold Time Range: {self.min_hold_time_minutes:.1f} - {self.max_hold_time_minutes:.1f} minutes")

        print(f"\n{'SIGNAL QUALITY':-^80}")
        print(f"Average Entry Score: {self.avg_entry_score:.3f}")
        print(f"Median Entry Score: {self.median_entry_score:.3f}")
        print(f"Avg Winning Score: {self.avg_winning_score:.3f}")
        print(f"Avg Losing Score: {self.avg_losing_score:.3f}")
        print(f"Score Predictive Power: {self.score_predictive_power:.3f}")

        print(f"\n{'EXIT REASONS':-^80}")
        print(f"Stop Loss: {self.stop_loss_exits} ({self.stop_loss_exits/self.total_trades*100 if self.total_trades > 0 else 0:.1f}%)")
        print(f"Take Profit: {self.take_profit_exits} ({self.take_profit_exits/self.total_trades*100 if self.total_trades > 0 else 0:.1f}%)")
        print(f"Time Exit: {self.time_exits} ({self.time_exits/self.total_trades*100 if self.total_trades > 0 else 0:.1f}%)")
        print(f"Signal Exit: {self.signal_exits} ({self.signal_exits/self.total_trades*100 if self.total_trades > 0 else 0:.1f}%)")

        if self.regime_performance:
            print(f"\n{'REGIME PERFORMANCE':-^80}")
            for regime, perf in self.regime_performance.items():
                print(f"{regime}: {perf.get('trades', 0)} trades, "
                      f"Win Rate: {perf.get('win_rate', 0):.1f}%, "
                      f"Avg P&L: ${perf.get('avg_pnl', 0):,.2f}")

        if self.strategy_specific_metrics:
            print(f"\n{'STRATEGY-SPECIFIC METRICS':-^80}")
            for key, value in self.strategy_specific_metrics.items():
                print(f"{key}: {value}")

        print("\n" + "="*80)


@dataclass
class StrategyTestConfig:
    """Configuration for strategy testing"""

    # Strategy Selection
    strategy: StrategyType

    # Test Mode
    mode: str = "backtest"  # "backtest" or "live"

    # Time Range (for backtesting)
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    # Test Duration (for live testing)
    live_duration_minutes: int = 60

    # Entry/Exit Parameters
    entry_threshold: float = 0.5  # Minimum score to enter
    exit_threshold: float = 0.3   # Score to exit
    stop_loss_pct: float = 0.5    # Stop loss percentage
    take_profit_pct: float = 2.0  # Take profit percentage
    max_hold_minutes: int = 240   # Maximum hold time

    # Position Sizing
    position_size_pct: float = 2.0  # % of capital per trade
    max_positions: int = 5

    # Market Conditions
    test_symbols: List[str] = field(default_factory=list)  # If empty, use default watchlist

    # Strategy-Specific Parameters
    strategy_params: Dict[str, Any] = field(default_factory=dict)

    # Simulation Parameters
    starting_capital: float = 100000.0
    commission: float = 1.0
    slippage_bps: float = 2.0

    # Output
    output_dir: str = "./test_results"
    save_trades: bool = True
    save_signals: bool = True


class StrategyBacktester:
    """
    Backtester for individual strategies

    Tests a single strategy in isolation against historical data,
    collecting comprehensive metrics for analysis.
    """

    def __init__(self, config: StrategyTestConfig, broker: Optional[AlpacaBroker] = None):
        self.config = config
        self.broker = broker or AlpacaBroker()

        # Strategy function mapping
        self.strategy_functions = {
            StrategyType.MOMENTUM: score_momentum,
            StrategyType.MEAN_REVERSION: score_mean_reversion,
            StrategyType.NEWS: score_news,
            StrategyType.VOLUME: score_volume,
            StrategyType.EARNINGS: score_earnings,
            StrategyType.LONGTERM_TREND: score_longterm_trend,
            StrategyType.LONGTERM_MOMENTUM: score_longterm_momentum,
            StrategyType.CRYPTO: score_crypto,
        }

        # Test state
        self.signals: List[StrategySignal] = []
        self.trades: List[StrategyTrade] = []
        self.open_positions: Dict[str, StrategyTrade] = {}
        self.equity = config.starting_capital
        self.peak_equity = config.starting_capital
        self.equity_curve: List[Tuple[datetime, float]] = []

        logger.info(f"Initialized StrategyBacktester for {config.strategy.value}")

    def _get_strategy_function(self) -> Callable:
        """Get the scoring function for the configured strategy"""
        return self.strategy_functions[self.config.strategy]

    def _calculate_score(self, symbol: str, snapshot: Dict, **kwargs) -> Tuple[float, Dict]:
        """Calculate strategy score for a symbol"""
        strategy_func = self._get_strategy_function()
        strategy_type = self.config.strategy

        try:
            # Extract common data from snapshot
            latest_trade = snapshot.get('latestTrade', {})
            daily_bar = snapshot.get('dailyBar', {})
            prev_bar = snapshot.get('prevDailyBar', {})

            current_price = latest_trade.get('p', 0)
            open_price = daily_bar.get('o', current_price)
            high = daily_bar.get('h', current_price)
            low = daily_bar.get('l', current_price)
            volume = daily_bar.get('v', 0)
            prev_close = prev_bar.get('c', open_price)

            # Call appropriate strategy function
            if strategy_type == StrategyType.MOMENTUM:
                score, details = strategy_func(current_price, open_price, prev_close, high, low)

            elif strategy_type == StrategyType.MEAN_REVERSION:
                score, details = strategy_func(current_price, open_price, prev_close, high, low)

            elif strategy_type == StrategyType.NEWS:
                news_data = kwargs.get('news_data', [])
                window_hours = self.config.strategy_params.get('news_window_hours', 6)
                score, details = strategy_func(symbol, news_data, window_hours)

            elif strategy_type == StrategyType.VOLUME:
                avg_volume = prev_bar.get('v', volume)
                score, details = strategy_func(volume, avg_volume)

            elif strategy_type == StrategyType.EARNINGS:
                earnings_calendar_raw = kwargs.get('earnings_calendar', {})
                # Convert from {symbol: 'YYYY-MM-DD'} to {symbol: {'days_until': N}}
                earnings_calendar = convert_earnings_calendar(earnings_calendar_raw) if earnings_calendar_raw else {}
                days_limit = self.config.strategy_params.get('earnings_days_limit', 7)
                score, details = strategy_func(symbol, earnings_calendar, days_limit)

            elif strategy_type == StrategyType.LONGTERM_TREND:
                score, details = strategy_func(current_price, prev_close, snapshot)

            elif strategy_type == StrategyType.LONGTERM_MOMENTUM:
                score, details = strategy_func(current_price, open_price, prev_close, snapshot)

            elif strategy_type == StrategyType.CRYPTO:
                score, details = strategy_func(symbol, current_price, open_price, prev_close, high, low)

            else:
                score, details = 0.0, {'error': 'Unknown strategy type'}

            return score, details

        except Exception as e:
            logger.error(f"Error calculating score for {symbol}: {e}")
            return 0.0, {'error': str(e)}

    def _detect_regime(self, snapshot: Dict) -> str:
        """Detect market regime for a symbol"""
        try:
            latest_trade = snapshot.get('latestTrade', {})
            daily_bar = snapshot.get('dailyBar', {})
            prev_bar = snapshot.get('prevDailyBar', {})

            current_price = latest_trade.get('p', 0)
            open_price = daily_bar.get('o', current_price)
            high = daily_bar.get('h', current_price)
            low = daily_bar.get('l', current_price)
            prev_close = prev_bar.get('c', open_price)

            if current_price == 0 or open_price == 0:
                return MarketRegime.UNKNOWN.value

            intraday_move = abs(current_price - open_price) / open_price * 100
            gap = abs(open_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
            range_pct = (high - low) / open_price * 100 if high > low else 0

            # Trending up
            if current_price > open_price and open_price >= prev_close and intraday_move > 1.0:
                return MarketRegime.TRENDING_UP.value

            # Trending down
            if current_price < open_price and open_price <= prev_close and intraday_move > 1.0:
                return MarketRegime.TRENDING_DOWN.value

            # High volatility
            if range_pct > 3.0 or gap > 2.0:
                return MarketRegime.HIGH_VOLATILITY.value

            # Low volatility
            if range_pct < 0.5:
                return MarketRegime.LOW_VOLATILITY.value

            # Ranging
            if 0.5 <= range_pct <= 2.0 and gap < 1.0:
                return MarketRegime.RANGING.value

            return MarketRegime.UNKNOWN.value

        except Exception as e:
            logger.error(f"Error detecting regime: {e}")
            return MarketRegime.UNKNOWN.value

    def _check_entry_signal(self, symbol: str, snapshot: Dict, **kwargs) -> Optional[StrategySignal]:
        """Check if strategy generates entry signal"""
        score, details = self._calculate_score(symbol, snapshot, **kwargs)

        if score >= self.config.entry_threshold:
            regime = self._detect_regime(snapshot)

            signal = StrategySignal(
                timestamp=datetime.now().isoformat(),
                symbol=symbol,
                strategy=self.config.strategy.value,
                score=score,
                confidence=1.0,  # Single strategy, full confidence
                details=details,
                market_data={
                    'current_price': snapshot.get('latestTrade', {}).get('p', 0),
                    'open': snapshot.get('dailyBar', {}).get('o', 0),
                    'high': snapshot.get('dailyBar', {}).get('h', 0),
                    'low': snapshot.get('dailyBar', {}).get('l', 0),
                    'volume': snapshot.get('dailyBar', {}).get('v', 0),
                    'prev_close': snapshot.get('prevDailyBar', {}).get('c', 0),
                },
                regime=regime
            )

            self.signals.append(signal)
            return signal

        return None

    def _enter_position(self, signal: StrategySignal):
        """Enter a position based on signal"""
        if len(self.open_positions) >= self.config.max_positions:
            logger.debug(f"Max positions reached, skipping entry for {signal.symbol}")
            return

        current_price = signal.market_data['current_price']
        position_value = self.equity * (self.config.position_size_pct / 100)
        qty = int(position_value / current_price)

        if qty <= 0:
            logger.debug(f"Insufficient capital for {signal.symbol}")
            return

        # Apply slippage and commission
        entry_price = current_price * (1 + self.config.slippage_bps / 10000)
        cost = qty * entry_price + self.config.commission

        if cost > self.equity:
            logger.debug(f"Insufficient capital for {signal.symbol} (need ${cost:.2f}, have ${self.equity:.2f})")
            return

        # Calculate stop loss
        stop_loss = entry_price * (1 - self.config.stop_loss_pct / 100)

        # Create trade
        trade = StrategyTrade(
            entry_time=signal.timestamp,
            symbol=signal.symbol,
            strategy=signal.strategy,
            entry_price=entry_price,
            qty=qty,
            entry_score=signal.score,
            entry_details=signal.details,
            stop_loss=stop_loss,
            regime=signal.regime
        )

        # Update state
        self.equity -= cost
        self.open_positions[signal.symbol] = trade

        logger.info(f"ENTER: {signal.symbol} @ ${entry_price:.2f} x {qty} (score: {signal.score:.3f})")

    def _check_exits(self, snapshot: Dict, **kwargs):
        """Check exit conditions for open positions"""
        to_close = []

        for symbol, trade in self.open_positions.items():
            current_price = snapshot.get('latestTrade', {}).get('p', 0)
            if current_price == 0:
                continue

            entry_time = datetime.fromisoformat(trade.entry_time)
            hold_time = (datetime.now() - entry_time).total_seconds() / 60

            exit_reason = None

            # Check stop loss
            if current_price <= trade.stop_loss:
                exit_reason = "stop_loss"

            # Check take profit
            elif (current_price - trade.entry_price) / trade.entry_price * 100 >= self.config.take_profit_pct:
                exit_reason = "take_profit"

            # Check max hold time
            elif hold_time >= self.config.max_hold_minutes:
                exit_reason = "time_exit"

            # Check signal exit
            else:
                score, _ = self._calculate_score(symbol, snapshot, **kwargs)
                if score < self.config.exit_threshold:
                    exit_reason = "signal_exit"

            if exit_reason:
                to_close.append((symbol, exit_reason, current_price))

        # Close positions
        for symbol, reason, exit_price in to_close:
            self._exit_position(symbol, reason, exit_price)

    def _exit_position(self, symbol: str, reason: str, exit_price: float):
        """Exit a position"""
        trade = self.open_positions.pop(symbol)

        # Apply slippage and commission
        exit_price_actual = exit_price * (1 - self.config.slippage_bps / 10000)
        proceeds = trade.qty * exit_price_actual - self.config.commission

        # Calculate P&L
        cost = trade.qty * trade.entry_price
        pnl = proceeds - cost
        pnl_pct = (pnl / cost) * 100

        # Update trade
        trade.exit_time = datetime.now().isoformat()
        trade.exit_price = exit_price_actual
        trade.exit_reason = reason
        trade.pnl = pnl
        trade.pnl_pct = pnl_pct

        entry_time = datetime.fromisoformat(trade.entry_time)
        exit_time = datetime.fromisoformat(trade.exit_time)
        trade.hold_time_minutes = int((exit_time - entry_time).total_seconds() / 60)

        # Update state
        self.equity += proceeds
        self.trades.append(trade)

        # Update peak equity
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        # Record equity point
        self.equity_curve.append((exit_time, self.equity))

        logger.info(f"EXIT: {symbol} @ ${exit_price_actual:.2f} | {reason} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)")

    def _update_equity(self, snapshots: Dict[str, Dict]):
        """Update equity curve with current mark-to-market"""
        total_equity = self.equity

        for symbol, trade in self.open_positions.items():
            if symbol in snapshots:
                current_price = snapshots[symbol].get('latestTrade', {}).get('p', trade.entry_price)
                unrealized_pnl = (current_price - trade.entry_price) * trade.qty
                total_equity += unrealized_pnl

        self.equity_curve.append((datetime.now(), total_equity))

    def run_backtest(self) -> DetailedStrategyMetrics:
        """Run backtest for the strategy"""
        logger.info(f"Starting backtest for {self.config.strategy.value}")
        logger.info(f"Period: {self.config.start_date} to {self.config.end_date}")

        # TODO: Implement full historical data backtesting
        # For now, this is a placeholder that would need historical bar data

        raise NotImplementedError("Full historical backtesting requires bar data integration")

    def run_live_test(self, news_data: Optional[List[NewsArticle]] = None,
                      earnings_calendar: Optional[Dict] = None) -> DetailedStrategyMetrics:
        """
        Run live paper trading test for the strategy

        Args:
            news_data: News articles for news strategy
            earnings_calendar: Earnings calendar for earnings strategy

        Returns:
            DetailedStrategyMetrics with comprehensive results
        """
        logger.info(f"Starting live test for {self.config.strategy.value}")
        logger.info(f"Duration: {self.config.live_duration_minutes} minutes")

        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=self.config.live_duration_minutes)

        # Get symbols to test
        symbols = self.config.test_symbols
        if not symbols:
            # Default to some popular symbols
            symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'NFLX']

        logger.info(f"Testing symbols: {', '.join(symbols)}")

        iteration = 0
        while datetime.now() < end_time:
            iteration += 1
            logger.info(f"Iteration {iteration} - {datetime.now().isoformat()}")

            try:
                # Get market data
                snapshots = self.broker.get_batch_snapshots(symbols)

                # Check exits first
                self._check_exits(snapshots[list(snapshots.keys())[0]] if snapshots else {},
                                 news_data=news_data, earnings_calendar=earnings_calendar)

                # Check for new entry signals
                for symbol in symbols:
                    if symbol in self.open_positions:
                        continue

                    if symbol not in snapshots:
                        continue

                    snapshot = snapshots[symbol]
                    signal = self._check_entry_signal(
                        symbol, snapshot,
                        news_data=news_data,
                        earnings_calendar=earnings_calendar
                    )

                    if signal:
                        self._enter_position(signal)

                # Update equity curve
                self._update_equity(snapshots)

            except Exception as e:
                logger.error(f"Error in iteration {iteration}: {e}")

            # Sleep until next iteration (check every 30 seconds)
            import time
            time.sleep(30)

        # Close any remaining positions
        logger.info("Test complete, closing remaining positions")
        final_snapshots = self.broker.get_batch_snapshots(list(self.open_positions.keys()))
        for symbol in list(self.open_positions.keys()):
            if symbol in final_snapshots:
                current_price = final_snapshots[symbol].get('latestTrade', {}).get('p', 0)
                if current_price > 0:
                    self._exit_position(symbol, "test_end", current_price)

        # Calculate metrics
        return self._calculate_metrics(start_time, end_time)

    def _calculate_metrics(self, start_time: datetime, end_time: datetime) -> DetailedStrategyMetrics:
        """Calculate comprehensive metrics from test results"""

        metrics = DetailedStrategyMetrics(
            strategy_name=self.config.strategy.value,
            test_mode=self.config.mode,
            test_start=start_time.isoformat(),
            test_end=end_time.isoformat(),
            total_test_duration_hours=(end_time - start_time).total_seconds() / 3600,
            test_parameters=asdict(self.config)
        )

        # Basic counts
        metrics.total_signals = len(self.signals)
        metrics.total_trades = len(self.trades)

        if metrics.total_trades == 0:
            logger.warning("No trades executed during test period")
            return metrics

        # Win/Loss statistics
        winners = [t for t in self.trades if t.pnl and t.pnl > 0]
        losers = [t for t in self.trades if t.pnl and t.pnl < 0]
        breakevens = [t for t in self.trades if t.pnl == 0]

        metrics.winning_trades = len(winners)
        metrics.losing_trades = len(losers)
        metrics.breakeven_trades = len(breakevens)
        metrics.win_rate = (metrics.winning_trades / metrics.total_trades) * 100

        # P&L metrics
        metrics.total_pnl = sum(t.pnl for t in self.trades if t.pnl)
        metrics.total_pnl_pct = (metrics.total_pnl / self.config.starting_capital) * 100

        if winners:
            metrics.avg_win = statistics.mean([t.pnl for t in winners])
            metrics.largest_win = max([t.pnl for t in winners])
            metrics.avg_winning_score = statistics.mean([t.entry_score for t in winners])

        if losers:
            metrics.avg_loss = statistics.mean([t.pnl for t in losers])
            metrics.largest_loss = min([t.pnl for t in losers])
            metrics.avg_losing_score = statistics.mean([t.entry_score for t in losers])

        # Profit factor
        gross_profit = sum(t.pnl for t in winners) if winners else 0
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Risk metrics
        if self.equity_curve:
            equity_values = [eq for _, eq in self.equity_curve]
            peak = self.config.starting_capital
            max_dd = 0

            for eq in equity_values:
                if eq > peak:
                    peak = eq
                dd = peak - eq
                if dd > max_dd:
                    max_dd = dd

            metrics.max_drawdown = max_dd
            metrics.max_drawdown_pct = (max_dd / peak) * 100 if peak > 0 else 0

            # Calculate returns for Sharpe ratio
            returns = []
            for i in range(1, len(equity_values)):
                ret = (equity_values[i] - equity_values[i-1]) / equity_values[i-1]
                returns.append(ret)

            if returns:
                avg_return = statistics.mean(returns)
                std_return = statistics.stdev(returns) if len(returns) > 1 else 0

                # Annualized Sharpe (assuming 252 trading days, 6.5 hours per day)
                periods_per_year = 252 * 13  # 13 thirty-minute periods per day
                metrics.sharpe_ratio = (avg_return * periods_per_year) / (std_return * (periods_per_year ** 0.5)) if std_return > 0 else 0

                # Sortino ratio (uses downside deviation)
                negative_returns = [r for r in returns if r < 0]
                downside_std = statistics.stdev(negative_returns) if len(negative_returns) > 1 else std_return
                metrics.sortino_ratio = (avg_return * periods_per_year) / (downside_std * (periods_per_year ** 0.5)) if downside_std > 0 else 0

                # Calmar ratio (return / max drawdown)
                annual_return = avg_return * periods_per_year
                metrics.calmar_ratio = annual_return / (metrics.max_drawdown_pct / 100) if metrics.max_drawdown_pct > 0 else 0

                metrics.daily_returns = returns

        # Timing metrics
        hold_times = [t.hold_time_minutes for t in self.trades if t.hold_time_minutes]
        if hold_times:
            metrics.avg_hold_time_minutes = statistics.mean(hold_times)
            metrics.median_hold_time_minutes = statistics.median(hold_times)
            metrics.min_hold_time_minutes = min(hold_times)
            metrics.max_hold_time_minutes = max(hold_times)

        # Signal quality
        entry_scores = [t.entry_score for t in self.trades]
        if entry_scores:
            metrics.avg_entry_score = statistics.mean(entry_scores)
            metrics.median_entry_score = statistics.median(entry_scores)

            # Score predictive power (correlation with outcome)
            if len(self.trades) > 1:
                outcomes = [1 if t.pnl and t.pnl > 0 else 0 for t in self.trades]
                # Simple correlation coefficient
                mean_score = metrics.avg_entry_score
                mean_outcome = statistics.mean(outcomes)

                numerator = sum((score - mean_score) * (outcome - mean_outcome)
                               for score, outcome in zip(entry_scores, outcomes))

                score_var = sum((score - mean_score) ** 2 for score in entry_scores)
                outcome_var = sum((outcome - mean_outcome) ** 2 for outcome in outcomes)

                denominator = (score_var * outcome_var) ** 0.5
                metrics.score_predictive_power = numerator / denominator if denominator > 0 else 0

        # Exit reason breakdown
        for trade in self.trades:
            if trade.exit_reason == "stop_loss":
                metrics.stop_loss_exits += 1
            elif trade.exit_reason == "take_profit":
                metrics.take_profit_exits += 1
            elif trade.exit_reason == "time_exit":
                metrics.time_exits += 1
            elif trade.exit_reason == "signal_exit":
                metrics.signal_exits += 1

        # Regime performance
        regime_stats = {}
        for trade in self.trades:
            regime = trade.regime or "UNKNOWN"
            if regime not in regime_stats:
                regime_stats[regime] = {'trades': [], 'wins': 0}

            regime_stats[regime]['trades'].append(trade)
            if trade.pnl and trade.pnl > 0:
                regime_stats[regime]['wins'] += 1

        for regime, stats in regime_stats.items():
            trades_list = stats['trades']
            metrics.regime_performance[regime] = {
                'trades': len(trades_list),
                'win_rate': (stats['wins'] / len(trades_list)) * 100,
                'avg_pnl': statistics.mean([t.pnl for t in trades_list if t.pnl]),
                'total_pnl': sum(t.pnl for t in trades_list if t.pnl)
            }

        # Score distribution
        score_buckets = {
            '0.0-0.3': 0, '0.3-0.5': 0, '0.5-0.7': 0, '0.7-0.9': 0, '0.9-1.0': 0
        }

        for signal in self.signals:
            score = signal.score
            if score < 0.3:
                score_buckets['0.0-0.3'] += 1
            elif score < 0.5:
                score_buckets['0.3-0.5'] += 1
            elif score < 0.7:
                score_buckets['0.5-0.7'] += 1
            elif score < 0.9:
                score_buckets['0.7-0.9'] += 1
            else:
                score_buckets['0.9-1.0'] += 1

        metrics.score_distribution = score_buckets
        metrics.equity_curve = [(ts.isoformat(), eq) for ts, eq in self.equity_curve]

        return metrics


def compare_strategies(results: List[DetailedStrategyMetrics], output_file: str = None) -> Dict:
    """
    Compare multiple strategy test results

    Args:
        results: List of strategy metrics to compare
        output_file: Optional file path to save comparison report

    Returns:
        Comparison report dictionary
    """
    if not results:
        return {}

    comparison = {
        'timestamp': datetime.now().isoformat(),
        'strategies_compared': len(results),
        'strategy_names': [r.strategy_name for r in results],
        'rankings': {},
        'detailed_comparison': {}
    }

    # Rank strategies by various metrics
    metrics_to_rank = [
        ('total_pnl', 'Total P&L', True),
        ('win_rate', 'Win Rate', True),
        ('profit_factor', 'Profit Factor', True),
        ('sharpe_ratio', 'Sharpe Ratio', True),
        ('max_drawdown_pct', 'Max Drawdown %', False),
        ('avg_hold_time_minutes', 'Avg Hold Time', False),
        ('score_predictive_power', 'Score Predictive Power', True),
    ]

    for metric_key, metric_name, higher_better in metrics_to_rank:
        sorted_results = sorted(results,
                               key=lambda r: getattr(r, metric_key, 0),
                               reverse=higher_better)

        comparison['rankings'][metric_name] = [
            {
                'strategy': r.strategy_name,
                'value': getattr(r, metric_key, 0)
            }
            for r in sorted_results
        ]

    # Detailed comparison table
    for result in results:
        comparison['detailed_comparison'][result.strategy_name] = {
            'Total P&L': f"${result.total_pnl:,.2f}",
            'P&L %': f"{result.total_pnl_pct:.2f}%",
            'Win Rate': f"{result.win_rate:.1f}%",
            'Total Trades': result.total_trades,
            'Profit Factor': f"{result.profit_factor:.2f}",
            'Sharpe Ratio': f"{result.sharpe_ratio:.2f}",
            'Max Drawdown': f"{result.max_drawdown_pct:.2f}%",
            'Avg Hold Time': f"{result.avg_hold_time_minutes:.1f} min",
            'Score Predictive Power': f"{result.score_predictive_power:.3f}",
        }

    # Save to file if requested
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(comparison, f, indent=2)
        logger.info(f"Comparison report saved to {output_file}")

    # Print comparison
    print("\n" + "="*100)
    print("STRATEGY COMPARISON REPORT")
    print("="*100)

    for metric_name, rankings in comparison['rankings'].items():
        print(f"\n{metric_name}:")
        for i, rank in enumerate(rankings, 1):
            print(f"  {i}. {rank['strategy']}: {rank['value']}")

    print("\n" + "="*100)

    return comparison
