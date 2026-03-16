"""
Configuration — ETF-focused day trading on TQQQ.
Based on Zarattini & Aziz (2023) ORB + VWAP research.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from .dotenv import load_dotenv
_env_loaded = load_dotenv()


@dataclass
class BrokerConfig:
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://paper-api.alpaca.markets"
    data_url: str = "https://data.alpaca.markets"
    data_feed: str = "iex"

    def __post_init__(self):
        self.api_key = self.api_key or os.getenv("APCA_API_KEY_ID", "")
        self.api_secret = self.api_secret or os.getenv("APCA_API_SECRET_KEY", "")
        self.base_url = os.getenv("APCA_API_BASE_URL", self.base_url)
        self.data_feed = os.getenv("APCA_DATA_FEED", self.data_feed)


@dataclass
class ScannerConfig:
    """Minimal — ETF mode doesn't need full scanner."""
    min_price: float = 1.0
    max_price: float = 10000.0
    min_volume: int = 100_000
    min_relative_volume: float = 0.5
    min_gap_pct: float = 2.0
    max_gap_pct: float = 15.0
    min_float: float = 0
    max_candidates: int = 5
    premarket_scan_time: str = "09:00"
    universe_file: str = ""


@dataclass
class StrategyConfig:
    """ETF-focused strategy settings."""

    primary_symbol: str = "QQQ"
    leveraged_bull: str = "TQQQ"
    leveraged_bear: str = "SQQQ"
    use_leveraged: bool = True

    # Opening Range Breakout (5-minute ORB per Zarattini/Aziz paper)
    orb_enabled: bool = True
    orb_range_minutes: int = 5
    orb_trade_both_directions: bool = True  # long on bullish days, short on bearish days
    orb_profit_target_r: float = 10.0
    orb_exit_at_close: bool = True
    orb_min_range_dollars: float = 0.10
    orb_max_range_atr_ratio: float = 3.0

    # VWAP Reversion
    vwap_enabled: bool = True
    vwap_deviation_pct: float = 0.5
    vwap_rsi_oversold: float = 30.0
    vwap_rsi_overbought: float = 70.0
    vwap_bb_period: int = 20
    vwap_bb_std: float = 2.0
    vwap_require_bullish_regime: bool = True
    vwap_regime_ema_period: int = 20

    # General
    bar_size: str = "1Min"
    block_reentry_after_stop: bool = True
    max_trades_per_strategy: int = 1
    vwap_regime_symbol: str = "QQQ"


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 3.0
    max_position_pct: float = 80.0
    max_positions: int = 2
    max_total_exposure_pct: float = 90.0

    default_stop_atr_multiple: float = 2.5
    min_stop_pct: float = 0.3
    trailing_stop_enabled: bool = False
    trailing_stop_atr_multiple: float = 3.0
    hard_stop_pct: float = 5.0

    take_profit_rr_ratio: float = 10.0
    partial_exit_pct: float = 0.0
    partial_exit_at_rr: float = 0.0
    max_risk_dollars: float = 3000.0

    daily_loss_limit_pct: float = 6.0
    max_daily_trades: int = 10
    close_all_eod: bool = True
    eod_minutes_before_close: int = 5

    no_trade_first_minutes: int = 0
    no_trade_last_minutes: int = 15


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    commission_per_share: float = 0.0035
    slippage_bps: float = 2.0


@dataclass
class Config:
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    log_level: str = "INFO"
    log_file: str = "daytrader.log"
    paper_trading: bool = True
    dry_run: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
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

    def get_trading_symbols(self) -> List[str]:
        if self.strategy.use_leveraged:
            return [self.strategy.leveraged_bull]
        return [self.strategy.primary_symbol]
