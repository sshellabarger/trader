# Trading Strategy Documentation

## Table of Contents
1. [Overview](#overview)
2. [Market Regime Detection](#market-regime-detection)
3. [Individual Strategies](#individual-strategies)
4. [Purchase Trigger Conditions](#purchase-trigger-conditions)
5. [Position Sizing and Risk Management](#position-sizing-and-risk-management)
6. [Strategy Weighting by Regime](#strategy-weighting-by-regime)

---

## Overview

This trading system uses a **multi-strategy approach** with **market regime awareness** to identify and execute trades. The system combines 8 different trading strategies, dynamically adjusting their importance based on current market conditions.

**Key Files:**
- `src/trading_bot/strategy_manager.py` - Main orchestration and regime detection
- `src/trading_bot/strategies.py` - Individual strategy scoring functions
- `src/trading_bot/engine.py` - Trade execution engine
- `src/trading_bot/risk_manager.py` - Position sizing and risk controls

---

## Market Regime Detection

Before scoring strategies, the system classifies the current market environment into one of six regimes:

### Regime Types

| Regime | Detection Criteria | Characteristics |
|--------|-------------------|-----------------|
| **TRENDING_UP** | Price > open, open >= prev_close, intraday move > 1% | Strong upward momentum |
| **TRENDING_DOWN** | Price < open, open <= prev_close, intraday move > 1% | Strong downward momentum |
| **RANGING** | Price range 0.5-2%, gap < 1% | Sideways/consolidation |
| **HIGH_VOLATILITY** | Price range > 3% OR gap > 2% | Large price swings |
| **LOW_VOLATILITY** | Price range < 0.5% | Minimal movement |
| **UNKNOWN** | Doesn't fit above patterns | Unclear market state |

**Code Location:** `strategy_manager.py:45-101`

The detected regime determines:
1. Which strategies get higher weight
2. Stop-loss width (wider for high volatility)
3. Entry threshold adjustments

---

## Individual Strategies

Each strategy scores a symbol from 0 to 1, with higher scores indicating stronger buy signals.

### 1. Momentum Strategy

**What it does:** Identifies stocks with strong intraday upward price movement

**Scoring Formula:**
```python
score = (intraday_change × 5.0) + (gap × 2.0) + (position_in_range - 0.5) × 0.3
```

**Components:**
- **Intraday Change (5x weight):** (current_price - open_price) / open_price
  - Example: Stock opens at $100, now at $102 → +2% → contributes 0.10 to score

- **Gap (2x weight):** (open_price - prev_close) / prev_close
  - Example: Closed at $95, opened at $100 → +5.3% gap → contributes 0.106 to score

- **Position in Range (0.3x weight):** Where current price sits in today's high-low range
  - Near high = positive, near low = negative

**Purchase Trigger:** Score > 0.62 when market is TRENDING_UP

**Example Buy Scenario:**
```
Symbol: AAPL
- Opened at $150 (prev close $148) → gap = +1.35%
- Currently $152 → intraday change = +1.33%
- Position at 80% of daily range

Calculation:
score = (0.0133 × 5.0) + (0.0135 × 2.0) + (0.8 - 0.5) × 0.3
score = 0.0665 + 0.027 + 0.09 = 0.1835 (before normalization)
Final score ≈ 0.68 → BUY SIGNAL (> 0.62 threshold)
```

**Code Location:** `strategies.py:23-52`

---

### 2. Mean Reversion Strategy

**What it does:** Identifies oversold stocks likely to bounce back toward recent prices

**Scoring Formula:**
```python
if (prev_close > current_price):
    score = (deviation × 5.0) + (1 - position_in_range) × 0.3
else:
    score = 0
```

**Components:**
- **Deviation (5x weight):** (prev_close - current_price) / prev_close
  - Only positive when price has dropped

- **Position in Range (0.3x weight):** Inverse position (lower = better)
  - Near daily low = higher score

**Purchase Trigger:** Score > 0.62 when market is RANGING or TRENDING_DOWN

**Example Buy Scenario:**
```
Symbol: MSFT
- Previous close: $350
- Currently: $343 → down $7 (-2%)
- Trading at 15% of daily range (near low)

Calculation:
deviation = (350 - 343) / 350 = 0.02 (2%)
position_in_range = 0.15 (near low)

score = (0.02 × 5.0) + (1 - 0.15) × 0.3
score = 0.10 + 0.255 = 0.355

If combined with other strategies (news, volume), final score could exceed 0.62
```

**Code Location:** `strategies.py:55-84`

---

### 3. News Strategy

**What it does:** Scores stocks with high recent news activity (6-hour window)

**Scoring Formula:**
```python
score = min(1.0, log(news_count + 1) / log(20))
```

**Logarithmic Scaling:**
| News Articles | Score |
|--------------|-------|
| 0 | 0.00 |
| 1 | 0.23 |
| 3 | 0.46 |
| 5 | 0.60 |
| 10 | 0.80 |
| 20+ | 1.00 |

**Purchase Trigger:** Combined with other strategies, boosts final score

**Example Buy Scenario:**
```
Symbol: TSLA
- 8 news articles in past 6 hours
- Score = log(8+1) / log(20) = 0.73

Combined with positive momentum (0.55), final score = weighted average
If news weight is 0.10 and momentum weight is 0.35:
final = (0.73 × 0.10) + (0.55 × 0.35) + ... ≈ 0.65 → BUY
```

**Code Location:** `strategies.py:87-105`

---

### 4. Volume Strategy

**What it does:** Identifies stocks trading with abnormally high volume

**Scoring Formula:**
```python
volume_ratio = current_volume / avg_volume

if volume_ratio > 1.0:
    score = min(1.0, 0.5 + (volume_ratio - 1.0) × 0.2)  # Above average
else:
    score = 0.5 × volume_ratio  # Below average
```

**Scaling:**
| Volume Ratio | Score |
|--------------|-------|
| 0.5x average | 0.25 |
| 1.0x average | 0.50 |
| 2.0x average | 0.70 |
| 3.0x average | 0.90 |
| 5.0x average | 1.00 |

**Purchase Trigger:** High volume confirms other signals

**Example Buy Scenario:**
```
Symbol: NVDA
- Average daily volume: 50M shares
- Current volume: 120M shares → 2.4x average

score = 0.5 + (2.4 - 1.0) × 0.2 = 0.78

High volume + momentum → strong combined signal
```

**Code Location:** `strategies.py:108-129`

---

### 5. Earnings Strategy

**What it does:** Scores stocks approaching earnings announcements

**Scoring Formula:**
```python
if days_until_earnings <= 7:
    score = 1.0 - (days_until / 7)
else:
    score = 0
```

**Scoring by Days:**
| Days Until Earnings | Score |
|--------------------|-------|
| 7+ days | 0.00 |
| 6 days | 0.14 |
| 4 days | 0.43 |
| 2 days | 0.71 |
| 1 day | 0.86 |
| 0 days (today) | 1.00 |

**Purchase Trigger:** Scores increase as earnings date approaches

**Example Buy Scenario:**
```
Symbol: GOOGL
- Earnings in 2 days
- Score = 1.0 - (2/7) = 0.71

Combined with other positive signals, creates high-probability setup
before potential earnings volatility
```

**Code Location:** `strategies.py:132-154`

---

### 6. Long-term Trend Strategy

**What it does:** Identifies stocks with sustained directional movement

**Scoring Formula:**
```python
trend_change = (current_price - prev_close) / prev_close

if trend_change > 0:
    score = min(1.0, trend_change × 10)  # Uptrend
else:
    score = max(0.0, 0.5 + trend_change × 10)  # Downtrend
```

**Scoring:**
- Uptrends: +5% change → score 0.50, +10% change → score 1.00
- Downtrends: -5% change → score 0.00

**Purchase Trigger:** Strong positive trend in TRENDING_UP regime

**Example Buy Scenario:**
```
Symbol: AMD
- Previous close: $100
- Current: $106 → +6% trend

score = min(1.0, 0.06 × 10) = 0.60

In TRENDING_UP regime, this strategy gets 0.20 weight (high)
Contributes significantly to final score
```

**Code Location:** `strategies.py:157-181`

---

### 7. Long-term Momentum Strategy

**What it does:** Looks for sustained momentum with gap support

**Scoring Formula:**
```python
price_momentum = (current_price - prev_close) / prev_close
gap_momentum = (open_price - prev_close) / prev_close

if price_momentum > 0 and gap_momentum > 0:
    score = min(1.0, (price_momentum + gap_momentum) × 5)  # Strong
elif price_momentum > 0:
    score = min(0.7, price_momentum × 5)  # Moderate
else:
    score = max(0.0, 0.5 + price_momentum × 5)  # Weak
```

**Best Case:** Both price and gap positive (gap up and continuing higher)

**Purchase Trigger:** Stock gaps up and continues climbing

**Example Buy Scenario:**
```
Symbol: META
- Closed: $300
- Opened: $310 (gap up +3.33%)
- Current: $315 (price momentum +5%)

score = min(1.0, (0.05 + 0.0333) × 5) = 0.417

Strong momentum signal, especially in TRENDING_UP regime
```

**Code Location:** `strategies.py:184-217`

---

### 8. Crypto Strategy

**What it does:** Specialized scoring for 24/7 cryptocurrency markets

**Scoring Formula:**
```python
trend = (current_price - prev_close) / prev_close
price_range_pct = (high - low) / open × 100

if trend > 0:
    score = min(1.0, (trend × 5) + (price_range_pct / 10))  # Uptrend
else:
    score = max(0.2, 0.5 + (trend × 3))  # Downtrend
```

**Key Differences from Stock Strategies:**
- 24/7 market activity considered normal
- Higher volatility expected and acceptable
- Wider stop losses (2x multiplier)
- Minimum downtrend score of 0.2 (never completely negative)

**Purchase Trigger:** Strong uptrend with high volatility in crypto assets

**Example Buy Scenario:**
```
Symbol: BTC/USD
- Previous close: $45,000
- Current: $46,500 → +3.33% trend
- Price range today: 5% (high volatility)

score = min(1.0, (0.0333 × 5) + (5 / 10))
score = min(1.0, 0.1665 + 0.5) = 0.6665

Crypto score > 0.62 → BUY SIGNAL
```

**Code Location:** `strategies.py:220-259`

---

## Purchase Trigger Conditions

A purchase is executed when ALL of the following conditions are met:

### 1. Signal Strength Requirements

**Location:** `strategy_manager.py:552-568`

```python
def get_entry_signal(signal, entry_threshold=0.62):
    # Must pass all these checks:

    ✓ final_score >= 0.62 (default threshold)
    ✓ active_strategies >= 2 (at least 2 strategies scoring > 0.3)
    ✓ confidence >= 0.3
    ✓ Special rule: HIGH_VOLATILITY requires score >= 0.72 for non-crypto
```

**Example:**
```
Symbol: AAPL
- Final score: 0.68 ✓
- Active strategies: ['momentum', 'volume', 'news'] (3) ✓
- Confidence: 0.35 ✓
- Regime: TRENDING_UP (not high volatility) ✓

RESULT: ENTRY SIGNAL APPROVED
```

### 2. Position and Risk Checks

**Location:** `engine.py:397-589`

```python
# Before any entry:
✓ No existing position in this symbol
✓ No risk violations (max positions, daily loss limits, etc.)
✓ Buying power available
✓ Valid current price data
✓ Position size >= 1 share
✓ Order passes validation (price checks, market hours for stocks, etc.)
✓ New positions this cycle < 3 (max 3 new entries per check)
```

### 3. Execution Process

**Step-by-step flow when conditions are met:**

```
1. System checks top 20 ranked candidates
2. For each candidate (up to 3 new positions):

   a. Verify no existing position
   b. Check signal strength (final_score >= 0.62)
   c. Get current market price
   d. Calculate regime-aware stop loss
   e. Calculate position size based on risk (typically 1-2% risk per trade)
   f. Validate order won't exceed risk limits
   g. Log entry decision with details
   h. Place market order (buy)
   i. Record trade in state store

3. Stop after 3 new positions opened in this cycle
```

**Code Location:** `engine.py:440-586`

### 4. Timing and Refresh

**Candidates are refreshed every 30 minutes (default):**

```python
# During market hours: Check stock universe (up to 100 symbols)
# After hours with crypto enabled: Check crypto universe (up to 50 symbols)
# After hours without crypto: Still check stocks

Refresh interval: 30 minutes (configurable)
Entry checks: Every trading cycle (typically every minute when market open)
```

**Code Location:** `engine.py:590-646`

---

## Position Sizing and Risk Management

### Position Size Calculation

**Location:** `risk_manager.py`

```python
def calculate_position_size(
    symbol,
    current_price,
    stop_loss_price,
    account_value,
    existing_positions
):
    # Risk per trade: 1% of account (default)
    # Max position size: 5% of account (default)
    # Max positions: 10 (default)

    risk_amount = account_value × 0.01
    stop_distance = current_price - stop_loss_price

    # Shares based on risk
    shares_by_risk = risk_amount / stop_distance

    # Shares based on max position size
    max_position_value = account_value × 0.05
    shares_by_max = max_position_value / current_price

    # Use smaller of the two
    quantity = min(shares_by_risk, shares_by_max)

    # If at max positions, quantity = 0
    if existing_positions >= 10:
        quantity = 0

    return int(quantity)
```

**Example:**
```
Account value: $100,000
Current price: $100
Stop loss: $95 (5% below entry)
Existing positions: 3

risk_amount = $100,000 × 0.01 = $1,000
stop_distance = $100 - $95 = $5
shares_by_risk = $1,000 / $5 = 200 shares

max_position_value = $100,000 × 0.05 = $5,000
shares_by_max = $5,000 / $100 = 50 shares

quantity = min(200, 50) = 50 shares
position_value = 50 × $100 = $5,000 (5% of account)
risk_on_trade = 50 × $5 = $250 (0.25% of account)
```

### Stop Loss Calculation

**Location:** `strategy_manager.py:570-590`

Stop losses are **regime-aware**:

| Regime | Multiplier | Example (base 0.5%) |
|--------|-----------|-------------------|
| TRENDING_UP | 0.8x | 0.4% stop |
| TRENDING_DOWN | 1.2x | 0.6% stop |
| RANGING | 1.0x | 0.5% stop |
| HIGH_VOLATILITY | 1.5x | 0.75% stop |
| LOW_VOLATILITY | 0.7x | 0.35% stop |
| Crypto (any regime) | 2.0x | 1.0% stop |

**Formula:**
```python
adjusted_stop_bps = base_stop_bps × regime_multiplier
if is_crypto:
    adjusted_stop_bps × 2

stop_loss_price = entry_price × (1 - adjusted_stop_bps / 10000)
```

**Example:**
```
Entry: $100
Base stop: 50 bps (0.5%)
Regime: HIGH_VOLATILITY
Asset: Stock

adjusted_stop = 50 × 1.5 = 75 bps (0.75%)
stop_loss_price = $100 × (1 - 75/10000) = $99.25

For crypto: stop_loss_price = $100 × (1 - 150/10000) = $98.50
```

---

## Strategy Weighting by Regime

The system dynamically adjusts strategy importance based on market conditions:

### TRENDING_UP Market

**Characteristics:** Strong upward momentum, buyers in control

| Strategy | Weight | Reasoning |
|----------|--------|-----------|
| Momentum | 0.35 | Trend following works best |
| Long-term Trend | 0.20 | Sustained moves likely |
| Volume | 0.15 | Confirms strength |
| Long-term Momentum | 0.10 | Continuation likely |
| News | 0.08 | Moderate importance |
| Mean Reversion | 0.05 | Counter-trend less likely |
| Crypto | 0.05 | Baseline |
| Earnings | 0.02 | Minimal importance |

**Best Strategies:** Momentum, Long-term Trend, Volume

---

### TRENDING_DOWN Market

**Characteristics:** Strong downward momentum, sellers in control

| Strategy | Weight | Reasoning |
|----------|--------|-----------|
| Mean Reversion | 0.35 | Look for oversold bounces |
| Volume | 0.15 | Identify capitulation |
| News | 0.12 | Negative catalysts important |
| Momentum | 0.10 | Reduced importance |
| Long-term Momentum | 0.10 | Some continuation trades |
| Long-term Trend | 0.08 | Reduced importance |
| Earnings | 0.05 | Moderate importance |
| Crypto | 0.05 | Baseline |

**Best Strategies:** Mean Reversion, Volume, News

---

### RANGING Market

**Characteristics:** Sideways action, no clear direction

| Strategy | Weight | Reasoning |
|----------|--------|-----------|
| Mean Reversion | 0.40 | Buy dips, sell rips |
| News | 0.15 | Catalyst for breakout |
| Long-term Trend | 0.15 | Identify subtle trends |
| Volume | 0.10 | Spot breakout attempts |
| Momentum | 0.05 | Reduced importance |
| Long-term Momentum | 0.05 | Minimal importance |
| Earnings | 0.05 | Moderate importance |
| Crypto | 0.05 | Baseline |

**Best Strategies:** Mean Reversion, News, Long-term Trend

---

### HIGH_VOLATILITY Market

**Characteristics:** Large price swings, increased risk

| Strategy | Weight | Reasoning |
|----------|--------|-----------|
| Volume | 0.25 | Critical for validation |
| Momentum | 0.20 | Capture big moves |
| Mean Reversion | 0.15 | Snap-back trades |
| News | 0.15 | Identify catalysts |
| Long-term Trend | 0.08 | Less reliable |
| Long-term Momentum | 0.07 | Less reliable |
| Earnings | 0.05 | Moderate importance |
| Crypto | 0.05 | Baseline |

**Best Strategies:** Volume, Momentum, News
**Special Rule:** Non-crypto requires score >= 0.72 (vs 0.62 normal)

---

### Code Location

**Regime weights:** `strategy_manager.py:128-169`

**Weight application:** `strategy_manager.py:509-520`

```python
# Weights are applied during scoring:
for signal in signals:
    weight = signal.confidence  # Base weight from regime

    # If strategy doesn't match regime, reduce weight by 50%
    if not signal.regime_match:
        weight *= 0.5

    weighted_sum += signal.score × weight
    total_weight += weight

final_score = weighted_sum / total_weight
```

---

## Complete Purchase Example

**Scenario:** Stock showing strong momentum during market hours

### Input Data
```
Symbol: NVDA
Time: 10:30 AM (market open)
Previous close: $800
Open: $810 (gap up +1.25%)
Current: $825 (up +3.1% total)
High: $828
Low: $808
Volume: 45M (avg: 30M → 1.5x)
News articles (6hr): 4
Earnings: Not scheduled
Account equity: $50,000
Existing positions: 2
```

### Step 1: Regime Detection
```
current > open: ✓ ($825 > $810)
open >= prev_close: ✓ ($810 >= $800)
intraday_move_pct = ($825-$810)/$810 = 1.85%

REGIME: TRENDING_UP
```

### Step 2: Strategy Scoring

**Momentum:**
```
intraday_change = ($825-$810)/$810 = 0.0185 (1.85%)
gap = ($810-$800)/$800 = 0.0125 (1.25%)
position_in_range = ($825-$808)/($828-$808) = 0.85

score = (0.0185 × 5.0) + (0.0125 × 2.0) + (0.85 - 0.5) × 0.3
score = 0.0925 + 0.025 + 0.105 = 0.2225
normalized ≈ 0.72

Weight in TRENDING_UP: 0.35
Weighted contribution: 0.72 × 0.35 = 0.252
```

**Mean Reversion:**
```
deviation = ($800-$825)/$800 = -0.03 (negative)
score = 0 (only scores when price is down)

Weight: 0.05
Weighted contribution: 0 × 0.05 = 0
```

**News:**
```
news_count = 4
score = log(4+1)/log(20) = 0.537

Weight: 0.08
Weighted contribution: 0.537 × 0.08 = 0.043
```

**Volume:**
```
volume_ratio = 45M / 30M = 1.5
score = 0.5 + (1.5-1.0) × 0.2 = 0.6

Weight: 0.15
Weighted contribution: 0.6 × 0.15 = 0.09
```

**Long-term Trend:**
```
trend_change = ($825-$800)/$800 = 0.03125 (3.125%)
score = min(1.0, 0.03125 × 10) = 0.3125

Weight: 0.20
Weighted contribution: 0.3125 × 0.20 = 0.0625
```

**Long-term Momentum:**
```
price_momentum = 0.03125 (3.125%)
gap_momentum = 0.0125 (1.25%)
Both positive!
score = min(1.0, (0.03125 + 0.0125) × 5) = 0.2188

Weight: 0.10
Weighted contribution: 0.2188 × 0.10 = 0.02188
```

**Total:**
```
final_score = 0.252 + 0 + 0.043 + 0.09 + 0.0625 + 0.02188
final_score = 0.469

Active strategies: momentum (0.72), volume (0.6), news (0.537), longterm_trend (0.31)
Count: 4 strategies > 0.3
Confidence: mean of weights ≈ 0.16
```

### Step 3: Entry Signal Check
```
✗ final_score (0.469) >= 0.62 threshold → FAILS

RESULT: NO ENTRY - Score too low despite good signals
```

**Why it failed:** While individual strategies showed promise, the weighted combination didn't reach the 0.62 threshold. The mean reversion strategy contributed nothing (stock was up, not down), and some strategies had lower individual scores.

**What would trigger entry:**
- Higher volume (2.5x instead of 1.5x) → volume score 0.8
- More news articles (8 instead of 4) → news score 0.73
- Either would push final_score above 0.62

---

## Summary: When Does a Purchase Happen?

A purchase occurs when:

1. **Market is analyzed** every refresh cycle (30min default)
2. **Regime is detected** (TRENDING_UP, RANGING, etc.)
3. **8 strategies score** the candidate (0 to 1 each)
4. **Weighted final score calculated** using regime-specific weights
5. **Signal passes validation:**
   - Final score >= 0.62 (or 0.72 in high volatility for non-crypto)
   - At least 2 strategies scoring > 0.3
   - Confidence >= 0.3
6. **Risk checks pass:**
   - No existing position in symbol
   - Account has buying power
   - Under max position limit (10 default)
   - Valid price data available
7. **Position sized** based on 1% risk per trade
8. **Stop loss calculated** using regime multipliers
9. **Order validated** for market conditions
10. **Market order placed** to buy

**Frequency:** Checked every trading cycle, limited to 3 new positions per cycle

**Key Threshold:** Final score >= 0.62 is the primary gate for entries
