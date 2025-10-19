"""
Backtesting Framework
Test strategies on historical data with realistic constraints
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import json


@dataclass
class BacktestTrade:
    """Single trade in backtest"""
    symbol: str
    entry_time: datetime
    entry_price: float
    qty: int
    stop_loss: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_time_minutes: float = 0.0
    regime: Optional[str] = None


@dataclass
class BacktestMetrics:
    """Backtest performance metrics"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    
    avg_hold_time_minutes: float = 0.0
    trades_per_day: float = 0.0
    
    starting_capital: float = 0.0
    ending_capital: float = 0.0
    total_return_pct: float = 0.0
    
    daily_returns: List[float] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.win_rate,
            'total_pnl': self.total_pnl,
            'total_pnl_pct': self.total_pnl_pct,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'largest_win': self.largest_win,
            'largest_loss': self.largest_loss,
            'profit_factor': self.profit_factor,
            'sharpe_ratio': self.sharpe_ratio,
            'max_drawdown': self.max_drawdown,
            'max_drawdown_pct': self.max_drawdown_pct,
            'avg_hold_time_minutes': self.avg_hold_time_minutes,
            'trades_per_day': self.trades_per_day,
            'starting_capital': self.starting_capital,
            'ending_capital': self.ending_capital,
            'total_return_pct': self.total_return_pct
        }


class Backtester:
    """
    Backtesting engine for trading strategies
    Simulates realistic trading with slippage, commissions, and constraints
    """
    
    def __init__(
        self,
        initial_capital: float = 100000.0,
        commission_per_trade: float = 1.0,
        slippage_bps: float = 2.0,
        logger: Optional[logging.Logger] = None
    ):
        self.initial_capital = initial_capital
        self.commission = commission_per_trade
        self.slippage_bps = slippage_bps
        self.logger = logger or logging.getLogger(__name__)
        
        # State
        self.cash = initial_capital
        self.positions: Dict[str, BacktestTrade] = {}
        self.closed_trades: List[BacktestTrade] = []
        self.equity_history: List[Tuple[datetime, float]] = []
        
    def reset(self):
        """Reset backtest state"""
        self.cash = self.initial_capital
        self.positions.clear()
        self.closed_trades.clear()
        self.equity_history.clear()
    
    def apply_slippage(self, price: float, side: str) -> float:
        """Apply realistic slippage to price"""
        slippage = price * (self.slippage_bps / 10000.0)
        if side == 'buy':
            return price + slippage
        else:  # sell
            return price - slippage
    
    def can_open_position(
        self,
        symbol: str,
        price: float,
        qty: int,
        max_positions: int = 10
    ) -> Tuple[bool, str]:
        """Check if we can open a new position"""
        if symbol in self.positions:
            return False, "Position already open"
        
        if len(self.positions) >= max_positions:
            return False, f"Max positions ({max_positions}) reached"
        
        cost = qty * price + self.commission
        if cost > self.cash:
            return False, f"Insufficient cash: ${self.cash:.2f} < ${cost:.2f}"
        
        return True, "OK"
    
    def open_position(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        qty: int,
        stop_loss: float,
        regime: Optional[str] = None
    ) -> bool:
        """Open a new position"""
        can_open, reason = self.can_open_position(symbol, price, qty)
        if not can_open:
            self.logger.debug(f"Cannot open {symbol}: {reason}")
            return False
        
        # Apply slippage and commission
        entry_price = self.apply_slippage(price, 'buy')
        cost = qty * entry_price + self.commission
        
        self.cash -= cost
        
        trade = BacktestTrade(
            symbol=symbol,
            entry_time=timestamp,
            entry_price=entry_price,
            qty=qty,
            stop_loss=stop_loss,
            regime=regime
        )
        
        self.positions[symbol] = trade
        
        self.logger.debug(
            f"OPEN {symbol}: {qty} @ ${entry_price:.2f}, "
            f"stop=${stop_loss:.2f}, cash=${self.cash:.2f}"
        )
        
        return True
    
    def close_position(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        reason: str
    ) -> bool:
        """Close an existing position"""
        if symbol not in self.positions:
            return False
        
        trade = self.positions[symbol]
        
        # Apply slippage and commission
        exit_price = self.apply_slippage(price, 'sell')
        proceeds = trade.qty * exit_price - self.commission
        
        self.cash += proceeds
        
        # Calculate P&L
        cost = trade.qty * trade.entry_price
        trade.pnl = proceeds - cost - self.commission  # Include entry commission
        trade.pnl_pct = (trade.pnl / cost) * 100
        
        # Record exit details
        trade.exit_time = timestamp
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.hold_time_minutes = (timestamp - trade.entry_time).total_seconds() / 60
        
        # Move to closed trades
        self.closed_trades.append(trade)
        del self.positions[symbol]
        
        self.logger.debug(
            f"CLOSE {symbol}: {trade.qty} @ ${exit_price:.2f}, "
            f"P/L=${trade.pnl:.2f} ({trade.pnl_pct:.2f}%), reason={reason}"
        )
        
        return True
    
    def update_equity(self, timestamp: datetime, current_prices: Dict[str, float]):
        """Update equity curve with current prices"""
        position_value = sum(
            pos.qty * current_prices.get(pos.symbol, pos.entry_price)
            for pos in self.positions.values()
        )
        
        total_equity = self.cash + position_value
        self.equity_history.append((timestamp, total_equity))
    
    def check_exits(
        self,
        timestamp: datetime,
        current_prices: Dict[str, float],
        take_profit_pct: float = 2.0,
        max_hold_minutes: int = 240
    ) -> List[str]:
        """
        Check all positions for exit conditions
        Returns list of symbols that were closed
        """
        closed_symbols = []
        
        for symbol, trade in list(self.positions.items()):
            current_price = current_prices.get(symbol)
            if not current_price:
                continue
            
            # Check stop loss
            if current_price <= trade.stop_loss:
                if self.close_position(symbol, timestamp, current_price, "stop_loss"):
                    closed_symbols.append(symbol)
                continue
            
            # Check take profit
            pnl_pct = ((current_price - trade.entry_price) / trade.entry_price) * 100
            if pnl_pct >= take_profit_pct:
                if self.close_position(symbol, timestamp, current_price, "take_profit"):
                    closed_symbols.append(symbol)
                continue
            
            # Check max hold time
            hold_time = (timestamp - trade.entry_time).total_seconds() / 60
            if hold_time >= max_hold_minutes:
                if self.close_position(symbol, timestamp, current_price, "time_exit"):
                    closed_symbols.append(symbol)
                continue
        
        return closed_symbols
    
    def calculate_metrics(self, trading_days: int = None) -> BacktestMetrics:
        """Calculate comprehensive backtest metrics"""
        metrics = BacktestMetrics()
        
        if not self.closed_trades:
            self.logger.warning("No trades to analyze")
            return metrics
        
        metrics.starting_capital = self.initial_capital
        metrics.ending_capital = self.cash + sum(
            pos.qty * pos.entry_price for pos in self.positions.values()
        )
        
        metrics.total_trades = len(self.closed_trades)
        
        wins = [t for t in self.closed_trades if t.pnl > 0]
        losses = [t for t in self.closed_trades if t.pnl <= 0]
        
        metrics.winning_trades = len(wins)
        metrics.losing_trades = len(losses)
        metrics.win_rate = (len(wins) / len(self.closed_trades)) * 100 if self.closed_trades else 0
        
        # P&L metrics
        metrics.total_pnl = sum(t.pnl for t in self.closed_trades)
        metrics.total_pnl_pct = (
            (metrics.ending_capital - metrics.starting_capital) / metrics.starting_capital
        ) * 100
        
        if wins:
            metrics.avg_win = sum(t.pnl for t in wins) / len(wins)
            metrics.largest_win = max(t.pnl for t in wins)
        
        if losses:
            metrics.avg_loss = sum(t.pnl for t in losses) / len(losses)
            metrics.largest_loss = min(t.pnl for t in losses)
        
        # Profit factor
        gross_profit = sum(t.pnl for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl for t in losses)) if losses else 0
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Hold time
        if self.closed_trades:
            metrics.avg_hold_time_minutes = sum(
                t.hold_time_minutes for t in self.closed_trades
            ) / len(self.closed_trades)
        
        # Trades per day
        if trading_days:
            metrics.trades_per_day = len(self.closed_trades) / trading_days
        
        # Calculate drawdown from equity curve
        if self.equity_history:
            metrics.equity_curve = self.equity_history
            peak = self.initial_capital
            max_dd = 0
            max_dd_pct = 0
            
            for _, equity in self.equity_history:
                if equity > peak:
                    peak = equity
                dd = peak - equity
                dd_pct = (dd / peak) * 100 if peak > 0 else 0
                
                if dd > max_dd:
                    max_dd = dd
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
            
            metrics.max_drawdown = max_dd
            metrics.max_drawdown_pct = max_dd_pct
        
        # Calculate Sharpe ratio
        if len(self.equity_history) > 1:
            returns = []
            for i in range(1, len(self.equity_history)):
                prev_equity = self.equity_history[i-1][1]
                curr_equity = self.equity_history[i][1]
                if prev_equity > 0:
                    ret = (curr_equity - prev_equity) / prev_equity
                    returns.append(ret)
            
            if returns:
                import statistics
                avg_return = statistics.mean(returns)
                std_return = statistics.stdev(returns) if len(returns) > 1 else 0
                
                # Annualize (assuming 252 trading days)
                if std_return > 0:
                    metrics.sharpe_ratio = (avg_return / std_return) * (252 ** 0.5)
                metrics.daily_returns = returns
        
        return metrics
    
    def print_summary(self, metrics: BacktestMetrics):
        """Print backtest summary"""
        print("\n" + "="*60)
        print("BACKTEST SUMMARY")
        print("="*60)
        print(f"Initial Capital:    ${metrics.starting_capital:,.2f}")
        print(f"Ending Capital:     ${metrics.ending_capital:,.2f}")
        print(f"Total Return:       {metrics.total_pnl_pct:+.2f}%")
        print(f"Total P/L:          ${metrics.total_pnl:+,.2f}")
        print()
        print(f"Total Trades:       {metrics.total_trades}")
        print(f"Winning Trades:     {metrics.winning_trades} ({metrics.win_rate:.1f}%)")
        print(f"Losing Trades:      {metrics.losing_trades}")
        print()
        print(f"Average Win:        ${metrics.avg_win:+,.2f}")
        print(f"Average Loss:       ${metrics.avg_loss:+,.2f}")
        print(f"Largest Win:        ${metrics.largest_win:+,.2f}")
        print(f"Largest Loss:       ${metrics.largest_loss:+,.2f}")
        print(f"Profit Factor:      {metrics.profit_factor:.2f}")
        print()
        print(f"Max Drawdown:       ${metrics.max_drawdown:,.2f} ({metrics.max_drawdown_pct:.2f}%)")
        print(f"Sharpe Ratio:       {metrics.sharpe_ratio:.2f}")
        print(f"Avg Hold Time:      {metrics.avg_hold_time_minutes:.1f} minutes")
        if metrics.trades_per_day > 0:
            print(f"Trades Per Day:     {metrics.trades_per_day:.1f}")
        print("="*60 + "\n")
    
    def export_trades(self, filename: str):
        """Export trade log to JSON"""
        trades_data = []
        for trade in self.closed_trades:
            trades_data.append({
                'symbol': trade.symbol,
                'entry_time': trade.entry_time.isoformat() if trade.entry_time else None,
                'entry_price': trade.entry_price,
                'qty': trade.qty,
                'stop_loss': trade.stop_loss,
                'exit_time': trade.exit_time.isoformat() if trade.exit_time else None,
                'exit_price': trade.exit_price,
                'exit_reason': trade.exit_reason,
                'pnl': trade.pnl,
                'pnl_pct': trade.pnl_pct,
                'hold_time_minutes': trade.hold_time_minutes,
                'regime': trade.regime
            })
        
        with open(filename, 'w') as f:
            json.dump(trades_data, f, indent=2)
        
        self.logger.info(f"Exported {len(trades_data)} trades to {filename}")
