# Trading System Conceptual Architecture Analysis

## Executive Summary

This trading system uses a **multi-layered architecture** combining:
- **Strategies**: Independent scoring systems that provide signal strength (0-1 score)
- **Regimes**: Market condition classifications that adjust strategy weights
- **Multipliers**: Adjustment factors applied to risk parameters based on market conditions

---

## 1. STRATEGIES: Independent Scoring Systems

### Definition
Strategies are **independent decision engines** that each assign a score (0-1) to a symbol indicating how attractive it is for trading.

### Current Strategies (10 total)

| Strategy | Role | Scoring | File |
|----------|------|---------|------|
| **Momentum** | Intraday trend following | Intraday change, gap, position in range | strategies.py:29-58 |
| **Mean Reversion** | Oversold bounce plays | Deviation from prev close | strategies.py:61-90 |
| **News** | Sentiment-driven trades | Article count + sentiment analysis | strategies.py:93-181 |
| **Volume** | Confirmation of strength | Volume ratio to average | strategies.py:184-205 |
| **Earnings** | Event-driven trades | Days until earnings date | strategies.py:208-230 |
| **Long-term Trend** | Sustained directional moves | Current vs previous close | strategies.py:233-257 |
| **Long-term Momentum** | Gap-supported momentum | Price + gap momentum combined | strategies.py:260-293 |
| **Crypto** | 24/7 digital assets | 40% basic + 60% indicators | strategies.py:296-416 |
| **Forex** | Currency pairs 24/5 | Technical indicators focused | strategies.py:419-545 |
| **ETF** | Index/sector tracking | Momentum + volume + sector | strategies.py:548-668 |

### How Strategies Work

**Example: Momentum Strategy**
```python
# File: strategy_manager.py, lines 208-229
def score_momentum(current_price, open_price, prev_close, high, low):
    # Calculate three independent components
    intraday_change = (current_price - open_price) / open_price    # 5x weight
    gap = (open_price - prev_close) / prev_close                    # 2x weight
    position_in_range = (current_price - low) / (high - low)       # 0.3x weight
    
    # Combine with weights
    score = (intraday_change × 5.0) + (gap × 2.0) + (position_in_range - 0.5) × 0.3
    
    # Normalize to 0-1 range
    return max(0, min(1, (score + 0.5)))
```

**Key Characteristics:**
- Each strategy produces a **single float score** between 0 and 1
- Score is **independent** - not influenced by other strategies
- Score represents **confidence/strength** of that specific signal
- **No time aspect** - only looks at current market data
- **No external events** - just price/volume/news data

### Strategy Scoring Flow
```
Candidate Symbol (e.g., AAPL)
    ↓
Strategy 1 → Score 0.72 (Momentum: strong uptrend)
Strategy 2 → Score 0.45 (Mean Reversion: not oversold)
Strategy 3 → Score 0.65 (News: positive sentiment)
Strategy 4 → Score 0.60 (Volume: above average)
Strategy 5 → Score 0.30 (Earnings: not scheduled)
    ↓
Combine Scores → Final Decision (via Regimes)
```

---

## 2. MARKET REGIMES: Contextual Market States

### Definition
**Regimes** are the current market condition classifications that determine:
- Which strategies are most relevant
- How much weight each strategy receives
- Risk parameter adjustments

### RegimeDetector Implementation

**File:** `strategy_manager.py:49-105`

```python
class MarketRegime(Enum):
    """Market regime classification"""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"
```

### Regime Detection Logic

**File:** `strategy_manager.py:55-105`

| Regime | Detection Criteria | Example |
|--------|-------------------|---------|
| **TRENDING_UP** | current > open AND open ≥ prev_close AND intraday move > 1% | Stock opens $100, now $102.50, prev close $99 |
| **TRENDING_DOWN** | current < open AND open ≤ prev_close AND intraday move > 1% | Stock opens $100, now $97.50, prev close $101 |
| **RANGING** | Price range 0.5-2% AND gap < 1% | Stock moves $0.50 in a narrow band |
| **HIGH_VOLATILITY** | Price range > 3% OR gap > 2% | Stock swings $3+, earnings announcement |
| **LOW_VOLATILITY** | Price range < 0.5% | Consolidation, no major news |
| **UNKNOWN** | Doesn't fit patterns | Mixed signals |

### Regime-Specific Strategy Weights

**File:** `strategy_manager.py:148-198`

#### TRENDING_UP Market
```python
weights_trending_up = {
    'momentum': 0.30,              # HIGHEST - trend following works
    'longterm_trend': 0.20,        # Sustained move
    'volume': 0.13,                # Confirms strength
    'longterm_momentum': 0.09,     # Continuation play
    'news': 0.07,                  # Positive catalysts
    'mean_reversion': 0.04,        # Counter-trend unlikely
    'earnings': 0.02,              # Event risk lower
    'crypto': 0.05, 'forex': 0.08, 'etf': 0.04
}
```

#### RANGING Market
```python
weights_ranging = {
    'mean_reversion': 0.40,        # HIGHEST - buy dips, sell rips
    'news': 0.15,                  # Catalyst for breakout
    'longterm_trend': 0.13,        # Subtle trend
    'volume': 0.09,                # Spot breakout
    'momentum': 0.04,              # Less relevant
    'longterm_momentum': 0.04,     # Less relevant
    'earnings': 0.04,
    'crypto': 0.05, 'forex': 0.10, 'etf': 0.04
}
```

#### HIGH_VOLATILITY Market
```python
weights_high_volatility = {
    'volume': 0.22,                # HIGHEST - validates strength
    'momentum': 0.18,              # Capture big moves
    'mean_reversion': 0.13,        # Snap-back trades
    'news': 0.13,                  # Identify catalysts
    'longterm_trend': 0.07,        # Less reliable
    'longterm_momentum': 0.06,     # Less reliable
    'earnings': 0.04,
    'crypto': 0.05, 'forex': 0.08, 'etf': 0.04
}
```

### How Regimes Influence Trading

**File:** `strategy_manager.py:608-640`

```python
# STAGE 1: Filter to regime-matching strategies only
regime_specific_signals = [s for s in all_signals if s.regime_match]

# STAGE 2: Weight only by confidence (regime-aware)
for signal in regime_specific_signals:
    weight = strategy_confidence[signal.strategy_name]  # Fixed confidence
    weighted_sum += signal.score × weight
    total_weight += weight

final_score = weighted_sum / total_weight
```

---

## 3. MULTIPLIERS: Risk Parameter Adjusters

### Definition
**Multipliers** are adjustment factors applied to risk parameters based on market conditions. Currently, multipliers are **only used for stop losses**.

### Current Multiplier Implementation

**File:** `strategy_manager.py:700-736`

```python
def calculate_stop_loss(entry_price, regime, base_stop_bps=50.0, is_crypto=False):
    
    # Base stop loss: 50 basis points (0.5%)
    regime_multipliers = {
        MarketRegime.TRENDING_UP: 0.8,          # Tighter: 0.4%
        MarketRegime.TRENDING_DOWN: 1.2,        # Wider: 0.6%
        MarketRegime.RANGING: 1.0,              # Standard: 0.5%
        MarketRegime.HIGH_VOLATILITY: 1.5,      # Much wider: 0.75%
        MarketRegime.LOW_VOLATILITY: 0.7,       # Tighter: 0.35%
        MarketRegime.UNKNOWN: 1.0               # Standard: 0.5%
    }
    
    multiplier = regime_multipliers[regime]
    
    # Crypto needs 2x wider stops due to volatility
    if is_crypto:
        multiplier *= 2.0
    
    adjusted_stop_bps = base_stop_bps × multiplier
    stop_loss = entry_price × (1 - adjusted_stop_bps / 10000.0)
    return stop_loss
```

### Example Stop Loss Calculation
```
Entry Price: $100
Base Stop Loss: 50 bps (0.5%)

Regime: TRENDING_UP
  Multiplier: 0.8
  Adjusted Stop: 50 × 0.8 = 40 bps (0.4%)
  Stop Price: $100 × (1 - 40/10000) = $99.60

Regime: HIGH_VOLATILITY
  Multiplier: 1.5
  Adjusted Stop: 50 × 1.5 = 75 bps (0.75%)
  Stop Price: $100 × (1 - 75/10000) = $99.25

Regime: HIGH_VOLATILITY + Crypto
  Multiplier: 1.5 × 2.0 = 3.0
  Adjusted Stop: 50 × 3.0 = 150 bps (1.5%)
  Stop Price: $100 × (1 - 150/10000) = $98.50
```

### Where Multipliers Don't Exist

Looking through the codebase, **multipliers are NOT used for:**
- Position sizing (fixed by risk calculation in risk_manager.py)
- Entry thresholds (0.62 baseline, adjustable per strategy)
- Exit thresholds (per strategy_configs.py)
- Take profit targets (per strategy_configs.py)

---

## 4. KEY ARCHITECTURAL DIFFERENCE: Strategies vs Multipliers/Regimes

### Strategies: WHAT to trade
- Answer: "Should I enter this symbol right now?"
- Input: Current market data (price, volume, news)
- Output: Score 0-1 (confidence/strength)
- Time: Immediate, no forward-looking
- Example: "Momentum is strong (0.75), but mean reversion is weak (0.30)"

### Regimes: HOW market behaves
- Answer: "What type of market are we in?"
- Input: Price action metrics (trend, volatility, range)
- Output: Market condition classification
- Time: Describes current moment
- Example: "Market is in HIGH_VOLATILITY regime"

### Multipliers: ADJUST risk based on conditions
- Answer: "How much risk should I take given conditions?"
- Input: Regime + asset type
- Output: Adjustment factors (0.7× to 3.0×)
- Time: Applied to all new trades
- Example: "HIGH_VOLATILITY regime → 1.5× wider stops"

### Visual Comparison

```
                  STRATEGIES        REGIMES          MULTIPLIERS
─────────────────────────────────────────────────────────────────
Purpose          Entry scoring     Context          Risk tuning
Input            Price/Volume      Price action     Regime
Output           0-1 score         Classification   Adjustment factor
Scope            Per-symbol        Whole market     Risk parameters
Usage            Candidate rank    Weight strategies Stop loss width
Dynamic?         Yes (fast)        Yes (intraday)   Yes (per regime)
Affects entry?   YES (directly)    YES (weights)    NO (only stops)
```

---

## 5. CURRENT NEWS & EARNINGS: How They're Used as Strategies

### News as a Strategy

**File:** `strategies.py:93-181`

**Scoring Logic:**
```python
def score_news(symbol, articles, window_hours=6):
    # 1. Count articles for activity signal
    volume_score = min(0.5, log(count + 1) / log(20) × 0.5)
    
    # 2. Analyze sentiment of articles
    avg_sentiment = mean([article.sentiment for articles])
    sentiment_component = avg_sentiment × 0.5  # Range: -0.5 to +0.5
    
    # 3. Combine: activity + direction
    score = volume_score + sentiment_component
    return max(0, min(1, score))
```

**Weight in Each Regime:**
```python
'news': {
    'TRENDING_UP': 0.07,          # Low priority in momentum
    'TRENDING_DOWN': 0.10,        # Higher in downtrends
    'RANGING': 0.15,              # HIGH in ranges (catalyst)
    'HIGH_VOLATILITY': 0.13,      # Moderate importance
    'LOW_VOLATILITY': 0.10,       # Default
}
```

**Current Role:**
- News is treated like any other scoring signal
- Contributes a 0-1 score to the final decision
- Weight varies by regime (0.07 to 0.15)
- Sentiment can push score down (negative news) or up (positive)

### Earnings as a Strategy

**File:** `strategies.py:208-230` and `strategy_manager.py:280-297`

**Scoring Logic:**
```python
def score_earnings(symbol, earnings_calendar, days_until_limit=7):
    if symbol not in earnings_calendar:
        return 0  # No earnings scheduled
    
    days_until = earnings_calendar[symbol]['days_until']
    
    if days_until <= 7:
        score = 1.0 - (days_until / 7)  # 0 at 7 days, 1.0 at 0 days
    else:
        score = 0  # Too far away
    
    return score
```

**Scoring by Days:**
```
7+ days:  0.00 (no signal)
6 days:   0.14
4 days:   0.43
2 days:   0.71
1 day:    0.86
0 days:   1.00 (today!)
```

**Weight in Each Regime:**
```python
'earnings': {
    'TRENDING_UP': 0.02,          # LOWEST - event risk suppressed
    'TRENDING_DOWN': 0.04,        # Low
    'RANGING': 0.04,              # Low
    'HIGH_VOLATILITY': 0.04,      # Low
    'LOW_VOLATILITY': 0.05,       # Slightly higher
}
```

**Current Role:**
- Earnings scores are very LOW weight (0.02-0.05)
- Rarely influences final entry decision
- Acts as a weak tiebreaker or tertiary signal
- No special volatility adjustment despite earnings event risk

### Problems with Current Approach

1. **News & Earnings as Strategies Are Weak:**
   - They compete with daily price patterns
   - Earnings gets only 0.02-0.05 weight (compared to 0.30 for momentum)
   - News gets swept away by volume/momentum signals

2. **No Context Adjustment:**
   - Stop losses don't widen around earnings
   - Position sizing doesn't reduce for earnings risk
   - Take profit doesn't tighten for earnings volatility

3. **Wrong Time Dimension:**
   - Strategies score on "current moment"
   - News/earnings are "event-driven" with forward-looking impact
   - Current model treats 1 day before earnings same as momentum

4. **No Volatility Management:**
   - Crypto has 2× stop loss multiplier
   - Earnings should have 1.5-2× multiplier
   - News events should adjust position sizing

---

## 6. BETTER ARCHITECTURE: News & Earnings as Multipliers/Regimes

### Proposal 1: Earnings as a Regime Component

**Current (Strategy):**
```
Score = 0.0-1.0 contribution to final decision
Weight = 0.02-0.05 in regime weights
Effect: Negligible
```

**Better (Regime + Multipliers):**
```
1. Create EARNINGS-specific regime
   - Input: Days until earnings for ANY symbol in portfolio
   - Output: "EARNINGS_IMMINENT" regime classification
   
2. Apply multipliers when earnings detected:
   - Stop loss: × 1.5 to 2.0 (widen for volatility)
   - Position size: × 0.5 to 0.75 (reduce for event risk)
   - Entry threshold: +0.05 to +0.10 (higher bar to enter)
   - Max hold time: ÷ 2 (close before announcement)

3. Code Example:
   
   if earnings_days_until <= 2:
       regime_event = "EARNINGS_IMMINENT"
       stop_multiplier = 1.8
       position_size_multiplier = 0.6
       entry_threshold_boost = 0.08
```

### Proposal 2: News as a Volatility Regime Modifier

**Current (Strategy):**
```
Score = sentiment × 0.5 + volume_score
Weight = 0.07-0.15 in regime weights
Effect: Moderate, but ignores volatility
```

**Better (Event Detection + Volatility Adjustment):**
```
1. Detect news event types
   - Material news (negative, earnings miss, downgrade) → HIGH_VOLATILITY_EVENT
   - Positive catalyst → BULLISH_EVENT
   - Earnings announcement → EARNINGS_VOLATILITY
   
2. Apply volatility multipliers:
   
   if negative_news_headline_detected:
       regime_override = "HIGH_VOLATILITY_EVENT"
       stop_multiplier = 2.0 (double stops)
       entry_threshold = 0.72 (from 0.62)
       max_position_pct = 1.0 (from 2.5)
   
   elif positive_catalyst:
       regime_boost = "BULLISH_CATALYST"
       momentum_weight_boost = +0.10
       volatility_adjustment = -0.2 (tighter stops)

3. Apply news sentiment differently:
   - Instead of adding to entry score
   - Use as regime modifier
   - Adjust all risk parameters based on sentiment
```

### Proposal 3: Integrated Event Calendar Regime

**New Architecture:**

```
// Step 1: Event Detection
events = {
    'AAPL': {
        'earnings': 2,           // 2 days
        'ex_dividend': None,
        'stock_split': None,
        'analyst_note': 'downgrade',
        'recent_news_sentiment': -0.6
    }
}

// Step 2: Event-Based Regime
event_regime = detect_event_regime(symbol, events)
// Returns: "EARNINGS_IMMINENT", "NEGATIVE_CATALYST", "BULLISH_SETUP", etc.

// Step 3: Apply Event-Specific Parameters
if event_regime == "EARNINGS_IMMINENT":
    base_regime = detect_regime(prices)  // TRENDING_UP
    
    // Override with event multipliers
    entry_threshold = 0.62 + 0.10  // Higher bar: 0.72
    stop_loss_mult = 1.8            // Wider: 90 bps instead of 50
    position_size_mult = 0.6        // Smaller: 60% of normal
    max_hold_minutes = 240 ÷ 2      // Exit before announcement
    
// Step 4: Weight strategies differently
if event_regime contains "EARNINGS":
    momentum_weight = 0.20  // Reduce from 0.30
    volume_weight = 0.15    // Reduce from 0.13
    news_weight = 0.05      // Remove entirely (already known)
else:
    // Normal regime weights
```

---

## 7. RECOMMENDED ARCHITECTURE CHANGES

### Phase 1: Separate News/Earnings from Strategies

```python
# TODAY (strategies.py)
def score_news(symbol, articles):  # 0-1 score
def score_earnings(symbol, calendar):  # 0-1 score

# TOMORROW (event_manager.py)
def detect_earnings_event(symbol, calendar):  # True/False
def get_news_sentiment(symbol, articles):  # -1.0 to +1.0
def classify_news_event_type(articles):  # 'negative', 'positive', 'neutral'
```

### Phase 2: Create Event Regime System

```python
class EventRegime(Enum):
    """Event-based market conditions"""
    EARNINGS_IMMINENT = "earnings_imminent"      # 0-2 days
    EARNINGS_WEEK = "earnings_week"              # 3-7 days
    MAJOR_ECONOMIC_EVENT = "major_event"         # FOMC, NFP, etc.
    NEGATIVE_CATALYST = "negative_catalyst"      # Bad news
    BULLISH_CATALYST = "bullish_catalyst"        # Good news
    EARNINGS_ANNOUNCED = "earnings_announced"    # Already happened
    NO_EVENT = "no_event"
```

### Phase 3: Event-Based Multipliers

```python
EVENT_MULTIPLIERS = {
    "EARNINGS_IMMINENT": {
        "entry_threshold_boost": 0.10,
        "stop_loss_multiplier": 1.8,
        "position_size_multiplier": 0.6,
        "max_hold_minutes_divisor": 2.0,
        "take_profit_tight": True
    },
    "NEGATIVE_CATALYST": {
        "entry_threshold_boost": 0.15,
        "stop_loss_multiplier": 2.0,
        "position_size_multiplier": 0.5,
        "max_hold_minutes_divisor": 3.0,
        "skip_entries": True  // Don't enter
    },
    "BULLISH_CATALYST": {
        "entry_threshold_reduction": -0.05,
        "stop_loss_multiplier": 0.8,
        "position_size_multiplier": 1.2,
        "momentum_weight_boost": 0.10
    }
}
```

### Phase 4: Update Entry Logic

```python
# CURRENT (strategy_manager.py:rank_candidates)
final_score = weighted_average(strategy_scores, regime_weights)
entry = final_score >= 0.62

# PROPOSED
event_regime = detect_event_regime(symbol)
base_regime = detect_price_regime(symbol)

if event_regime == "NEGATIVE_CATALYST":
    skip_entries = True  // Don't trade at all
    close_existing = True  // Exit positions
    
elif event_regime == "EARNINGS_IMMINENT":
    adjusted_entry_threshold = 0.62 + 0.10  // Stricter
    adjusted_stop_mult = 1.8  // Wider
    adjusted_position_size = normal_size × 0.6  // Smaller

else:
    // Use base regime logic (TRENDING_UP, RANGING, etc.)
```

---

## 8. SUMMARY TABLE

| Aspect | Strategies | Regimes | Multipliers | News | Earnings |
|--------|-----------|---------|-------------|------|----------|
| **What?** | Entry signals | Market context | Risk tuners | Sentiment data | Event calendar |
| **Currently** | 10 scoring functions | 6 classifications | Stop loss only | Strategy #3 | Strategy #5 |
| **Score** | 0-1 float | Enum class | 0.7-3.0 factor | -1.0 to +1.0 | 0.0-1.0 by days |
| **Should Be** | Price action signals | Price + time context | Risk params | Event detection | Event regime |
| **Weight** | Variable by regime | Selects strategy set | Applied to stops | 0.07-0.15 | 0.02-0.05 |
| **Better As** | ✓ Current (good) | ✓ Current (good) | ✓ Expand | Event detector | Event regime |

---

## 9. FILES TO REVIEW FOR IMPLEMENTATION

1. **strategy_manager.py** - Where regimes apply weights
2. **strategy_configs.py** - Per-strategy parameters
3. **risk_manager.py** - Position sizing (no multipliers here)
4. **news.py** - Article fetching + sentiment
5. **earnings.py** - Calendar fetching
6. **engine.py** - Where entry decisions happen
7. **strategies.py** - Individual scoring functions

---

## 10. CONCRETE EXAMPLE: How It Should Work

### Scenario: Apple (AAPL) - 1 Day Before Earnings

**Current System:**
```
Momentum score:        0.65 (weight 0.22 base)
Mean reversion:        0.30 (weight 0.18)
News:                  0.45 (weight 0.10)
Volume:                0.70 (weight 0.08)
Earnings:              0.86 (weight 0.05)  ← Too low!
─────────────────────────────
Final: 0.62 → ENTRY SIGNAL ✓

But position sizing: 2% of account (normal)
Stop loss: 50 bps (normal)
Max hold: 4 hours (normal)
→ Problem: No special consideration for earnings event!
```

**Proposed System:**
```
// Step 1: Detect event
earnings_days_until = 1
event_regime = "EARNINGS_IMMINENT"

// Step 2: Calculate base score (without earnings)
Momentum: 0.65 × 0.35 = 0.2275
Mean rev: 0.30 × 0.04 = 0.0120
Volume:   0.70 × 0.13 = 0.0910
─────────────────────────────
Base score: 0.33 (WITHOUT earnings influence)

// Step 3: Apply event multipliers
entry_threshold = 0.62 + 0.10 = 0.72
final_score = 0.33 < 0.72 → NO ENTRY ✓

// If score WAS above 0.72:
position_size = 2% × 0.6 = 1.2% (risk reduction)
stop_loss = 50 bps × 1.8 = 90 bps (wider)
max_hold = 240 min ÷ 2 = 120 min (close before announcement)
→ Result: Conservative approach to known risk event!
```

---

## Conclusion

The trading system has a **well-designed foundation**:
- Strategies correctly score entry signals
- Regimes correctly classify market conditions
- Multipliers correctly adjust risk parameters

However:
- **News & Earnings are underutilized as strategies** (0.02-0.15 weight)
- **They should be event detectors**, not scoring signals
- **Multipliers should adjust ALL risk params**, not just stops
- **Event-based regimes** would better capture scheduled risks

This separation would make the system more **robust for event-driven trading** while keeping the core strategy/regime/multiplier architecture clean.

