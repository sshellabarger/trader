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
    # ORB is long-only per instrument. "Both directions" is realized by also
    # trading the inverse ETF (leveraged_bear): a down-Nasdaq day breaks SQQQ
    # UP, so we go long SQQQ instead of shorting TQQQ.
    #
    # DEFAULT OFF (2026-06-13 diagnosis): the SQQQ bear leg has negative
    # expectancy in every coherent backtest slice analysed (full-history union:
    # n=56, -$868, 21% win, PF 0.97; H1-2025: -$1,135, 21% win). It is an
    # unhedged counter-trend long that loses through the Nasdaq's upward drift,
    # and it drove most of the H1-2025 drawdown. The coherent TQQQ-only run
    # (2025-01-02..2026-03-10) was +29.0%, PF 1.64, Sharpe 2.75, max DD 2.6% —
    # so trading the bull instrument alone is the strong, validated profile.
    # Re-enable ONLY behind regime alignment (orb_require_regime_alignment) so
    # the bear leg trades exclusively in confirmed bearish regimes, and only
    # once that combination is validated out-of-sample on fresh data.
    orb_trade_both_directions: bool = False  # include leveraged_bear in the trading set
    orb_entry_window_minutes: int = 3  # how long after the range completes an entry is allowed
    orb_min_range_bars: int = 3  # require this many 1-min bars inside the opening range
    orb_profit_target_r: float = 10.0
    orb_exit_at_close: bool = True
    orb_min_range_dollars: float = 0.10
    orb_max_range_atr_ratio: float = 3.0
    # Opening-range size as a % of price. H1-2025 diagnosis: breakouts with an
    # opening range < ~0.5% of price are noise (0/11 winners) and oversized
    # ranges are high-volatility whipsaw days that blow through the range-low
    # stop. Default band 0.5-1.2% turns H1-2025 from PF 0.87 (-9%) to PF 1.22
    # (+8.3%). A tighter 0.5-1.0% cap scores higher in-sample (PF 1.52) but is
    # more curve-fit to that window; widen/tighten per out-of-sample results.
    # Set max=0 to disable the upper cap.
    orb_min_range_pct: float = 0.5
    orb_max_range_pct: float = 1.2
    # Optional: only take an ORB breakout that agrees with the daily regime
    # (long the bull ETF only when QQQ is bullish, the bear ETF only when
    # bearish). Default off — kept inert until validated out-of-sample.
    orb_require_regime_alignment: bool = False

    # Optional: align ORB long entries with the overnight drift. The Nasdaq's
    # return accrues mostly overnight (close->open); the intraday session ORB
    # trades in is the weaker leg (QQQ 2010-2026 backtest: overnight Sharpe
    # ~0.75 vs intraday ~0.27 — see research/overnight_drift_backtest.py). This
    # gate skips an ORB long on days the market gapped DOWN overnight, i.e. when
    # the overnight drift ran against the trade. The signal is the regime
    # symbol's (QQQ) prior-close -> today-open move, supplied to the strategy as
    # indicators["overnight_gap_pct"]. If that value is absent the gate is INERT
    # (fails open) so a missing-data day never silently blocks every trade.
    # Default OFF — a testable hypothesis; validate out-of-sample before use.
    # This is the momentum-alignment direction; the mean-reversion variant
    # (fade a down gap) is its inverse and would gate on a maximum gap instead.
    orb_require_overnight_alignment: bool = False
    orb_overnight_gap_min_pct: float = 0.0  # min overnight gap % to allow a long

    # Optional: instead of flattening at the close, carry a WINNING ORB position
    # (in profit at the close) past the close and exit it at the NEXT session's
    # open, capturing the overnight drift (the index's strongest leg — see
    # research/overnight_drift_backtest.py). Losers and non-ORB positions still
    # flatten at EOD. This adds real overnight gap risk: a bad open fills at the
    # open, not the stop. Default OFF — validate against the exit-at-close
    # baseline and buy&hold QQQ before enabling. Backtester support only for now;
    # the live engine still flattens EOD until this is wired and validated there.
    orb_hold_overnight: bool = False

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

    def __post_init__(self):
        # Let the deployed bot toggle the overnight experiments from the
        # environment (.env) without a code change, mirroring how the broker
        # keys are configured. Code defaults stay off.
        def _envbool(name: str, current: bool) -> bool:
            v = os.getenv(name)
            if v is None:
                return current
            return v.strip().lower() in ("1", "true", "yes", "on")

        self.orb_require_overnight_alignment = _envbool(
            "ORB_REQUIRE_OVERNIGHT_ALIGNMENT", self.orb_require_overnight_alignment)
        self.orb_hold_overnight = _envbool(
            "ORB_HOLD_OVERNIGHT", self.orb_hold_overnight)
        g = os.getenv("ORB_OVERNIGHT_GAP_MIN_PCT")
        if g is not None:
            try:
                self.orb_overnight_gap_min_pct = float(g)
            except ValueError:
                pass


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
    # Buying power as a multiple of equity, to mirror the live margin account
    # (Alpaca paper RegT ~2x). The old hardcoded 0.5 under-sized vs live.
    margin_multiple: float = 2.0


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
        """Symbols the engine actually trades.

        In leveraged mode we trade the bull ETF (long on up days) and, when
        both-directions is on, also the bear ETF (long on down days) so the
        bot profits whether the Nasdaq rises or falls — without shorting.
        """
        if self.strategy.use_leveraged:
            symbols = [self.strategy.leveraged_bull]
            if self.strategy.orb_trade_both_directions:
                symbols.append(self.strategy.leveraged_bear)
            return symbols
        return [self.strategy.primary_symbol]

    def vwap_symbols(self) -> List[str]:
        """Symbols VWAP reversion may trade.

        Only the bull instrument: the regime filter is keyed to QQQ, so
        running mean-reversion on the inverse ETF would invert the
        falling-knife protection.
        """
        if self.strategy.use_leveraged:
            return [self.strategy.leveraged_bull]
        return [self.strategy.primary_symbol]
