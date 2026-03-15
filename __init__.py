"""
DayTrader v2 — Stock Day Trading System
Goal: Maximize daily profits from intraday stock trading.

Strategies:
  - Opening Range Breakout (ORB)
  - VWAP Mean Reversion
  - Gap & Go Momentum

Architecture:
  config     → settings, thresholds, environment
  broker     → Alpaca API wrapper
  data       → bar fetching, streaming, caching
  indicators → VWAP, EMA, RSI, Bollinger Bands, ATR
  scanner    → pre-market gapper + intraday candidate scanner
  strategies → modular strategy classes with entry/exit signals
  risk       → position sizing, stop losses, exposure limits
  engine     → main loop orchestrating scan → signal → validate → execute
  backtest   → historical simulation with realistic fills
  journal    → trade logging, daily P&L, strategy attribution
"""

__version__ = "2.0.0"
