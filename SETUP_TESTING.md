# Setup Guide for Batch Testing

## Prerequisites

To run batch tests with live market data, you need:

1. **Alpaca API Credentials** (free paper trading account)
2. **Python dependencies** (should already be installed)

## Quick Setup

### 1. Get Alpaca API Keys (Free)

1. Sign up for a free Alpaca paper trading account: https://alpaca.markets
2. Go to your dashboard and generate API keys
3. Copy your API Key ID and Secret Key

### 2. Configure API Keys

You can set the API keys in multiple ways:

#### Option A: Environment Variables (Recommended)

```bash
# Add to your ~/.bashrc or ~/.zshrc
export APCA_API_KEY_ID="your-api-key-id"
export APCA_API_SECRET_KEY="your-secret-key"

# Then reload your shell
source ~/.bashrc
```

#### Option B: .env File

Create a `.env` file in the project root:

```bash
# .env
APCA_API_KEY_ID=your-api-key-id
APCA_API_SECRET_KEY=your-secret-key
```

#### Option C: Direct in Code (Not Recommended)

Edit `src/trading_bot/broker_alpaca.py` to hardcode keys (not recommended for security).

### 3. Verify Setup

```bash
# Test API connection
python -c "
import sys
sys.path.insert(0, 'src')
from trading_bot.broker_alpaca import AlpacaBroker

broker = AlpacaBroker()
account = broker.get_account()

if account:
    print('✅ API connection successful!')
    print(f'Account status: {account.get(\"status\")}')
    print(f'Buying power: \${account.get(\"buying_power\")}')
else:
    print('❌ API connection failed')
"
```

## Running Batch Tests

Once API keys are configured:

### Quick Test (30 minutes, 7 strategies)

```bash
python batch_test_strategies.py --duration 30
```

### Standard Test (60 minutes, 7 strategies)

```bash
python batch_test_strategies.py --duration 60
```

### Comprehensive Test (120 minutes, all 10 strategies)

```bash
python batch_test_strategies.py --duration 120 --include-all-asset-classes
```

## Market Hours

**Important**: Tests run during market hours only!

- **US Market Hours**: 9:30 AM - 4:00 PM ET (Monday-Friday)
- **Crypto**: 24/7 (if testing crypto strategies)
- **Forex**: 24/5 (Sunday evening - Friday evening)

If you start a test when the market is closed, the strategies will wait for market open to begin collecting data.

## What to Expect

### During Testing

- The script runs all strategies in parallel (concurrent execution)
- Each strategy monitors market data independently
- Progress logs appear in real-time
- Test duration = time specified (not per-strategy)

Example: `--duration 60` means **60 minutes total** for all strategies running in parallel.

### After Testing

Results are saved to `test_results/` directory:

```
test_results/
├── momentum_test_20240315_143022.json          # Metrics
├── momentum_trades_20240315_143022.json        # Trades
├── momentum_signals_20240315_143022.json       # Signals
├── ... (files for each strategy)
└── batch_comparison_20240315_150022.json       # Comparison
```

### Analyze Results

```bash
# View analysis and recommendations
python analyze_and_optimize_settings.py test_results/

# Apply optimized settings
python analyze_and_optimize_settings.py test_results/ --apply
```

## Troubleshooting

### "Access denied" or "403 Forbidden"

- Check that API keys are set correctly
- Verify keys are for paper trading (not live trading)
- Ensure keys haven't expired
- Try regenerating keys in Alpaca dashboard

### "Market is closed"

- Check current time vs market hours
- Tests will pause until market opens
- Consider using `--duration` that fits within market hours

### "No trades generated"

- Lower entry threshold: `--entry-threshold 0.4`
- Increase duration: `--duration 90`
- Market might be low volatility
- Try more volatile symbols

### Rate Limits

Alpaca free tier limits:
- 200 requests per minute
- The scripts are optimized to stay within limits
- If you hit limits, they reset after 1 minute

## Example: Complete Workflow

```bash
# 1. Verify API setup
python -c "import sys; sys.path.insert(0, 'src'); from trading_bot.broker_alpaca import AlpacaBroker; print('✅ Connected!' if AlpacaBroker().get_account() else '❌ Failed')"

# 2. Run batch test (during market hours)
python batch_test_strategies.py --duration 60 --symbols AAPL,MSFT,GOOGL,AMZN,TSLA,NVDA,META,NFLX

# 3. Wait for completion (60 minutes)

# 4. Analyze results
python analyze_and_optimize_settings.py test_results/ --detailed

# 5. Apply optimizations
python analyze_and_optimize_settings.py test_results/ --apply

# 6. Review and commit
diff src/trading_bot/strategy_configs.py src/trading_bot/strategy_configs_optimized.py
cp src/trading_bot/strategy_configs_optimized.py src/trading_bot/strategy_configs.py
git add src/trading_bot/strategy_configs.py
git commit -m "Optimize strategy settings based on batch testing"
git push
```

## Testing Without API Keys (Development)

If you want to test the scripts without API keys:

1. The scripts will show helpful error messages
2. You can review the code and configuration
3. Set up API keys when ready for live testing

## Support

- **Alpaca Docs**: https://alpaca.markets/docs/
- **Alpaca Support**: support@alpaca.markets
- **Project Issues**: Check the repository's issue tracker

## Security Notes

- Never commit API keys to git
- Use environment variables or `.env` file
- Keep `.env` in `.gitignore`
- Regenerate keys if accidentally exposed
- Paper trading keys are separate from live trading

## Next Steps

1. ✅ Set up Alpaca API keys
2. ✅ Verify connection
3. ✅ Run your first batch test
4. ✅ Analyze results
5. ✅ Apply optimizations
6. ✅ Monitor live trading performance
