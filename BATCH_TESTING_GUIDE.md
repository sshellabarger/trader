# Batch Strategy Testing & Optimization Guide

This guide explains how to run comprehensive batch tests for all trading strategies and optimize their settings based on the results.

## Overview

The batch testing system runs all strategies concurrently with live market data, collects detailed performance metrics, and provides recommendations for optimizing each strategy's parameters.

## Quick Start

### 1. Run Batch Tests (All Strategies in Parallel)

```bash
# Test all 7 standard strategies for 60 minutes each (default)
python batch_test_strategies.py

# Test for a custom duration (90 minutes)
python batch_test_strategies.py --duration 90

# Test with specific symbols
python batch_test_strategies.py --symbols AAPL,MSFT,GOOGL,AMZN,TSLA,NVDA

# Test with symbols from file
python batch_test_strategies.py --symbols-file examples/symbols.csv

# Include crypto, forex, and ETF strategies (total 10 strategies)
python batch_test_strategies.py --include-all-asset-classes
```

### 2. Analyze Results & Get Recommendations

```bash
# Analyze results and show recommendations (dry run)
python analyze_and_optimize_settings.py test_results/

# Apply optimized settings to generate new config file
python analyze_and_optimize_settings.py test_results/ --apply

# Analyze a specific strategy with detailed metrics
python analyze_and_optimize_settings.py test_results/ --strategy momentum --detailed
```

### 3. Review and Apply Changes

After running the analysis with `--apply`, review the generated `strategy_configs_optimized.py` and copy it over the original if the changes look good:

```bash
# Review the differences
diff src/trading_bot/strategy_configs.py src/trading_bot/strategy_configs_optimized.py

# Apply the optimized settings
cp src/trading_bot/strategy_configs_optimized.py src/trading_bot/strategy_configs.py
```

## Detailed Usage

### Batch Testing Options

#### Strategy Selection

```bash
# Test specific strategies only
python batch_test_strategies.py --strategies momentum,news,volume

# Test all standard strategies (default: 7 strategies)
# - momentum, mean_reversion, news, volume, earnings, longterm_trend, longterm_momentum
python batch_test_strategies.py

# Test all strategies including crypto, forex, and ETF (10 total)
python batch_test_strategies.py --include-all-asset-classes
```

#### Symbol Selection

```bash
# Use specific symbols
python batch_test_strategies.py --symbols AAPL,MSFT,GOOGL,TSLA,NVDA,META

# Load symbols from CSV file
python batch_test_strategies.py --symbols-file my_watchlist.csv

# Default: Uses a diverse set of 16 stocks across multiple sectors
```

#### Test Parameters

```bash
# Set test duration (in minutes)
python batch_test_strategies.py --duration 120

# Set starting capital
python batch_test_strategies.py --capital 250000

# Override strategy parameters for all strategies
python batch_test_strategies.py --entry-threshold 0.65 --stop-loss 1.0

# Set output directory
python batch_test_strategies.py --output-dir my_test_results
```

#### Complete Example

```bash
# Comprehensive test: all strategies, 2 hours each, with custom symbols
python batch_test_strategies.py \
  --include-all-asset-classes \
  --duration 120 \
  --symbols-file examples/symbols.csv \
  --capital 100000 \
  --output-dir test_results_$(date +%Y%m%d)
```

### Analysis & Optimization Options

```bash
# Basic analysis (shows recommendations but doesn't apply)
python analyze_and_optimize_settings.py test_results/

# Apply optimized settings (generates new config file)
python analyze_and_optimize_settings.py test_results/ --apply

# Analyze specific strategy with detailed breakdown
python analyze_and_optimize_settings.py test_results/ --strategy momentum --detailed

# Custom output file
python analyze_and_optimize_settings.py test_results/ --apply --output my_configs.py
```

## Understanding the Output

### Batch Test Output

The batch test generates multiple files in the output directory:

```
test_results/
├── momentum_test_20240315_143022.json          # Metrics for each strategy
├── momentum_trades_20240315_143022.json        # Individual trades
├── momentum_signals_20240315_143022.json       # All signals generated
├── mean_reversion_test_20240315_143022.json
├── news_test_20240315_143022.json
├── ... (one set per strategy)
└── batch_comparison_20240315_150022.json       # Comparison across all strategies
```

### Key Metrics Explained

- **Win Rate**: Percentage of profitable trades
- **Profit Factor**: Gross profits / Gross losses (>1.5 is good, >2.0 is excellent)
- **Sharpe Ratio**: Risk-adjusted returns (>1.0 is good, >1.5 is excellent)
- **Max Drawdown**: Largest peak-to-trough decline
- **Avg Win/Loss**: Average profit vs average loss per trade
- **Exit Reasons**: Why trades were closed (stop loss, take profit, time)

### Analysis Output

The analysis script provides:

1. **Performance Score**: Composite score (0-1) based on multiple metrics
2. **Current Performance**: Summary of key metrics
3. **Recommendations**: Specific suggestions for improvement
4. **Suggested Settings**: Proposed parameter changes

Example recommendations:

```
✅ High win rate (68.2%) - Consider lowering entry_threshold to capture more opportunities
⚠️  High stop loss rate (65.0%) - Consider widening stop loss or raising entry threshold
✅ Excellent Sharpe ratio (1.85) - Strong risk-adjusted returns
```

## Strategy-by-Strategy Testing

If you want more control, you can test individual strategies using the original script:

```bash
# Test single strategy with default optimal parameters
python test_strategy.py test momentum

# Test with custom parameters
python test_strategy.py test momentum --duration 90 --entry-threshold 0.70

# Optimize a single strategy (parameter sweep)
python test_strategy.py optimize momentum --duration 30
```

## Workflow for Complete Optimization

### Full 3-Step Process

```bash
# Step 1: Run comprehensive batch test (2 hours per strategy)
python batch_test_strategies.py \
  --duration 120 \
  --symbols-file examples/symbols.csv \
  --include-all-asset-classes

# Step 2: Analyze results and generate optimized settings
python analyze_and_optimize_settings.py test_results/ --apply

# Step 3: Review and apply changes
diff src/trading_bot/strategy_configs.py src/trading_bot/strategy_configs_optimized.py
cp src/trading_bot/strategy_configs_optimized.py src/trading_bot/strategy_configs.py
```

## Best Practices

### Testing Duration

- **Quick Test**: 30-60 minutes (good for rapid iteration)
- **Standard Test**: 60-90 minutes (default, balanced)
- **Comprehensive Test**: 120-180 minutes (more reliable data)

### Symbol Selection

- **Minimum**: 10-15 symbols for reliable results
- **Recommended**: 20-30 diverse symbols across sectors
- **Include different market caps and volatility levels**

### When to Re-test

- After significant market regime changes
- Monthly or quarterly for live strategies
- After adjusting any strategy parameters
- When performance degrades in live trading

### Interpreting Results

✅ **Trust the recommendations when:**
- Total trades > 10 (more data points)
- Test duration > 60 minutes
- Multiple strategies show consistent patterns

⚠️ **Be cautious when:**
- Total trades < 5 (insufficient data)
- High volatility during test period
- Single strategy shows anomalous results

## Advanced: Parameter Optimization

For deep optimization of a single strategy, use the parameter sweep:

```bash
# Optimize momentum strategy (tests multiple parameter combinations)
python test_strategy.py optimize momentum --duration 30

# Quick optimization (reduced parameter space)
python test_strategy.py optimize momentum --duration 20 --quick

# Show top 10 parameter combinations
python test_strategy.py optimize momentum --duration 30 --top-n 10
```

This tests multiple combinations of:
- Entry threshold: [0.4, 0.5, 0.6, 0.7]
- Stop loss: [0.3, 0.5, 0.7, 1.0]
- Take profit: [1.5, 2.0, 2.5, 3.0]
- Max hold time: [120, 180, 240, 360]

Total combinations: 4 × 4 × 4 × 4 = 256 tests

## Troubleshooting

### No trades generated

- Lower the entry threshold
- Increase test duration
- Use more volatile symbols
- Check market hours (strategies only trade during market hours)

### High stop loss rate

- Widen stop loss percentage
- Raise entry threshold (be more selective)
- Check if market was particularly volatile during test

### Inconsistent results

- Increase test duration
- Test during normal market conditions
- Use more diverse symbol set
- Run multiple tests and average results

## Integration with Live Trading

After optimizing settings:

1. Update `src/trading_bot/strategy_configs.py` with new parameters
2. Test in paper trading mode first
3. Monitor performance for 1-2 weeks
4. Gradually increase position sizes if performing well
5. Re-optimize monthly or quarterly

## Files Generated

| File | Description |
|------|-------------|
| `{strategy}_test_*.json` | Comprehensive metrics for strategy |
| `{strategy}_trades_*.json` | All trades executed |
| `{strategy}_signals_*.json` | All signals generated |
| `batch_comparison_*.json` | Cross-strategy comparison |
| `strategy_configs_optimized.py` | Updated config with optimal parameters |
| `strategy_configs_backup_*.py` | Backup of original config |

## Example: Complete Optimization Session

```bash
# 1. Create test directory
mkdir -p test_results_$(date +%Y%m%d)

# 2. Run comprehensive batch test
python batch_test_strategies.py \
  --duration 120 \
  --symbols-file examples/symbols.csv \
  --include-all-asset-classes \
  --output-dir test_results_$(date +%Y%m%d)

# 3. Wait for tests to complete (120 minutes)
# All strategies run in parallel, so total time = 120 minutes

# 4. Analyze results with detailed output
python analyze_and_optimize_settings.py test_results_$(date +%Y%m%d)/ --detailed

# 5. Review recommendations for each strategy
# Read through the analysis output

# 6. Apply optimizations
python analyze_and_optimize_settings.py test_results_$(date +%Y%m%d)/ --apply

# 7. Review changes
diff src/trading_bot/strategy_configs.py src/trading_bot/strategy_configs_optimized.py

# 8. Apply changes if satisfied
cp src/trading_bot/strategy_configs_optimized.py src/trading_bot/strategy_configs.py

# 9. Commit changes
git add src/trading_bot/strategy_configs.py
git commit -m "Optimize strategy parameters based on batch test results"
git push
```

## Notes

- **Concurrent Execution**: All strategies run simultaneously, so a 120-minute test takes 120 minutes total (not 120 × 7 = 840 minutes)
- **Live Data**: Tests use real-time market data via Alpaca API
- **Paper Trading**: All tests are paper trading (no real money at risk)
- **Market Hours**: Tests run during market hours; adjust duration accordingly
- **Rate Limits**: Be mindful of API rate limits with large symbol lists

## Next Steps

1. Run your first batch test with default settings
2. Review the comparison report to see which strategies perform best
3. Analyze individual strategies for optimization opportunities
4. Apply recommended changes and re-test
5. Integrate optimized settings into live trading

For more details on individual strategies, see:
- `STRATEGY_TESTING.md` - Strategy testing framework
- `CRYPTO_STRATEGY.md` - Crypto-specific indicators
- `FOREX_ETF_STRATEGIES.md` - Forex and ETF strategies
