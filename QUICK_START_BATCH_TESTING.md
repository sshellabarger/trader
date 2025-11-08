# Quick Start: Batch Testing & Optimization

## What This Does

Run all trading strategies simultaneously with live market data and automatically optimize their settings based on performance.

## 3-Step Process

### Step 1: Setup (One Time)

Set up your Alpaca API keys:

```bash
# Add to ~/.bashrc or ~/.zshrc
export APCA_API_KEY_ID="your-api-key-id"
export APCA_API_SECRET_KEY="your-secret-key"

# Reload shell
source ~/.bashrc
```

Get free API keys at: https://alpaca.markets

### Step 2: Run Batch Test

```bash
# Test all 7 strategies for 60 minutes (during market hours: 9:30 AM - 4:00 PM ET)
python batch_test_strategies.py --duration 60
```

**Note**: All strategies run in parallel, so 60 minutes = total time (not per strategy)

### Step 3: Analyze & Optimize

```bash
# View recommendations
python analyze_and_optimize_settings.py test_results/

# Apply optimized settings
python analyze_and_optimize_settings.py test_results/ --apply

# Review changes
diff src/trading_bot/strategy_configs.py src/trading_bot/strategy_configs_optimized.py

# Apply if satisfied
cp src/trading_bot/strategy_configs_optimized.py src/trading_bot/strategy_configs.py
```

## What You Get

### Strategies Tested (7 standard)

1. **Momentum** - Intraday momentum, gaps, range position
2. **Mean Reversion** - Oversold conditions, bounce plays
3. **News** - News sentiment and volume
4. **Volume** - Above-average volume breakouts
5. **Earnings** - Pre-earnings announcement plays
6. **Longterm Trend** - Sustained uptrend/downtrend following
7. **Longterm Momentum** - Sustained directional movement

**Optional**: Add `--include-all-asset-classes` for Crypto, Forex, and ETF strategies (10 total)

### Performance Metrics

- Win rate, profit factor, Sharpe ratio
- Max drawdown, average win/loss
- Exit reason breakdown (stop loss, take profit, time)
- Regime performance (trending, ranging, volatile)

### Recommendations

- Adjust entry/exit thresholds
- Optimize stop loss and take profit levels
- Fine-tune position sizing
- Adjust hold times

## Examples

### Basic Test

```bash
python batch_test_strategies.py --duration 60
```

### Custom Symbols

```bash
python batch_test_strategies.py --duration 60 --symbols AAPL,MSFT,GOOGL,TSLA,NVDA
```

### Comprehensive Test (All Strategies)

```bash
python batch_test_strategies.py --duration 120 --include-all-asset-classes
```

### With Custom Parameters

```bash
python batch_test_strategies.py --duration 90 --entry-threshold 0.6 --stop-loss 1.0
```

## Output

Results saved to `test_results/`:

```
test_results/
├── momentum_test_*.json        # Metrics for each strategy
├── momentum_trades_*.json      # Individual trades
├── momentum_signals_*.json     # All signals
├── ... (one set per strategy)
└── batch_comparison_*.json     # Cross-strategy comparison
```

## Tips

✅ **Do:**
- Run during market hours (9:30 AM - 4:00 PM ET)
- Use at least 60 minutes for reliable results
- Test with 10-20 diverse symbols
- Review recommendations before applying

⚠️ **Don't:**
- Run with insufficient test time (<30 min)
- Test with too few symbols (<5)
- Apply settings blindly (review first)
- Test during extreme market events

## Timing

- **Setup**: 5 minutes (one time)
- **Test run**: 60-120 minutes
- **Analysis**: 5 minutes
- **Review & apply**: 10 minutes

**Total**: ~1.5-2.5 hours for complete optimization

## Full Documentation

- `BATCH_TESTING_GUIDE.md` - Comprehensive guide
- `SETUP_TESTING.md` - Detailed setup instructions
- `STRATEGY_TESTING.md` - Strategy testing framework

## Need Help?

Check if API is working:

```bash
python -c "
import sys
sys.path.insert(0, 'src')
from trading_bot.broker_alpaca import AlpacaBroker
broker = AlpacaBroker()
print('✅ Connected!' if broker.get_account() else '❌ Check API keys')
"
```

If issues:
1. Verify API keys are set
2. Check market is open
3. Review `SETUP_TESTING.md`
