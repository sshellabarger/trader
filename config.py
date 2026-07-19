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


# ---------------------------------------------------------------------------
# Small env helpers. The deployed bot is configured entirely through .env, so
# the operator can tune toggles and risk caps on the droplet without a code
# change. Each returns the current default unchanged when the var is unset or
# unparseable, so a typo never silently zeroes a limit.
# ---------------------------------------------------------------------------

def _env_bool(name: str, current: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return current
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, current: int) -> int:
    v = os.getenv(name)
    if v is None:
        return current
    try:
        return int(v)
    except ValueError:
        return current


def _env_float(name: str, current: float) -> float:
    v = os.getenv(name)
    if v is None:
        return current
    try:
        return float(v)
    except ValueError:
        return current


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
    #
    # 2026-06-21: enabled at user request to trade the SQQQ long leg.
    # 2026-07-01: DISABLED again, per user decision after the honest review —
    # the negative-expectancy finding was never invalidated (walk-forward OOS
    # tested the whole system at PF 0.99, and every coherent SQQQ slice is
    # negative). Re-enable only after an out-of-sample backtest run with
    # BACKTEST_ENTRY_FILL_NEXT_OPEN=true and BACKTEST_SLIPPAGE_BPS=10 shows
    # the leg adds money, ideally paired with orb_require_regime_alignment.
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
    # Falling-knife guard: refuse a reversion entry while price is still
    # extending its drop. Requires the latest bar to show the down-move has
    # paused (close >= prior close, or a higher low than the prior N bars).
    # The daily regime filter can't see an intraday selloff inside a bullish
    # day; this can.
    vwap_require_entry_confirmation: bool = True
    vwap_confirmation_lookback: int = 3

    # General
    bar_size: str = "1Min"
    block_reentry_after_stop: bool = True
    max_trades_per_strategy: int = 1
    vwap_regime_symbol: str = "QQQ"

    # News sentiment filter (OFF by default; see trader/news.py). Changes
    # nothing live until news_enabled is set. Intended as an entry gate once
    # validated against a backtest A/B.
    news_enabled: bool = False
    news_window_min: int = 120
    news_block_below: float = -0.35
    news_min_articles: int = 2
    news_symbols: tuple = ("QQQ",)

    # ── Stock sleeve (stocks-only, scanner-driven) ──────────────────────
    # When enabled the engine sets the index instruments aside and instead
    # day-trades a basket of individual high-growth stocks chosen each morning
    # by the scanner (premarket gappers/movers from the high-growth universe),
    # using the same long-only ORB breakout, bracket stops, and EOD flatten.
    #
    # DEFAULT OFF: the code ships completely inert. The bot keeps trading the
    # validated TQQQ/SQQQ index profile until the operator sets
    # STOCK_SLEEVE_ENABLED=true in .env. Intended for the Alpaca PAPER account.
    stock_sleeve_enabled: bool = False
    # Universe categories to scan (comma-separated names from universe.UNIVERSE).
    # Default is "liquid_movers": high-beta names that still trade enough volume
    # for the FREE IEX feed to print intraday bars. The older
    # "tech_volatile,volatile_movers" pool includes low-priced / low-float names
    # (e.g. BITF) that IEX barely covers, so the sleeve got no bars and never
    # traded — only use those on the paid SIP feed (APCA_DATA_FEED=sip).
    stock_sleeve_universe: str = "liquid_movers"
    # Optional explicit symbol list (comma-separated). When set it OVERRIDES the
    # categories above — handy for a curated watchlist or a tight test.
    stock_sleeve_symbols: str = ""
    # Optional path to a screened pool file (written by
    # `python -m trader screen-universe`). When set and readable it is the scan
    # universe, so the pool stays current instead of using the static categories.
    stock_sleeve_pool_file: str = ""
    # Top-N scanner candidates to actually trade each day.
    stock_sleeve_max_candidates: int = 5
    # Most stock positions open at once (the sleeve's concurrency cap; in
    # stocks-only mode this becomes the engine's effective max_positions).
    stock_sleeve_max_positions: int = 3
    # Per-name position cap as % of equity. Smaller than the 80% single-index
    # cap because the sleeve spreads across several names.
    stock_sleeve_max_position_pct: float = 25.0

    # ORB entries allowed per day. Default 1 preserves the index profile exactly
    # (one breakout/day across TQQQ+SQQQ → no delta-neutral straddle). The stock
    # sleeve raises this so several names can break out the same morning.
    orb_max_entries_per_day: int = 1

    # Long-bias the sleeve's morning picks: the sleeve trades LONG opening-range
    # breakouts only, and a stock that gaps DOWN a few percent almost never sets
    # up one — the 07-06→07-17 week produced 45 name-days and a single entry
    # because most picks were gap-downs. When true, the scanner ranks gap-ups
    # first and the sleeve trades only non-negative gaps (fewer names on a red
    # tape is the correct long-only behavior). Set STOCK_SLEEVE_LONG_BIAS=false
    # to restore the old direction-agnostic picks.
    stock_sleeve_long_bias: bool = True

    # ── Stock-sleeve news / catalysts (default OFF) ─────────────────────
    # When on, each morning the sleeve pulls recent MARKET-WIDE news, adds the
    # top catalyst names (most fresh coverage) to the scan pool, and gates
    # breakout longs away from strongly-negative headlines. Off = no news calls.
    stock_sleeve_news_enabled: bool = False
    stock_sleeve_news_lookback_min: int = 1080    # hot-list window (~18h: overnight + premarket)
    stock_sleeve_news_hotlist: int = 10           # catalyst names added to the pool
    stock_sleeve_news_min_articles: int = 2       # min articles to count as a catalyst
    stock_sleeve_news_max_pages: int = 40         # market-wide fetch page cap (50/page)
    stock_sleeve_news_gate_window_min: int = 120  # lookback for the entry gate
    stock_sleeve_news_block_below: float = -0.4   # block a long when mean sentiment <= this
    stock_sleeve_news_gate_min_articles: int = 2  # min coverage before the gate acts

    def __post_init__(self):
        # Let the deployed bot toggle experiments and the stock sleeve from the
        # environment (.env) without a code change, mirroring how the broker
        # keys are configured. Code defaults stay off.
        self.orb_require_overnight_alignment = _env_bool(
            "ORB_REQUIRE_OVERNIGHT_ALIGNMENT", self.orb_require_overnight_alignment)
        self.orb_hold_overnight = _env_bool(
            "ORB_HOLD_OVERNIGHT", self.orb_hold_overnight)
        # A/B the falling-knife guard from the env without a code change.
        self.vwap_require_entry_confirmation = _env_bool(
            "VWAP_REQUIRE_ENTRY_CONFIRMATION", self.vwap_require_entry_confirmation)
        self.news_enabled = _env_bool("NEWS_ENABLED", self.news_enabled)
        self.orb_overnight_gap_min_pct = _env_float(
            "ORB_OVERNIGHT_GAP_MIN_PCT", self.orb_overnight_gap_min_pct)

        # Stock sleeve (default off — see the field comments above).
        self.stock_sleeve_enabled = _env_bool(
            "STOCK_SLEEVE_ENABLED", self.stock_sleeve_enabled)
        self.stock_sleeve_universe = os.getenv(
            "STOCK_SLEEVE_UNIVERSE", self.stock_sleeve_universe)
        self.stock_sleeve_symbols = os.getenv(
            "STOCK_SLEEVE_SYMBOLS", self.stock_sleeve_symbols)
        self.stock_sleeve_pool_file = os.getenv(
            "STOCK_SLEEVE_POOL_FILE", self.stock_sleeve_pool_file)
        self.stock_sleeve_max_candidates = _env_int(
            "STOCK_SLEEVE_MAX_CANDIDATES", self.stock_sleeve_max_candidates)
        self.stock_sleeve_max_positions = _env_int(
            "STOCK_SLEEVE_MAX_POSITIONS", self.stock_sleeve_max_positions)
        self.stock_sleeve_max_position_pct = _env_float(
            "STOCK_SLEEVE_MAX_POSITION_PCT", self.stock_sleeve_max_position_pct)
        self.orb_max_entries_per_day = _env_int(
            "ORB_MAX_ENTRIES_PER_DAY", self.orb_max_entries_per_day)
        self.stock_sleeve_long_bias = _env_bool(
            "STOCK_SLEEVE_LONG_BIAS", self.stock_sleeve_long_bias)

        # Stock-sleeve news layer (default off — see the field comments above).
        self.stock_sleeve_news_enabled = _env_bool(
            "STOCK_SLEEVE_NEWS_ENABLED", self.stock_sleeve_news_enabled)
        self.stock_sleeve_news_lookback_min = _env_int(
            "STOCK_SLEEVE_NEWS_LOOKBACK_MIN", self.stock_sleeve_news_lookback_min)
        self.stock_sleeve_news_hotlist = _env_int(
            "STOCK_SLEEVE_NEWS_HOTLIST", self.stock_sleeve_news_hotlist)
        self.stock_sleeve_news_min_articles = _env_int(
            "STOCK_SLEEVE_NEWS_MIN_ARTICLES", self.stock_sleeve_news_min_articles)
        self.stock_sleeve_news_max_pages = _env_int(
            "STOCK_SLEEVE_NEWS_MAX_PAGES", self.stock_sleeve_news_max_pages)
        self.stock_sleeve_news_gate_window_min = _env_int(
            "STOCK_SLEEVE_NEWS_GATE_WINDOW_MIN", self.stock_sleeve_news_gate_window_min)
        self.stock_sleeve_news_block_below = _env_float(
            "STOCK_SLEEVE_NEWS_BLOCK_BELOW", self.stock_sleeve_news_block_below)
        self.stock_sleeve_news_gate_min_articles = _env_int(
            "STOCK_SLEEVE_NEWS_GATE_MIN_ARTICLES", self.stock_sleeve_news_gate_min_articles)


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

    def __post_init__(self):
        # Risk caps are tunable from .env on the droplet (no code change), so a
        # stock-sleeve deploy can be bounded without editing the image. Defaults
        # are unchanged when the vars are unset.
        self.risk_per_trade_pct = _env_float("RISK_PER_TRADE_PCT", self.risk_per_trade_pct)
        self.max_position_pct = _env_float("MAX_POSITION_PCT", self.max_position_pct)
        self.max_positions = _env_int("MAX_POSITIONS", self.max_positions)
        self.max_total_exposure_pct = _env_float(
            "MAX_TOTAL_EXPOSURE_PCT", self.max_total_exposure_pct)
        self.max_risk_dollars = _env_float("MAX_RISK_DOLLARS", self.max_risk_dollars)
        self.daily_loss_limit_pct = _env_float(
            "DAILY_LOSS_LIMIT_PCT", self.daily_loss_limit_pct)
        self.max_daily_trades = _env_int("MAX_DAILY_TRADES", self.max_daily_trades)


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    commission_per_share: float = 0.0035
    # Per-side slippage. 2 bps models a resting-liquidity fill; live entries
    # are MARKET orders sent from a 30s poll loop, so for go/no-go decisions
    # stress the result at 5-10 bps (BACKTEST_SLIPPAGE_BPS=10). A strategy
    # whose edge disappears at 10 bps never had one.
    slippage_bps: float = 2.0
    # Buying power as a multiple of equity, to mirror the live margin account
    # (Alpaca paper RegT ~2x). The old hardcoded 0.5 under-sized vs live.
    margin_multiple: float = 2.0
    # Honest entry timing: fill a signal at the NEXT bar's open instead of the
    # signal bar's close. The live bot sees a completed bar and then sends a
    # market order, so next-open is the earliest price it can actually get;
    # same-bar-close fills flatter every backtest by ~0.5-1.5 min of drift on
    # a 3x ETF. Default off to keep comparability with historical runs — turn
    # on (BACKTEST_ENTRY_FILL_NEXT_OPEN=true) for any decision-grade result.
    entry_fill_next_open: bool = False

    def __post_init__(self):
        self.slippage_bps = _env_float("BACKTEST_SLIPPAGE_BPS", self.slippage_bps)
        self.entry_fill_next_open = _env_bool(
            "BACKTEST_ENTRY_FILL_NEXT_OPEN", self.entry_fill_next_open)


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

    def stock_sleeve_scan_universe(self) -> List[str]:
        """Symbols the stock-sleeve scanner considers each morning.

        Priority: an explicit STOCK_SLEEVE_SYMBOLS list wins; then a screened
        STOCK_SLEEVE_POOL_FILE if set and non-empty; otherwise the named
        universe categories (STOCK_SLEEVE_UNIVERSE) from universe.UNIVERSE.
        """
        explicit = self.strategy.stock_sleeve_symbols.strip()
        if explicit:
            return [s.strip().upper() for s in explicit.split(",") if s.strip()]
        pool_file = self.strategy.stock_sleeve_pool_file.strip()
        if pool_file:
            from .universe_screen import load_pool_symbols
            syms = load_pool_symbols(pool_file)
            if syms:
                return syms          # else fall through to the static categories
        from .universe import get_universe
        cats = [c.strip() for c in self.strategy.stock_sleeve_universe.split(",") if c.strip()]
        return get_universe(cats) if cats else []
