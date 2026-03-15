"""
Configuration — all settings in one place with sensible defaults.
Override via environment variables or a config dict passed at runtime.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BrokerConfig:
    """Alpaca connection settings."""
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://paper-api.alpaca.markets"  # paper by default
    data_url: str = "https://data.alpaca.markets"
    data_feed: str = "iex"  # "iex" (free) or "sip" (paid)

    def __post_init__(self):
        self.api_key = self.api_key or os.getenv("APCA_API_KEY_ID", "")
        self.api_secret = self.api_secret or os.getenv("APCA_API_SECRET_KEY", "")
        self.base_url = os.getenv("APCA_API_BASE_URL", self.base_url)


@dataclass
class ScannerConfig:
    """Pre-market and intraday scanner settings."""
    min_price: float = 5.0
    max_price: float = 500.0
    min_volume: int = 500_000          # minimum avg daily volume
    min_relative_volume: float = 1.5   # today vol / avg vol
    min_gap_pct: float = 2.0           # minimum gap % for Gap & Go
    max_gap_pct: float = 15.0          # avoid blow-off gaps
    min_float: float = 0               # 0 = no filter
    max_candidates: int = 20           # top N to track
    premarket_scan_time: str = "09:00" # ET — when to start scanning
    universe_file: str = ""            # optional CSV of tickers


@dataclass
class StrategyConfig:
    """Per-strategy toggles and parameters."""
    # Opening Range Breakout
    orb_enabled: bool = True
    orb_range_minutes: int = 15        # first 15 min to define range
    orb_min_range_pct: float = 0.3     # minimum range size as % of price
    orb_max_range_pct: float = 3.0     # skip if range is too wide
    orb_volume_confirm: bool = True    # require volume surge on breakout
    orb_confirmation_bars: int = 2     # bars above/below range to confirm

    # VWAP Reversion
    vwap_enabled: bool = True
    vwap_deviation_pct: float = 1.0    # min % away from VWAP to trigger
    vwap_rsi_oversold: float = 30.0    # RSI threshold for long entry
    vwap_rsi_overbought: float = 70.0  # RSI threshold for short entry
    vwap_bb_period: int = 20           # Bollinger Band lookback
    vwap_bb_std: float = 2.0           # Bollinger Band std devs

    # Gap & Go
    gap_enabled: bool = True
    gap_min_pct: float = 3.0           # minimum gap to qualify
    gap_volume_surge: float = 2.0      # volume must be Nx average
    gap_first_pullback: bool = True    # wait for first pullback to enter
    gap_max_entry_minutes: int = 60    # stop looking after N min

    # General
    bar_size: str = "1Min"             # candle size for signals
    lookback_days: int = 20            # historical bars for indicators
    max_trades_per_strategy: int = 3   # concurrent trades per strategy


@dataclass
class RiskConfig:
    """Risk management parameters."""
    # Position sizing
    risk_per_trade_pct: float = 1.0    # % of equity risked per trade
    max_position_pct: float = 10.0     # max single position as % of equity
    max_positions: int = 6             # max concurrent positions
    max_total_exposure_pct: float = 60.0

    # Stop losses
    default_stop_atr_multiple: float = 1.5  # stop = entry ± N × ATR
    trailing_stop_enabled: bool = True
    trailing_stop_atr_multiple: float = 2.0
    hard_stop_pct: float = 3.0        # absolute max loss per trade

    # Take profit
    take_profit_rr_ratio: float = 2.0  # reward:risk target
    partial_exit_pct: float = 50.0     # sell half at 1R, rest at 2R
    partial_exit_at_rr: float = 1.0    # R-multiple for first exit

    # Daily limits
    daily_loss_limit_pct: float = 2.0  # stop trading after N% loss
    max_daily_trades: int = 30
    close_all_eod: bool = True
    eod_minutes_before_close: int = 15

    # Time filters
    no_trade_first_minutes: int = 0    # optional: wait N min after open
    no_trade_last_minutes: int = 30    # stop new entries before close


@dataclass
class BacktestConfig:
    """Backtesting parameters."""
    initial_capital: float = 100_000.0
    commission_per_share: float = 0.0  # Alpaca is commission-free
    slippage_bps: float = 3.0          # estimated slippage in basis points
    fill_delay_seconds: int = 1        # simulated fill delay


@dataclass
class Config:
    """Top-level configuration container."""
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    # Logging
    log_level: str = "INFO"
    log_file: str = "daytrader.log"

    # Mode
    paper_trading: bool = True
    dry_run: bool = False  # if True, generate signals but don't submit orders

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """Build Config from a flat or nested dictionary."""
        cfg = cls()
        for section_name in ("broker", "scanner", "strategy", "risk", "backtest"):
            section_data = d.get(section_name, {})
            section_obj = getattr(cfg, section_name)
            for k, v in section_data.items():
                if hasattr(section_obj, k):
                    setattr(section_obj, k, v)
        for k in ("log_level", "log_file", "paper_trading", "dry_run"):
            if k in d:
                setattr(cfg, k, d[k])
        return cfg
