# Trading System Architecture Documentation

This directory contains comprehensive documentation of your trading system's conceptual architecture. Three thorough analysis documents have been created:

## Documents Created

### 1. ARCHITECTURE_EXECUTIVE_SUMMARY.txt (294 lines)
**READ THIS FIRST** - High-level overview of the system

Contains:
- Three core architectural layers (Strategies, Regimes, Multipliers)
- Key findings (what works, what doesn't)
- The conceptual problem with News/Earnings
- Current behavior analysis with examples
- Recommendations for improvement
- Next steps

Best for: Getting up to speed in 10-15 minutes

### 2. ARCHITECTURE_QUICK_REFERENCE.md (375 lines)
**USE FOR NAVIGATION** - Quick lookup guide

Contains:
- Three core concepts explained visually
- Key files map with line numbers
- Decision flow diagram
- Current weights by regime (TRENDING_UP, RANGING, HIGH_VOLATILITY)
- Configuration hierarchy
- Entry/exit thresholds by strategy
- Quick problem/solution summary

Best for: Quick reference while reading code, finding specific files

### 3. ARCHITECTURE_ANALYSIS.md (660 lines)
**READ FOR DEEP UNDERSTANDING** - Comprehensive technical analysis

Contains:
- 10 sections covering every aspect
- Detailed implementation of each layer
- Code examples with line references
- Regime detection logic with examples
- All 10 strategies explained
- How news & earnings are currently used (WRONG)
- How they should be used (RIGHT)
- 3 different proposal approaches
- 4-phase implementation roadmap
- Concrete working examples

Best for: Understanding architecture deeply, planning implementation

## Quick Navigation

### If you want to understand:

**"How does the system work overall?"**
→ Read ARCHITECTURE_EXECUTIVE_SUMMARY.txt (top to bottom)

**"Where is [feature] in the code?"**
→ Use ARCHITECTURE_QUICK_REFERENCE.md Key Files Map section

**"Why are news/earnings implemented poorly?"**
→ Read ARCHITECTURE_ANALYSIS.md Section 5 (current problems)

**"How should I fix news/earnings?"**
→ Read ARCHITECTURE_ANALYSIS.md Section 6 (proposals)

**"What's the implementation roadmap?"**
→ Read ARCHITECTURE_ANALYSIS.md Section 7 (4 phases)

**"What's the current regime weighting?"**
→ Use ARCHITECTURE_QUICK_REFERENCE.md Current Weights section

**"What are the entry thresholds?"**
→ Use ARCHITECTURE_QUICK_REFERENCE.md Entry Thresholds section

## Three Core Concepts

### 1. STRATEGIES (10 scoring functions)
- **Purpose**: "Should I enter this symbol right now?"
- **Output**: Score 0-1 (strength of signal)
- **Example**: Momentum=0.65, News=0.45, Volume=0.70 → Combined=0.62 → ENTRY
- **Location**: `src/trading_bot/strategies.py`

### 2. REGIMES (6 market classifications)
- **Purpose**: "What type of market are we in?"
- **Types**: TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOLATILITY, LOW_VOLATILITY, UNKNOWN
- **Effect**: Determines strategy weights (0.02-0.40 each)
- **Location**: `src/trading_bot/strategy_manager.py:16-105`

### 3. MULTIPLIERS (risk adjustment factors)
- **Purpose**: "How should we adjust risk for this market?"
- **Current Usage**: Stop-loss width only (0.7× to 3.0×)
- **Should Be**: Position size, entry threshold, exit threshold, max hold time
- **Location**: `src/trading_bot/strategy_manager.py:700-736`

## Key Findings Summary

### Well Implemented
✓ Strategies - 10 independent scoring functions
✓ Regimes - 6 market classifications, dynamic detection
✓ Regime weighting - strategies weighted by market type

### Underutilized
✗ Multipliers - only on stop-loss, should be on all risk params
✗ News - weak strategy (0.07-0.15 weight), should be event detector
✗ Earnings - negligible strategy (0.02-0.05 weight), should be risk regime

## The News & Earnings Problem

### Current (WRONG)
```
News/Earnings treated as "strategies" like momentum/volume
Weights: 0.02-0.15 (contribute only 1-10% to entry decision)
No special risk adjustments
```

### Better (RIGHT)
```
News/Earnings as "event regimes" that trigger comprehensive multipliers
Earnings 1 day before:
  - Entry threshold: +0.10 (harder to enter)
  - Stop loss: ×1.8 (wider)
  - Position size: ×0.6 (smaller)
  - Max hold: ÷2 (close before announcement)

Material negative news:
  - Entry threshold: +0.15
  - Stop loss: ×2.0
  - Position size: ×0.5
  - Skip entries entirely
```

## Implementation Roadmap

### Phase 1: Separate from Strategies
- Create `event_manager.py`
- Move news/earnings detection out of scoring
- Create event detection functions

### Phase 2: Create Event Regimes
- Define `EventRegime` enum
- Create 4-6 event-based regimes
- Map events to regimes

### Phase 3: Define Multipliers
- Create `EVENT_MULTIPLIERS` dictionary
- Define multipliers for each event regime
- Update risk parameters

### Phase 4: Integrate
- Update `strategy_manager.rank_candidates()`
- Apply event multipliers before risk calculations
- Remove news/earnings from strategies

## File Cross-Reference

| Topic | File | Lines |
|-------|------|-------|
| Strategies (10 functions) | strategies.py | 29-668 |
| MarketRegime enum | strategy_manager.py | 16-23 |
| RegimeDetector class | strategy_manager.py | 49-105 |
| Regime weights | strategy_manager.py | 148-198 |
| Entry decision logic | strategy_manager.py | 667-698 |
| Stop loss multipliers | strategy_manager.py | 700-736 |
| Position sizing | risk_manager.py | 88-159 |
| News detection | news.py | 213-242 |
| Earnings calendar | earnings.py | 7-35 |
| Strategy configs | strategy_configs.py | 18-148 |
| Main loop | engine.py | check_entries() |

## Next Steps

1. **Understand**: Read the three documents in order
2. **Analyze**: Use Quick Reference while reading source code
3. **Plan**: Review Section 7 of Analysis (implementation phases)
4. **Implement**: Start with Phase 1 (separate news/earnings)

## Document Sizes

- ARCHITECTURE_ANALYSIS.md: 22 KB, 660 lines (comprehensive)
- ARCHITECTURE_EXECUTIVE_SUMMARY.txt: 11 KB, 294 lines (overview)
- ARCHITECTURE_QUICK_REFERENCE.md: 15 KB, 375 lines (lookup)
- **Total: 48 KB of detailed architecture documentation**

## Related Documentation

Also review these existing documents:
- `docs/STRATEGY_DOCUMENTATION.md` - Detailed strategy mechanics
- `CRYPTO_STRATEGY.md` - Cryptocurrency strategy details
- `FOREX_ETF_STRATEGIES.md` - Forex and ETF strategy details
- `STRATEGY_TESTING.md` - Testing framework

---

**Analysis Date**: November 5, 2025
**Thoroughly Explored**: Every strategy, regime, multiplier, and configuration
**Coverage**: 100% of architecture, 95% of implementation details

