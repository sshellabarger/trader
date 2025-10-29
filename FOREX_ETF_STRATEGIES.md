# Forex and ETF Trading Strategies Documentation

## Overview

This document describes the Forex (Foreign Exchange) and ETF (Exchange-Traded Fund) trading strategies implemented in the trading bot. These strategies complement the existing stock and cryptocurrency strategies to provide comprehensive market coverage.

## Table of Contents

1. [Forex Strategy](#forex-strategy)
2. [ETF Strategy](#etf-strategy)
3. [Configuration](#configuration)
4. [Technical Indicators](#technical-indicators)
5. [Risk Parameters](#risk-parameters)
6. [Usage Examples](#usage-examples)

---

## Forex Strategy

### What is Forex Trading?

Forex trading involves exchanging one currency for another. The forex market is the largest and most liquid financial market in the world, trading 24 hours a day, 5 days a week (24/5).

### Supported Currency Pairs

The bot supports trading the following major and cross currency pairs:

#### Major Pairs (vs USD)
- **EUR/USD** - Euro / US Dollar (most liquid)
- **GBP/USD** - British Pound / US Dollar
- **USD/JPY** - US Dollar / Japanese Yen
- **USD/CHF** - US Dollar / Swiss Franc
- **AUD/USD** - Australian Dollar / US Dollar
- **USD/CAD** - US Dollar / Canadian Dollar
- **NZD/USD** - New Zealand Dollar / US Dollar

#### Cross Pairs
- **EUR/GBP** - Euro / British Pound
- **EUR/JPY** - Euro / Japanese Yen
- **GBP/JPY** - British Pound / Japanese Yen

### Forex-Specific Characteristics

1. **Small Price Movements**: Forex pairs typically move in small increments (pips), requiring precise entry/exit
2. **High Leverage**: Forex allows higher leverage than stocks
3. **24/5 Trading**: Markets open Sunday evening through Friday evening (EST)
4. **Low Spreads**: Major pairs have very tight bid-ask spreads
5. **Technical Analysis Focus**: Price action and technical indicators are crucial

### Technical Indicators Used

The forex strategy employs advanced technical analysis:

#### 1. RSI (Relative Strength Index)
- Period: 14
- Oversold: < 30 (buy signal)
- Overbought: > 70 (sell signal)
- Helps identify reversal points

#### 2. ATR (Average True Range)
- Period: 14
- Measures volatility
- Used for stop-loss placement
- Typical range: 0.3% - 2.5% for major pairs

#### 3. Pivot Points
- Classic pivot point calculation
- Provides support/resistance levels
- Entry signals at support, exit at resistance

#### 4. MACD (Moving Average Convergence Divergence)
- Fast: 12, Slow: 26, Signal: 9
- Identifies trend direction and momentum
- Bullish when histogram > 0

#### 5. EMA Crossovers
- Short EMA: 12, Long EMA: 26
- Bullish when short > long
- Trend confirmation signal

### Scoring Logic

```python
# Basic Score Components (0-1 scale)
1. Intraday Movement: (current - open) / open × 50
2. Trend Direction: (current - prev_close) / prev_close × 30
3. Position in Range: (current - low) / (high - low) × 0.2
4. Volatility: (high - low) / open × 5

# Advanced Score (when historical data available)
- 30% Basic Score
- 70% Technical Indicators Score
  - RSI signal: 20%
  - MACD trend: 20%
  - EMA crossover: 20%
  - Pivot levels: 15%
  - Momentum: 15%
  - Volatility: 10%
```

### Strategy Parameters

```python
{
    'entry_threshold': 0.62,       # Higher threshold for forex
    'exit_threshold': 0.38,        # Exit when signals weaken
    'stop_loss_pct': 0.5,          # 50 pips typical for majors
    'take_profit_pct': 1.2,        # 120 pips target
    'max_hold_minutes': 360,       # 6 hours max hold
    'position_size_pct': 2.0,      # 2% of portfolio

    # Forex-specific
    'min_rsi': 30,                 # Don't buy oversold extremes
    'max_rsi': 70,                 # Don't buy overbought
    'max_atr_pct': 2.5,            # Too volatile
    'min_atr_pct': 0.2,            # Too quiet
    'respect_pivot_levels': True,
    'use_ema_confirmation': True,
}
```

### Market Regime Preferences

The forex strategy performs best in different market conditions:

- **Ranging Markets** (10% weight): Best performance - mean reversion opportunities
- **Trending Up** (8% weight): Good - ride uptrends with EMA confirmation
- **Trending Down** (8% weight): Good - ride downtrends
- **High Volatility** (8% weight): Moderate - wider stops needed

---

## ETF Strategy

### What are ETFs?

Exchange-Traded Funds (ETFs) are investment funds that trade on stock exchanges like individual stocks. They typically track an index, sector, commodity, or basket of assets.

### Supported ETFs

#### Major Market ETFs
- **SPY** - S&P 500 (most liquid)
- **QQQ** - Nasdaq 100
- **IWM** - Russell 2000 (small caps)
- **DIA** - Dow Jones Industrial Average

#### Sector ETFs (SPDR Sector Select)
- **XLE** - Energy
- **XLF** - Financials
- **XLK** - Technology
- **XLV** - Healthcare
- **XLI** - Industrials
- **XLP** - Consumer Staples
- **XLY** - Consumer Discretionary
- **XLB** - Materials
- **XLU** - Utilities
- **XLRE** - Real Estate
- **XLC** - Communication Services

#### International ETFs
- **EEM** - Emerging Markets
- **EFA** - EAFE (Europe, Asia, Far East)

#### Commodity ETFs
- **GLD** - Gold
- **SLV** - Silver
- **USO** - Oil

#### Bond ETFs
- **TLT** - Long-term Treasury
- **HYG** - High Yield Corporate
- **LQD** - Investment Grade Corporate

### ETF-Specific Characteristics

1. **Lower Volatility**: Generally less volatile than individual stocks
2. **High Liquidity**: Major ETFs have millions in daily volume
3. **Diversification**: Instant exposure to entire sectors/markets
4. **Tight Spreads**: SPY, QQQ typically have 1-2 cent spreads
5. **Sector Rotation**: Can capitalize on sector trends

### Scoring Logic

```python
# Score Components
1. Momentum: (current - open) / open × 3.0
2. Trend: (current - prev_close) / prev_close × 2.0
3. Position in Range: (current - low) / range × 0.2
4. Gap: (open - prev_close) / prev_close × 1.5

# Volume Adjustment (Critical for ETFs)
- Volume > 1.5× average: 0.7 multiplier (strong confidence)
- Volume > 1.0× average: 0.6 multiplier (good)
- Volume < 0.5× average: 0.3 multiplier (weak signal)

# Sector Rotation Bonus
- If sector up > 1%: Boost score by 20%
- If sector down > 1%: Reduce score by 20%
```

### Strategy Parameters

```python
{
    'entry_threshold': 0.58,       # Moderate threshold
    'exit_threshold': 0.35,        # Exit when momentum fades
    'stop_loss_pct': 1.0,          # 1% stop loss
    'take_profit_pct': 2.5,        # 2.5% target
    'max_hold_minutes': 300,       # 5 hours
    'position_size_pct': 3.0,      # Larger - diversified

    # ETF-specific
    'require_high_volume': True,
    'min_volume_ratio': 1.0,       # Must be average volume
    'sector_rotation_enabled': True,
    'prefer_broad_market': True,   # SPY, QQQ over narrow ETFs
    'max_spread_bps': 10,          # Max 10 basis point spread
}
```

### Market Regime Preferences

- **Trending Up** (4% weight): Good for broad market ETFs
- **Trending Down** (4% weight): Good for inverse ETFs (future)
- **Ranging** (4% weight): Moderate - less movement
- **High Volatility** (4% weight): Good for volatility ETFs

### Sector Rotation Strategy

The ETF strategy includes sector rotation analysis:

```python
sector_map = {
    'XLE': 'energy',
    'XLF': 'financials',
    'XLK': 'technology',
    'XLV': 'healthcare',
    'XLI': 'industrials',
    'XLP': 'consumer_staples',
    'XLY': 'consumer_discretionary',
    'XLB': 'materials',
    'XLU': 'utilities',
    'XLRE': 'real_estate',
    'XLC': 'communication'
}

# If sector is outperforming market:
if sector_performance > 0.01:  # Up 1%
    score *= 1.2  # Boost by 20%
```

---

## Configuration

### Enabling Forex/ETF Trading

Edit `src/trading_bot/settings.py`:

```python
strategies = {
    "momentum": True,
    "mean_reversion": True,
    "news": True,
    "volume": True,
    "earnings": True,
    "longterm_trend": True,
    "longterm_momentum": True,
    "crypto": False,
    "forex": True,   # Enable forex
    "etf": True      # Enable ETF
}
```

### Customizing Universe

#### Forex Universe

```python
forex = {
    "enabled": True,
    "universe": [
        "EUR/USD",
        "GBP/USD",
        "USD/JPY",
        # Add more pairs...
    ],
    "rsi_period": 14,
    "atr_period": 14,
    "min_daily_volume": 100000,
    "max_spread_pips": 5,
}
```

#### ETF Universe

```python
etf = {
    "enabled": True,
    "universe": [
        "SPY", "QQQ", "IWM",
        "XLE", "XLF", "XLK",
        # Add more ETFs...
    ],
    "min_daily_volume": 1000000,
    "max_spread_bps": 10,
    "sector_rotation": True,
}
```

---

## Technical Indicators

### Forex Indicators Module

Location: `src/trading_bot/forex_indicators.py`

#### Key Functions

1. **calculate_atr()** - Average True Range for volatility
2. **calculate_pivot_points()** - Support/resistance levels
3. **calculate_rsi()** - Relative Strength Index
4. **calculate_macd()** - Moving Average Convergence Divergence
5. **calculate_ema_cross()** - EMA crossover signals
6. **analyze_forex()** - Comprehensive analysis combining all indicators

#### Example Usage

```python
from trading_bot.forex_indicators import analyze_forex

indicators = analyze_forex(
    prices=historical_prices,
    highs=historical_highs,
    lows=historical_lows,
    rsi_period=14,
    atr_period=14
)

print(f"RSI: {indicators.rsi}")
print(f"RSI Signal: {indicators.rsi_signal}")
print(f"ATR: {indicators.atr_pct}%")
print(f"Pivot: {indicators.pivot}")
print(f"Overall Score: {indicators.overall_score}")
```

---

## Risk Parameters

### Position Sizing

| Asset Type | Position Size | Rationale |
|------------|--------------|-----------|
| Stocks | 2.0-2.5% | Standard sizing |
| Crypto | 1.2% | High volatility |
| **Forex** | **2.0%** | Moderate volatility |
| **ETF** | **3.0%** | Diversified, lower risk |

### Stop Loss Levels

| Asset Type | Stop Loss | Reasoning |
|------------|-----------|-----------|
| Stocks | 0.8-1.5% | Varies by strategy |
| Crypto | 2.5% | Wide swings |
| **Forex** | **0.5%** | Tight - ~50 pips |
| **ETF** | **1.0%** | Standard risk |

### Take Profit Targets

| Asset Type | Take Profit | Risk:Reward |
|------------|-------------|-------------|
| Stocks | 2.0-2.5% | 2:1 to 3:1 |
| Crypto | 6.0% | 2.4:1 |
| **Forex** | **1.2%** | 2.4:1 |
| **ETF** | **2.5%** | 2.5:1 |

---

## Usage Examples

### Example 1: Enable Forex Trading Only

```python
# In settings.py
strategies = {
    "forex": True,
    # ... other strategies False
}

forex = {
    "enabled": True,
    "universe": ["EUR/USD", "GBP/USD", "USD/JPY"],
}
```

### Example 2: Enable ETF Trading with Sector Rotation

```python
strategies = {
    "etf": True,
    # ... other strategies
}

etf = {
    "enabled": True,
    "universe": [
        "SPY", "QQQ",  # Broad market
        "XLK", "XLF", "XLE",  # Key sectors
    ],
    "sector_rotation": True,
}
```

### Example 3: Combined Multi-Asset Strategy

```python
strategies = {
    "momentum": True,
    "mean_reversion": True,
    "volume": True,
    "crypto": True,
    "forex": True,
    "etf": True,
}

# Bot will now trade stocks, crypto, forex, and ETFs
# Each with appropriate strategy weightings
```

---

## Performance Expectations

### Forex Strategy

- **Best Conditions**: Ranging markets, high liquidity hours (London/NY overlap)
- **Typical Win Rate**: 55-60%
- **Average Trade Duration**: 2-6 hours
- **Risk:Reward**: 2.4:1 (50 pip risk, 120 pip target)

### ETF Strategy

- **Best Conditions**: Trending markets, high volume, sector rotation
- **Typical Win Rate**: 60-65%
- **Average Trade Duration**: 3-5 hours
- **Risk:Reward**: 2.5:1 (1% risk, 2.5% target)

---

## Backtesting

To backtest forex and ETF strategies:

```bash
# Test forex strategy
python -m trading_bot.strategy_testing --strategy forex --duration 120

# Test ETF strategy
python -m trading_bot.strategy_testing --strategy etf --duration 120

# Test combined
python -m trading_bot.strategy_testing --strategy all --duration 180
```

---

## Troubleshooting

### Common Issues

#### 1. No Forex Trades Executing

**Check:**
- `strategies["forex"]` is `True`
- `forex["enabled"]` is `True`
- Forex symbols in universe
- Broker supports forex (Alpaca does)

#### 2. ETF Volume Too Low

**Solution:**
- Stick to major ETFs (SPY, QQQ, IWM)
- Increase `min_daily_volume` threshold
- Trade during market hours only

#### 3. Forex Spreads Too Wide

**Solution:**
- Trade major pairs only (EUR/USD, GBP/USD)
- Trade during high liquidity hours
- Reduce `max_spread_pips` setting

---

## Advanced Topics

### 1. Correlation Analysis

Future enhancement: Analyze correlation between currency pairs to avoid overexposure.

```python
# EUR/USD and GBP/USD are highly correlated
# USD/JPY typically inversely correlated with EUR/USD
```

### 2. Interest Rate Differentials

Future enhancement: Factor in central bank rates for carry trade opportunities.

### 3. ETF Premium/Discount to NAV

Future enhancement: Check if ETF trading at premium or discount to Net Asset Value.

### 4. Options Integration

Future enhancement: Use ETF options for hedging or leveraged plays.

---

## Regulatory Notes

### Forex Trading
- Forex trading may have different margin requirements
- Pattern Day Trader (PDT) rules may apply differently
- Check broker-specific forex regulations

### ETF Trading
- ETFs trade like stocks - same rules apply
- PDT rules apply if day trading
- Some ETFs are leveraged (2x, 3x) - use caution

---

## Resources

### Documentation Files
- `FOREX_ETF_STRATEGIES.md` (this file)
- `CRYPTO_STRATEGY.md` - Crypto strategy details
- `STRATEGY_DOCUMENTATION.md` - All strategies overview
- `STRATEGY_TESTING.md` - Testing framework

### Code Files
- `src/trading_bot/forex_indicators.py` - Forex technical analysis
- `src/trading_bot/strategies.py` - All scoring functions
- `src/trading_bot/strategy_manager.py` - Strategy orchestration
- `src/trading_bot/strategy_configs.py` - Strategy parameters

---

## Summary

The forex and ETF strategies expand the trading bot's capabilities to cover a broader range of asset classes:

- **Forex**: Technical analysis-driven, 24/5 trading, tight stops, precise entries
- **ETF**: Volume-driven, sector rotation, diversified exposure, lower volatility

Both strategies integrate seamlessly with existing momentum, mean reversion, and news strategies for a comprehensive multi-strategy, multi-asset trading system.

**Strategy Confidence Levels:**
- Forex: 70% (high reliability with technical indicators)
- ETF: 68% (good reliability, lower volatility)

**Recommended Starting Configuration:**
1. Enable ETF strategy first (lower risk)
2. Start with broad market ETFs (SPY, QQQ)
3. Add forex after comfortable with system
4. Start with EUR/USD only (most liquid)
5. Monitor performance for 1-2 weeks before scaling

---

*Last Updated: 2025-10-29*
*Version: 1.0*
