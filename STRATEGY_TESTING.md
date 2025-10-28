# Strategy Testing Framework

Comprehensive testing and optimization framework for individual trading strategies.

## Overview

This framework allows you to:
- **Test individual strategies** in isolation with live paper trading
- **Backtest strategies** against historical data (when integrated with bar data)
- **Optimize parameters** through systematic parameter sweeps
- **Compare strategies** side-by-side with detailed metrics
- **Generate AI-readable reports** for detailed analysis and recommendations

## Architecture

### Core Components

1. **strategy_testing.py** - Main testing engine
   - `StrategyBacktester` - Runs individual strategy tests
   - `DetailedStrategyMetrics` - Comprehensive performance metrics
   - `StrategySignal` & `StrategyTrade` - Signal and trade tracking
   - `compare_strategies()` - Multi-strategy comparison

2. **strategy_optimizer.py** - Parameter optimization
   - `StrategyOptimizer` - Systematic parameter sweep testing
   - `ParameterRange` - Define parameters to optimize
   - `OptimizationResult` - Results with ranking scores

3. **test_strategy.py** - CLI interface
   - Easy command-line access to all functionality
   - Multiple commands: test, test-all, optimize, compare

## Quick Start

### Test a Single Strategy

```bash
# Test momentum strategy for 60 minutes
python test_strategy.py test momentum --duration 60

# Test with custom parameters
python test_strategy.py test momentum \
  --duration 30 \
  --entry-threshold 0.7 \
  --stop-loss 0.3 \
  --take-profit 2.5

# Test specific symbols (command line)
python test_strategy.py test news \
  --symbols AAPL,MSFT,GOOGL \
  --duration 30

# Test with symbols from file
python test_strategy.py test momentum \
  --symbols-file examples/symbols.txt \
  --duration 60

# Test with main bot's watchlist
python test_strategy.py test momentum \
  --use-watchlist \
  --duration 60
```

### Test All Strategies

```bash
# Test all strategies for 30 minutes each
python test_strategy.py test-all --duration 30

# Include crypto strategy
python test_strategy.py test-all --duration 30 --include-crypto
```

### Optimize Strategy Parameters

```bash
# Full optimization (tests all parameter combinations)
python test_strategy.py optimize momentum --duration 30

# Quick optimization (reduced parameter space)
python test_strategy.py optimize momentum --duration 15 --quick

# Show top 5 results
python test_strategy.py optimize mean_reversion --top-n 5
```

### Compare Results

```bash
# Compare multiple test results
python test_strategy.py compare test_results/momentum_*.json test_results/mean_reversion_*.json
```

## Symbol Management

The testing framework provides flexible options for specifying which symbols to test:

### Option 1: Command Line (--symbols)

Pass symbols directly as a comma-separated list:

```bash
python test_strategy.py test momentum --symbols AAPL,MSFT,GOOGL,AMZN
```

### Option 2: From File (--symbols-file)

Create a file with symbols (text or CSV format):

**Text format** (examples/symbols.txt):
```
# Comments start with #
AAPL
MSFT
GOOGL
AMZN
TSLA
```

**CSV format** (examples/symbols.csv):
```
SYMBOL
AAPL
MSFT
GOOGL
```

Then use it:
```bash
python test_strategy.py test momentum --symbols-file examples/symbols.txt
python test_strategy.py test momentum --symbols-file my_watchlist.csv
```

### Option 3: Main Bot's Watchlist (--use-watchlist)

Use the same symbols your main trading bot uses (from `DAYTRADER_UNIVERSE` env var):

```bash
# Use your bot's watchlist
python test_strategy.py test momentum --use-watchlist

# Include crypto symbols too
python test_strategy.py test crypto --use-watchlist --include-crypto
```

The watchlist is loaded from:
1. `DAYTRADER_UNIVERSE` environment variable (file path or comma-separated)
2. Falls back to default list if not set

### Option 4: Default Symbols

If you don't specify any option, these defaults are used:
```
AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA, META, NFLX
```

### Priority Order

When multiple options are provided, the framework uses this priority:
1. `--symbols-file` (highest priority)
2. `--symbols`
3. `--use-watchlist`
4. Default list (lowest priority)

### Examples

```bash
# Test with file
python test_strategy.py test momentum \
  --duration 30 \
  --symbols-file my_stocks.txt

# Test with watchlist
python test_strategy.py test-all \
  --duration 60 \
  --use-watchlist

# Optimize with specific symbols
python test_strategy.py optimize mean_reversion \
  --duration 20 \
  --symbols SPY,QQQ,IWM

# Compare file vs watchlist
python test_strategy.py test momentum --symbols-file tech_stocks.txt --duration 30
python test_strategy.py test momentum --use-watchlist --duration 30
python test_strategy.py compare test_results/*.json
```

## Available Strategies

All strategies from your trading system can be tested individually:

1. **momentum** - Intraday momentum and gap trading
2. **mean_reversion** - Oversold/overbought reversals
3. **news** - News sentiment-based trading
4. **volume** - Volume spike detection
5. **earnings** - Earnings announcement plays
6. **longterm_trend** - Longer-term trend following
7. **longterm_momentum** - Multi-day momentum
8. **crypto** - Cryptocurrency-specific patterns

## Comprehensive Metrics

Each test generates detailed metrics for AI analysis:

### Trade Statistics
- Total signals generated
- Signal-to-trade conversion rate
- Win rate, losing trades, breakeven trades
- Total trades executed

### P&L Metrics
- Total P&L ($ and %)
- Average win/loss amounts
- Largest win/loss
- Profit factor (gross profit / gross loss)

### Risk Metrics
- Maximum drawdown ($ and %)
- Sharpe ratio (risk-adjusted returns)
- Sortino ratio (downside risk focus)
- Calmar ratio (return / max drawdown)

### Timing Metrics
- Average, median, min, max hold times
- Hold time distribution
- Time-based performance breakdown

### Signal Quality Metrics
- Average entry score
- Score distribution across ranges
- Score predictive power (correlation with outcomes)
- Average winning vs losing scores

### Exit Analysis
- Stop loss exits (count and %)
- Take profit exits
- Time-based exits
- Signal-based exits

### Market Regime Performance
- Performance breakdown by regime:
  - TRENDING_UP
  - TRENDING_DOWN
  - RANGING
  - HIGH_VOLATILITY
  - LOW_VOLATILITY

### Strategy-Specific Metrics
- Custom metrics based on strategy type
- Parameter-specific tracking

## Output Files

### Test Results
Location: `./test_results/`

Each test generates:
- `{strategy}_test_{timestamp}.json` - Full metrics
- `{strategy}_trades_{timestamp}.json` - Individual trade details
- `{strategy}_signals_{timestamp}.json` - All signals generated

### Optimization Results
Location: `./optimization_results/`

Each optimization generates:
- `{strategy}_optimization_{timestamp}.json` - All parameter combinations tested
- `{strategy}_top5_{timestamp}.json` - Top 5 parameter sets with key metrics

### Comparison Reports
Location: `./test_results/`

Comparison generates:
- `comparison_{timestamp}.json` - Side-by-side strategy comparison with rankings

## AI Analysis Integration

All output files are JSON-formatted for easy AI consumption. The metrics are designed to provide:

1. **Quantitative Performance** - Hard numbers for objective comparison
2. **Risk Assessment** - Multiple risk metrics for safety evaluation
3. **Signal Quality** - Understanding of strategy prediction accuracy
4. **Market Conditions** - Performance across different regimes
5. **Parameter Sensitivity** - How parameter changes affect outcomes

### Example: Using Results for AI Recommendations

```python
import json

# Load test result
with open('test_results/momentum_test_20250101_120000.json', 'r') as f:
    metrics = json.load(f)

# AI can analyze:
print(f"Strategy: {metrics['strategy_name']}")
print(f"Win Rate: {metrics['win_rate']:.1f}%")
print(f"Profit Factor: {metrics['profit_factor']:.2f}")
print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")

# Regime analysis
best_regime = max(metrics['regime_performance'].items(),
                  key=lambda x: x[1]['win_rate'])
print(f"Best Regime: {best_regime[0]} ({best_regime[1]['win_rate']:.1f}% win rate)")

# Recommendations based on metrics
if metrics['win_rate'] < 50:
    print("⚠️ Low win rate - consider increasing entry threshold")
if metrics['profit_factor'] < 1.5:
    print("⚠️ Low profit factor - review stop loss and take profit settings")
if metrics['score_predictive_power'] < 0.2:
    print("⚠️ Scores not predictive - strategy may need refinement")
```

## Parameter Optimization

### Default Parameter Ranges

The optimizer tests combinations of:

**Common Parameters (all strategies):**
- `entry_threshold`: [0.4, 0.5, 0.6, 0.7]
- `stop_loss_pct`: [0.3, 0.5, 0.7, 1.0]
- `take_profit_pct`: [1.5, 2.0, 2.5, 3.0]
- `max_hold_minutes`: [120, 180, 240, 360]

**Strategy-Specific:**
- News: `news_window_hours` [3, 6, 12, 24]
- Earnings: `earnings_days_limit` [3, 5, 7, 10]
- Crypto: Higher stop loss ranges [0.5, 1.0, 1.5, 2.0]

### Ranking Algorithm

Results are ranked using a weighted composite score:

```
rank_score = (total_pnl * 0.3) +
             (win_rate * 0.2) +
             (profit_factor * 0.15) +
             (sharpe_ratio * 0.15) +
             (max_drawdown * -0.1) +  # Negative weight
             (score_predictive_power * 0.1)
```

Values are normalized to 0-1 range before scoring.

## Advanced Usage

### Python API

You can use the framework programmatically:

```python
from trading_bot.strategy_testing import (
    StrategyType, StrategyTestConfig, StrategyBacktester
)

# Create config
config = StrategyTestConfig(
    strategy=StrategyType.MOMENTUM,
    mode='live',
    live_duration_minutes=30,
    entry_threshold=0.6,
    stop_loss_pct=0.5,
    test_symbols=['AAPL', 'MSFT', 'GOOGL']
)

# Run test
backtester = StrategyBacktester(config)
metrics = backtester.run_live_test()

# Access results
print(f"Total P&L: ${metrics.total_pnl:,.2f}")
print(f"Win Rate: {metrics.win_rate:.1f}%")
print(f"Sharpe Ratio: {metrics.sharpe_ratio:.2f}")

# Export
metrics.to_json('my_test_results.json')
```

### Custom Parameter Ranges

```python
from trading_bot.strategy_optimizer import (
    StrategyOptimizer, ParameterRange
)

# Define custom parameter ranges
param_ranges = [
    ParameterRange(
        name='entry_threshold',
        values=[0.5, 0.6, 0.7, 0.8],
        description='Entry score threshold'
    ),
    ParameterRange(
        name='stop_loss_pct',
        values=[0.25, 0.5, 0.75],
        description='Stop loss percentage'
    )
]

# Run optimization
optimizer = StrategyOptimizer(StrategyType.MOMENTUM)
results = optimizer.optimize(param_ranges, base_config)

# Get best parameters
best = results[0]
print(f"Best parameters: {best.parameters}")
print(f"Rank score: {best.rank_score:.4f}")
```

### Batch Testing

Test multiple strategies in sequence:

```python
from trading_bot.strategy_testing import compare_strategies

strategies = [
    StrategyType.MOMENTUM,
    StrategyType.MEAN_REVERSION,
    StrategyType.VOLUME
]

all_metrics = []

for strategy in strategies:
    config = StrategyTestConfig(
        strategy=strategy,
        mode='live',
        live_duration_minutes=30
    )

    backtester = StrategyBacktester(config)
    metrics = backtester.run_live_test()
    all_metrics.append(metrics)

# Compare all
comparison = compare_strategies(all_metrics, 'comparison.json')
```

## Tips for Effective Testing

### 1. Test Duration
- **Quick tests** (15-30 min): Parameter exploration, quick validation
- **Standard tests** (60-120 min): Realistic performance assessment
- **Extended tests** (4+ hours): Full market day simulation

### 2. Symbol Selection
- **High liquidity**: AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA
- **Medium cap**: Test broader market behavior
- **Sector focus**: Test sector-specific strategies

### 3. Parameter Optimization
- Start with **quick optimization** to narrow parameter space
- Run **full optimization** on promising ranges
- Test top parameters across **different market conditions**

### 4. Interpreting Results

**Good Strategy Indicators:**
- Win rate > 55%
- Profit factor > 2.0
- Sharpe ratio > 1.0
- Max drawdown < 10%
- Score predictive power > 0.3

**Warning Signs:**
- High win rate but low profit factor (small wins, big losses)
- High Sharpe but low trade count (statistical noise)
- Negative score predictive power (scores don't matter)
- High variance across regimes (strategy too specialized)

### 5. AI-Driven Recommendations

After running tests, use AI to:
1. **Analyze metric patterns** across strategies
2. **Identify optimal parameter ranges** from optimization results
3. **Suggest strategy combinations** based on regime performance
4. **Recommend risk adjustments** based on drawdown analysis
5. **Propose new strategies** based on observed patterns

## Example Workflows

### Workflow 1: New Strategy Validation

```bash
# 1. Quick test with defaults
python test_strategy.py test momentum --duration 30

# 2. If promising, optimize parameters
python test_strategy.py optimize momentum --duration 30 --quick

# 3. Test best parameters longer
python test_strategy.py test momentum \
  --duration 120 \
  --entry-threshold 0.65 \
  --stop-loss 0.4 \
  --take-profit 2.5

# 4. Compare with existing strategies
python test_strategy.py compare test_results/*.json
```

### Workflow 2: Strategy Portfolio Building

```bash
# 1. Test all strategies
python test_strategy.py test-all --duration 60

# 2. Compare results
python test_strategy.py compare test_results/*_test_*.json

# 3. Optimize top 3 performers
python test_strategy.py optimize momentum --duration 30
python test_strategy.py optimize mean_reversion --duration 30
python test_strategy.py optimize volume --duration 30

# 4. Build combined strategy with AI recommendations
# (Use comparison and optimization results to inform weighting)
```

### Workflow 3: Market Condition Analysis

```bash
# Test same strategy across different times/conditions
python test_strategy.py test momentum --duration 60 --symbols AAPL,MSFT
# Wait for different market conditions
python test_strategy.py test momentum --duration 60 --symbols AAPL,MSFT
# Compare regime performance in output

# Analyze regime-specific performance
# AI can suggest regime-based strategy selection
```

## Troubleshooting

### Issue: No trades executed

**Possible causes:**
- Entry threshold too high
- Symbols not active during test period
- Market closed

**Solutions:**
- Lower entry threshold: `--entry-threshold 0.4`
- Test during market hours
- Use more active symbols
- Check market data availability

### Issue: All trades hitting stop loss

**Possible causes:**
- Stop loss too tight
- High volatility
- Strategy not suited for current conditions

**Solutions:**
- Widen stop loss: `--stop-loss 1.0`
- Test different symbols
- Try different strategy

### Issue: Low profit factor despite good win rate

**Cause:** Small winners, large losers (wrong risk/reward)

**Solutions:**
- Tighten stop loss
- Widen take profit target
- Review exit logic

## Integration with Main Trading Bot

To integrate optimized parameters into the main bot:

1. **Review optimization results** in JSON files
2. **Update strategy weights** in `strategy_manager.py`
3. **Adjust thresholds** in `settings.py`
4. **Run in simulation mode** first
5. **Monitor live performance** and iterate

## Future Enhancements

Planned features:
- [ ] Full historical backtesting with bar data
- [ ] Walk-forward optimization
- [ ] Monte Carlo simulation
- [ ] Multi-strategy portfolio optimization
- [ ] Real-time performance tracking dashboard
- [ ] Automated parameter adaptation
- [ ] Machine learning integration for signal prediction

## Support

For issues or questions:
1. Check this documentation
2. Review example outputs in `test_results/`
3. Run tests with `--help` flag for options
4. Check logs for detailed error messages

## Summary

This framework provides everything needed to:
- ✅ Test strategies individually
- ✅ Generate comprehensive metrics
- ✅ Optimize parameters systematically
- ✅ Compare strategies objectively
- ✅ Get AI-readable output for analysis
- ✅ Make data-driven trading decisions

Start with simple tests, analyze results, optimize parameters, and build a robust multi-strategy trading system backed by solid data.
