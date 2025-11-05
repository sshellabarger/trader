# Trading System Architecture: Quick Reference Guide

## Three Core Concepts

### 1. STRATEGIES - Entry Signal Strength (0-1 score)
**Purpose:** Should we trade this symbol right now?
**Location:** `src/trading_bot/strategies.py`
**Count:** 10 scoring functions

```
AAPL → [Momentum: 0.65, MeanRev: 0.30, News: 0.45, Volume: 0.70, ...]
       → Combined: 0.62 (above 0.62 threshold? YES → Entry!)
```

**The 10 Strategies:**
1. `score_momentum()` - Intraday trend (fast)
2. `score_mean_reversion()` - Oversold bounce
3. `score_news()` - Sentiment analysis
4. `score_volume()` - Volume confirmation
5. `score_earnings()` - Days until earnings
6. `score_longterm_trend()` - Sustained moves
7. `score_longterm_momentum()` - Gap + momentum
8. `score_crypto()` - Crypto specialized
9. `score_forex()` - FX specialized
10. `score_etf()` - ETF specialized

---

### 2. REGIMES - Market Condition Type
**Purpose:** What type of market are we in? (affects strategy weights)
**Location:** `src/trading_bot/strategy_manager.py` (lines 16-105)
**Count:** 6 classifications

```
AAPL: Price $102.50, Open $100, Prev $99 → TRENDING_UP Regime
      → Momentum weight: 0.30 (highest)
      → Mean reversion weight: 0.04 (lowest)
```

**The 6 Regimes:**
1. `TRENDING_UP` - Strong upward movement
2. `TRENDING_DOWN` - Strong downward movement
3. `RANGING` - Sideways/consolidation
4. `HIGH_VOLATILITY` - Large swings (>3% or gap >2%)
5. `LOW_VOLATILITY` - Tight range (<0.5%)
6. `UNKNOWN` - Mixed/unclear signals

**Regime Detection Logic:**
```python
# File: strategy_manager.py:55-105
def detect_regime(current, open, prev_close, high, low):
    # Input: 5 prices
    # Output: MarketRegime enum
    # Algorithm: Check trend + volatility patterns
```

---

### 3. MULTIPLIERS - Risk Parameter Adjusters
**Purpose:** Adjust risk parameters for current conditions
**Location:** `src/trading_bot/strategy_manager.py` (lines 700-736)
**Current Usage:** Stop loss width ONLY

```
Entry: $100, Regime: TRENDING_UP
Base Stop: 50 bps (0.5%)
Multiplier: 0.8 (tighter in trends)
Adjusted Stop: 50 × 0.8 = 40 bps (0.4%)
Final Stop Price: $99.60

Entry: $100, Regime: HIGH_VOLATILITY + Crypto
Multiplier: 1.5 × 2.0 = 3.0
Adjusted Stop: 50 × 3.0 = 150 bps (1.5%)
Final Stop Price: $98.50
```

**Current Regime Multipliers:**
| Regime | Multiplier | Stop Width |
|--------|-----------|-----------|
| TRENDING_UP | 0.8x | 40 bps |
| TRENDING_DOWN | 1.2x | 60 bps |
| RANGING | 1.0x | 50 bps |
| HIGH_VOLATILITY | 1.5x | 75 bps |
| LOW_VOLATILITY | 0.7x | 35 bps |
| + Crypto | ×2.0 | (doubles all) |

---

## Key Files Map

```
src/trading_bot/
├── strategy_manager.py      ← RegimeDetector + StrategyManager
│   ├── MarketRegime enum (lines 16-23)
│   ├── RegimeDetector class (lines 49-105)
│   ├── regime_weights dict (lines 148-198)
│   ├── regime_multipliers dict (lines 718-726)
│   └── rank_candidates() method (lines 439-665)
│
├── strategies.py            ← 10 scoring functions
│   ├── score_momentum() (lines 29-58)
│   ├── score_mean_reversion() (lines 61-90)
│   ├── score_news() (lines 93-181)
│   ├── score_volume() (lines 184-205)
│   ├── score_earnings() (lines 208-230)
│   ├── score_longterm_trend() (lines 233-257)
│   ├── score_longterm_momentum() (lines 260-293)
│   ├── score_crypto() (lines 296-416)
│   ├── score_forex() (lines 419-545)
│   └── score_etf() (lines 548-668)
│
├── strategy_configs.py      ← Per-strategy parameters
│   ├── STRATEGY_CONFIGS dict
│   └── get_strategy_config() function
│
├── risk_manager.py          ← Position sizing, stops, risk checks
│   ├── calculate_position_size() (lines 88-159)
│   ├── check_stop_losses() (lines 161-332)
│   ├── check_take_profit() (lines 334-367)
│   └── validate_order() (lines 369-428)
│
├── news.py                  ← Article fetching + sentiment
│   ├── NewsArticle dataclass
│   ├── analyze_sentiment() (lines 172-202)
│   ├── analyze_articles_sentiment() (lines 204-210)
│   └── get_news_articles() (lines 213-242)
│
├── earnings.py              ← Earnings calendar fetching
│   └── fetch_earnings_calendar() (lines 7-35)
│
├── engine.py                ← Main trading loop
│   └── check_entries() / _refresh_candidates() methods
│
└── settings.py              ← Configuration
    ├── scheduling dict
    ├── strategies dict
    ├── thresholds dict
    └── (asset-specific: crypto, forex, etf)
```

---

## Decision Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ MARKET OPENS - Check Candidates Every 30 Minutes           │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ For Each Symbol in Universe:                               │
│  1. Get current price, volume, previous close              │
│  2. Fetch news articles + sentiment                        │
│  3. Check earnings calendar                                │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 1: DETECT REGIME                                      │
│ Input: price_current, open, prev_close, high, low          │
│ Output: MarketRegime enum (TRENDING_UP, etc.)             │
│ File: strategy_manager.py:55-105                           │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 2: SCORE WITH STRATEGIES                              │
│ For each of 10 strategies:                                 │
│   Score = strategy_function(symbol, market_data)           │
│ Output: List[SignalResult] with scores 0-1                │
│ File: strategies.py + strategy_manager.py:208-400         │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 3: APPLY REGIME WEIGHTS                               │
│ Select only regime-matching strategies                     │
│ Weight each by: strategy_confidence (0.6-0.75)            │
│ Final score = weighted_average(matching_scores)            │
│ File: strategy_manager.py:608-640                          │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│ STEP 4: ENTRY DECISION                                     │
│ Is final_score >= 0.62? → YES                             │
│ Do 2+ strategies match? → YES                              │
│ Is confidence >= 0.3? → YES                                │
│ File: strategy_manager.py:667-698                          │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼ YES, ALL CHECKS PASS
         │
┌─────────────────────────────────────────────────────────────┐
│ STEP 5: RISK MANAGEMENT                                    │
│ a) Calculate position size (risk = 1% of account)          │
│    File: risk_manager.py:88-159                            │
│                                                             │
│ b) Calculate stop loss (with regime multiplier)            │
│    Base: 50 bps × regime_multiplier                        │
│    File: strategy_manager.py:700-736                       │
│                                                             │
│ c) Validate order (buying power, max positions, etc.)     │
│    File: risk_manager.py:369-428                           │
└────────┬────────────────────────────────────────────────────┘
         │
         ▼ ALL CHECKS PASS
         │
┌─────────────────────────────────────────────────────────────┐
│ STEP 6: PLACE ORDER                                        │
│ Market order: BUY quantity @ current price                 │
│ Log trade with details                                     │
│ File: engine.py                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## Current Weights by Regime

### TRENDING_UP
```
momentum:           0.30 ✓ HIGHEST
longterm_trend:     0.20
volume:             0.13
longterm_momentum:  0.09
news:               0.07
forex:              0.08
crypto:             0.05
etf:                0.04
mean_reversion:     0.04
earnings:           0.02  ✓ LOWEST
```

### RANGING
```
mean_reversion:     0.40 ✓ HIGHEST (buy dips)
news:               0.15 (catalyst)
longterm_trend:     0.13
volume:             0.09
forex:              0.10
crypto:             0.05
etf:                0.04
longterm_momentum:  0.04
momentum:           0.04
earnings:           0.04
```

### HIGH_VOLATILITY
```
volume:             0.22 ✓ HIGHEST (validation)
momentum:           0.18
mean_reversion:     0.13
news:               0.13
forex:              0.08
longterm_trend:     0.07
longterm_momentum:  0.06
crypto:             0.05
earnings:           0.04
etf:                0.04
```

---

## Configuration Hierarchy

```
DEFAULT (hardcoded in strategy_manager.py)
    ↓
SETTINGS (src/trading_bot/settings.py)
    ↓
STRATEGY_CONFIGS (src/trading_bot/strategy_configs.py)
    ↓
MULTIPLIERS (regime-based adjustments)
    ↓
RUNTIME KV OVERRIDES (via API /settings endpoint)
    ↓
FINAL PARAMETERS APPLIED
```

**Example - Entry Threshold:**
1. Default: 0.62 (hardcoded)
2. From settings: 0.62 (unchanged)
3. Strategy-specific (momentum): 0.65 from strategy_configs.py
4. Regime-based: applies weights, not thresholds
5. Runtime override: user POSTs new value

---

## News & Earnings Problem

### Current (as Strategies)
```
News Strategy:
  ├─ Weight: 0.07-0.15 (varies by regime)
  ├─ Score: sentiment (-0.5 to +0.5) + volume (0 to 0.5)
  ├─ Problem: Weak signal, competes with momentum/volume
  └─ Effect: ~5-10% of final score

Earnings Strategy:
  ├─ Weight: 0.02-0.05 (very low)
  ├─ Score: 0-1 based on days until earnings
  ├─ Problem: Negligible influence
  └─ Effect: ~1% of final score
```

### Better (as Event Regimes + Multipliers)
```
Earnings Event Regime:
  ├─ Trigger: 0-7 days until announced
  ├─ Effect: EARNINGS_IMMINENT regime
  └─ Multipliers:
     ├─ Entry threshold: +0.10 (0.72 instead of 0.62)
     ├─ Stop loss: ×1.8 (90 bps instead of 50)
     ├─ Position size: ×0.6 (smaller positions)
     └─ Max hold: ÷2 (close before announcement)

News Volatility:
  ├─ Trigger: Material news detected
  ├─ Effect: HIGH_VOLATILITY_EVENT override
  └─ Multipliers:
     ├─ Entry threshold: +0.15 (0.77 for negative)
     ├─ Stop loss: ×2.0 (100 bps)
     ├─ Position size: ×0.5 (conservative)
     └─ Risk management: Stricter checks
```

---

## Key Metrics by Strategy

| Strategy | Best Regime | Weight Range | Typical Score |
|----------|-------------|--------------|---------------|
| Momentum | TRENDING_UP | 0.04-0.35 | 0.4-0.8 |
| Mean Rev | RANGING | 0.04-0.40 | 0.2-0.7 |
| News | RANGING | 0.07-0.15 | 0.0-0.8 |
| Volume | HIGH_VOL | 0.09-0.22 | 0.3-0.9 |
| Earnings | RANGING | 0.02-0.05 | 0.0-1.0 |
| LT Trend | TRENDING | 0.07-0.20 | 0.0-1.0 |
| LT Mom | TRENDING_UP | 0.04-0.10 | 0.0-0.9 |
| Crypto | ANY | 0.05-0.05 | 0.2-0.8 |
| Forex | RANGING | 0.08-0.10 | 0.2-0.7 |
| ETF | TRENDING | 0.04-0.04 | 0.2-0.8 |

---

## Entry Thresholds by Strategy

| Strategy | Entry Threshold | Exit Threshold | Stop Loss | Take Profit |
|----------|-----------------|---------------|---------|-----------
| Momentum | 0.65 | 0.40 | 0.8% | 2.5% |
| Mean Rev | 0.60 | 0.35 | 1.0% | 1.5% |
| News | 0.55 | 0.30 | 1.2% | 3.0% |
| Volume | 0.50 | 0.30 | 0.7% | 2.0% |
| Earnings | 0.70 | 0.40 | 1.5% | 4.0% |
| LT Trend | 0.58 | 0.35 | 1.5% | 5.0% |
| LT Mom | 0.62 | 0.38 | 1.3% | 4.5% |
| Crypto | 0.60 | 0.40 | 2.5% | 6.0% |
| Forex | 0.62 | 0.38 | 0.5% | 1.2% |
| ETF | 0.58 | 0.35 | 1.0% | 2.5% |

---

## To Understand Further

1. **Strategy Scoring**: Read `/home/user/trader/docs/STRATEGY_DOCUMENTATION.md`
2. **Crypto Details**: Read `/home/user/trader/CRYPTO_STRATEGY.md`
3. **Forex/ETF**: Read `/home/user/trader/FOREX_ETF_STRATEGIES.md`
4. **Full Analysis**: Read `/home/user/trader/ARCHITECTURE_ANALYSIS.md`

---

**Last Updated:** 2025-11-05
**Analysis Date:** Current session
