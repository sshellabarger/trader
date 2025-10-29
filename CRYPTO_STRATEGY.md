# Cryptocurrency Trading Strategy

## Overview

The crypto trading strategy is designed for 24/7 cryptocurrency markets with advanced technical analysis and risk management tailored to crypto's high volatility.

## Features

### 1. Expanded Crypto Universe

The strategy now supports 10 major cryptocurrencies:

- **BTC/USD** - Bitcoin (largest market cap)
- **ETH/USD** - Ethereum (smart contracts leader)
- **SOL/USD** - Solana (high-performance blockchain)
- **AVAX/USD** - Avalanche (DeFi platform)
- **MATIC/USD** - Polygon (Ethereum scaling)
- **LINK/USD** - Chainlink (oracle network)
- **UNI/USD** - Uniswap (DEX leader)
- **AAVE/USD** - Aave (lending protocol)
- **DOT/USD** - Polkadot (multi-chain)
- **DOGE/USD** - Dogecoin (high volume meme coin)

### 2. Advanced Technical Indicators

The strategy uses multiple technical indicators for comprehensive market analysis:

#### RSI (Relative Strength Index)
- **Period**: 14 (configurable)
- **Signals**:
  - RSI < 30 = Oversold (potential buy)
  - RSI > 70 = Overbought (potential sell)
  - RSI 30-70 = Neutral

#### MACD (Moving Average Convergence Divergence)
- **Fast Period**: 12
- **Slow Period**: 26
- **Signal Period**: 9
- **Signals**:
  - Bullish: Histogram > 0 and MACD > 0
  - Bearish: Histogram < 0 and MACD < 0
  - Neutral: Mixed signals

#### Bollinger Bands
- **Period**: 20
- **Standard Deviations**: 2
- **Signals**:
  - Price near lower band = Oversold
  - Price near upper band = Overbought
  - Position calculated as: `(price - lower) / (upper - lower)`

#### Volume Analysis
- **Period**: 20-day moving average
- **Signals**:
  - Volume > 2x average = High volume (strong signal)
  - Volume < 0.5x average = Low volume (weak signal)
  - 0.5x-2x = Normal volume

#### Momentum Score
- Combines short-term (5 periods) and long-term (20 periods) price movement
- Normalized to 0-1 range
- Higher score = stronger upward momentum

#### Volatility Score
- Coefficient of variation (std dev / mean)
- Normalized to 0-1 range
- Higher score = more volatile market

### 3. Multi-Factor Scoring System

The crypto strategy combines multiple factors:

**Basic Scoring (40% weight):**
- Trend direction (up vs down)
- Price volatility (range as % of price)
- Intraday movement
- Position in daily range

**Advanced Indicators (60% weight):**
- RSI signal (oversold/neutral/overbought)
- MACD trend (bullish/neutral/bearish)
- Bollinger Band position
- Volume confirmation
- Momentum score

**Overall Score Calculation:**
```python
basic_score = trend_based_score()  # 0-1
indicator_score = average_of_all_indicators()  # 0-1
final_score = (basic_score * 0.4) + (indicator_score * 0.6)
```

### 4. Risk Management

Crypto-specific risk parameters:

- **Stop Loss**: 2.5% (wider than stocks due to volatility)
- **Take Profit**: 6.0% (crypto can move 5-10% easily)
- **Position Size**: 1.2% of portfolio (conservative)
- **Max Hold Time**: 8 hours (480 minutes)
- **Trailing Stop**:
  - Activates at 3% profit
  - Trails by 1.5%

### 5. Entry Filters

Additional filters for crypto entries:

- **Entry Threshold**: 0.60 (higher than stocks)
- **Exit Threshold**: 0.40
- **Min RSI**: 30 (don't buy extreme oversold)
- **Max RSI**: 80 (don't buy extreme overbought)
- **Min Volume Ratio**: 0.8 (require 80% of average volume)
- **Volume Confirmation**: Required for entries

## Configuration

### Enable Crypto Trading

In `src/trading_bot/settings.py`:

```python
crypto = {
    "enabled": True,  # Set to True to enable
    "universe": [...],  # List of crypto pairs
}

strategies = {
    "crypto": True,  # Enable crypto strategy
}
```

### Adjust Parameters

In `src/trading_bot/settings.py`:

```python
crypto = {
    "rsi_period": 14,           # RSI calculation period
    "bb_period": 20,            # Bollinger Bands period
    "bb_std": 2,                # Bollinger Bands std dev
    "volume_ma_period": 20,     # Volume moving average
    "macd_fast": 12,            # MACD fast EMA
    "macd_slow": 26,            # MACD slow EMA
    "macd_signal": 9,           # MACD signal line
    "min_volume_24h": 1000000,  # Min 24h volume filter
}
```

### Strategy-Specific Config

In `src/trading_bot/strategy_configs.py`:

```python
"crypto": {
    'entry_threshold': 0.60,
    'exit_threshold': 0.40,
    'stop_loss_pct': 2.5,
    'take_profit_pct': 6.0,
    'max_hold_minutes': 480,
    'position_size_pct': 1.2,
    'use_advanced_indicators': True,
    'min_rsi': 30,
    'max_rsi': 80,
    'require_volume_confirmation': True,
    'min_volume_ratio': 0.8,
}
```

## Usage

### Basic Usage

```python
from src.trading_bot.strategies import score_crypto

# Simple scoring (basic mode)
score, details = score_crypto(
    symbol="BTC/USD",
    current_price=45000,
    open_price=44000,
    prev_close=44500,
    high=45500,
    low=43800
)

print(f"Score: {score}")
print(f"Details: {details}")
```

### Advanced Usage with Historical Data

```python
# With historical data for advanced indicators
historical_prices = [44000, 44200, 44100, ...]  # Last 30+ prices
historical_volumes = [1000000, 1200000, ...]     # Corresponding volumes

score, details = score_crypto(
    symbol="BTC/USD",
    current_price=45000,
    open_price=44000,
    prev_close=44500,
    high=45500,
    low=43800,
    historical_prices=historical_prices,
    historical_volumes=historical_volumes,
    use_advanced_indicators=True
)

# Access detailed indicator values
print(f"RSI: {details['rsi']} ({details['rsi_signal']})")
print(f"MACD Trend: {details['macd_trend']}")
print(f"BB Position: {details['bb_position']}")
print(f"Volume Ratio: {details['volume_ratio']}x")
print(f"Momentum: {details['momentum_score']}")
print(f"Final Score: {score}")
```

### Direct Indicator Analysis

```python
from src.trading_bot.crypto_indicators import analyze_crypto

# Analyze with all indicators
indicators = analyze_crypto(
    prices=historical_prices,
    volumes=historical_volumes
)

print(f"RSI: {indicators.rsi} - {indicators.rsi_signal}")
print(f"MACD: {indicators.macd_trend}")
print(f"BB Position: {indicators.bb_position}")
print(f"Overall Score: {indicators.overall_score}")
print(f"Confidence: {indicators.confidence}")
```

## Signal Interpretation

### Strong Buy Signals (Score > 0.70)

- RSI oversold (< 30)
- MACD bullish crossover
- Price near lower Bollinger Band
- High volume confirmation
- Strong upward momentum

### Moderate Buy (Score 0.55-0.70)

- RSI neutral (30-50)
- Positive price momentum
- Normal to high volume
- Upward trend

### Neutral (Score 0.40-0.55)

- Mixed indicators
- No clear trend
- Normal volume
- RSI neutral

### Weak/Sell (Score < 0.40)

- RSI overbought (> 70)
- MACD bearish
- Price at upper Bollinger Band
- Low volume
- Negative momentum

## Examples

### Example 1: Strong Buy Signal

```
BTC/USD: $44,000
- RSI: 28 (oversold)
- MACD: Bullish crossover
- BB Position: 0.15 (near lower band)
- Volume: 2.5x average
- Momentum: 0.75

Final Score: 0.78 → STRONG BUY
```

### Example 2: Overbought

```
ETH/USD: $3,200
- RSI: 82 (overbought)
- MACD: Bearish divergence
- BB Position: 0.92 (near upper band)
- Volume: 0.6x average
- Momentum: 0.35

Final Score: 0.28 → AVOID/SELL
```

### Example 3: Trending Up

```
SOL/USD: $105
- RSI: 58 (neutral)
- MACD: Bullish
- BB Position: 0.65 (above middle)
- Volume: 1.2x average
- Momentum: 0.68

Final Score: 0.65 → MODERATE BUY
```

## Testing

Test the crypto strategy:

```bash
# Test crypto strategy for 3 hours
python test_strategy.py test crypto --duration 180

# Compare crypto vs other strategies
python test_strategy.py test-all --duration 120

# Optimize crypto parameters
python test_strategy.py optimize crypto --duration 90
```

## Performance Considerations

### Advantages
- 24/7 trading (no market hours restriction)
- High volatility = larger profit potential
- Technical indicators work well in crypto
- Multiple timeframe analysis

### Risks
- Higher volatility = higher risk
- Requires wider stops (2.5% vs 0.8%)
- Market can be more emotional/sentiment-driven
- Flash crashes more common

### Best Practices
1. **Start small**: Use 1-2% position sizes
2. **Use stops**: Always set stop losses
3. **Monitor frequently**: Crypto moves 24/7
4. **Diversify**: Don't put all capital in one coin
5. **Volume confirmation**: Require high volume for entries
6. **Trailing stops**: Lock in profits as price moves up

## Backtesting Results

(To be populated with actual backtest data)

Expected metrics:
- Win rate: 45-55%
- Profit factor: 1.5-2.5
- Average gain: 3-8%
- Average loss: 1-3%
- Max drawdown: 10-20%

## Troubleshooting

### No signals generated
- Check if crypto is enabled in settings
- Verify crypto symbols are in universe
- Ensure historical data is available (20+ bars)

### Poor performance
- Adjust entry/exit thresholds
- Tighten RSI filters
- Require higher volume confirmation
- Reduce position sizes

### Too many trades
- Increase entry threshold
- Add cooldown period between trades
- Require multiple indicator confirmation

## Advanced Topics

### Custom Crypto Pairs

Add custom pairs to universe:

```python
crypto = {
    "universe": [
        "BTC/USD",
        "MY_TOKEN/USD",  # Your custom pair
    ]
}
```

### Custom Indicators

Extend the crypto_indicators.py module:

```python
def my_custom_indicator(prices):
    # Your logic here
    return score
```

### Market Regime Detection

Combine with market regime:

```python
from src.trading_bot.strategy_manager import RegimeDetector

regime = RegimeDetector.detect_regime(prices)
if regime == "trending_up" and crypto_score > 0.7:
    # Strong buy in uptrend
    pass
```

## References

- RSI: https://www.investopedia.com/terms/r/rsi.asp
- MACD: https://www.investopedia.com/terms/m/macd.asp
- Bollinger Bands: https://www.investopedia.com/terms/b/bollingerbands.asp
- Crypto Trading Guide: https://www.coindesk.com/learn/

## Support

For issues or questions:
1. Check logs for error messages
2. Verify configuration settings
3. Test with simulation mode first
4. Review STRATEGY_TESTING.md for debugging

---

**Last Updated**: 2025-10-29
**Version**: 2.0 (Expanded Crypto Strategy)
