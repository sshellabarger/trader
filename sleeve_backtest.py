"""
Sleeve Replay Backtester — the stock sleeve's measuring instrument.

The live sleeve produces ~1 entry per 45 name-days (07-06..07-17: one COIN
trade), so tuning it live means one data point per fortnight. This module
replays the WHOLE sleeve pipeline — morning scanner ranking, long-bias pick
selection, per-pick ORB with honest fills — over months of Alpaca history, so
gate settings (range band, entry window, candidate count) are chosen on
hundreds of name-days and then walk-forward checked, instead of guessed.

Honesty rules (same bar as CLAUDE.md):
  - Entry fills at the NEXT bar's open when BACKTEST_ENTRY_FILL_NEXT_OPEN=true
    (set it for every decision-grade run), slippage via BACKTEST_SLIPPAGE_BPS
    (stress at 10), gap-aware stop fills — all inherited unchanged from
    backtest.Backtester._simulate_day.
  - Scanner inputs use no lookahead: gap = today's official open vs the PRIOR
    day's close; relative volume = PREMARKET cumulative volume vs the prior
    day's volume; daily ATR% for the ATR band uses prior days only.
  - The sweep reports train AND test segments for every config, sorted so an
    in-sample winner with a losing test column is immediately visible.

Known deltas vs the live sleeve (document, don't hide):
  - News layer is NOT replayed: no hot-list names added to the scan universe
    and no sentiment gate. The live droplet runs STOCK_SLEEVE_NEWS_ENABLED=true,
    so live picks can include catalyst names (LEVI, IBM) this replay never sees.
    `--calibrate` quantifies exactly this gap against the droplet journals.
  - Live relative volume comes from the 09:30 snapshot's accumulating daily
    bar; the replay reconstructs it from premarket 1-min bars (`--rvol
    premarket`) or neutralizes it (`--rvol off`). MEASURED 2026-07-22 on the
    Jan-Jul 2026 cache: IEX premarket volume is 0-9.5% of the prior IEX day
    (median 0, max 0.095 across 3,752 symbol-days), while live scanner rvols
    print 0.5-13 — the two quantities are NOT comparable. The sim therefore
    uses reconstructed rvol as a RANKING WEIGHT ONLY and never applies the
    live 0.5 floor to it (the floor would veto 100% of history, and did, on
    the first baseline run). Calibration shows which ranking mode best
    matches the live pick lists.
  - Live scanner trims to ScannerConfig.max_candidates (5, no env override)
    BEFORE the sleeve's own top-N — so live candidate counts above 5 need a
    code change, not just STOCK_SLEEVE_MAX_CANDIDATES. The replay mirrors the
    same two-stage trim.

Usage (run where Alpaca is reachable — Mac or droplet, valid keys in .env):
  BACKTEST_ENTRY_FILL_NEXT_OPEN=true BACKTEST_SLIPPAGE_BPS=10 \
    python -m trader sleeve-backtest --start 2026-01-05 --end 2026-07-18 --fetch
  ... --sweep --train-end 2026-05-15 --label sleeve_v1
  ... --calibrate trade_logs            # vs droplet journal summaries
Cache lives in data/sleeve_cache/ (JSON); after the first fetch+replay, sweeps
run fully offline.
"""
from __future__ import annotations

import csv
import glob
import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .backtest import Backtester, BacktestResult, session_window
from .config import Config
# daily_atr_pct lives in indicators.py so the LIVE engine and this replay
# compute the ATR band input with the same code; re-exported here because the
# replay's public API (and its tests) reach it via this module.
from .indicators import daily_atr_pct
from .regime import RegimeDetector
from .risk import RiskManager
from .strategies.orb import ORBStrategy

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _bar_time_et(bar: Dict) -> Optional[datetime]:
    t = bar.get("t", "")
    if not isinstance(t, str):
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(_ET)


def prior_daily(daily_bars: List[Dict], day_str: str) -> Optional[Dict]:
    """Most recent daily bar strictly BEFORE day_str (no lookahead)."""
    prior = [b for b in daily_bars if str(b.get("t", ""))[:10] < day_str]
    return prior[-1] if prior else None


def daily_bar_for(daily_bars: List[Dict], day_str: str) -> Optional[Dict]:
    for b in daily_bars:
        if str(b.get("t", ""))[:10] == day_str:
            return b
    return None


@dataclass
class SimCandidate:
    """What the replayed scanner knows about one symbol at the open."""
    symbol: str
    gap_pct: float
    rvol: float
    open_price: float
    prev_close: float
    prev_volume: float

    @property
    def score(self) -> float:
        return abs(self.gap_pct) * self.rvol


class BarCache:
    """Disk cache of Alpaca bars, so history is fetched once and every sweep
    or re-run afterwards is offline and instant. Three namespaces:

      daily_<SYM>.json        daily bars (fetch range padded ~90d back for ATR)
      scan_<SYM>.json         {"YYYY-MM-DD": {"pm_vol": v, "open": o}}
      rth_<SYM>_<DAY>.json    regular-session 1-min bars (picks only)

    A cached EMPTY rth file ([]) is meaningful — "no bars that day" — and is
    not refetched. In offline mode a cache miss is reported, never fetched.
    """

    def __init__(self, cache_dir: str, broker, offline: bool = False,
                 polite_sleep: float = 0.35):
        self.dir = cache_dir
        self.broker = broker
        self.offline = offline
        self.sleep = polite_sleep
        self.misses: List[str] = []          # offline cache misses (bounded)
        os.makedirs(cache_dir, exist_ok=True)

    # -- io ------------------------------------------------------------
    def _path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def _load(self, name: str):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _save(self, name: str, obj) -> None:
        tmp = self._path(name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, self._path(name))

    def _miss(self, what: str) -> None:
        if len(self.misses) < 500:
            self.misses.append(what)

    # -- daily bars ------------------------------------------------------
    def daily(self, symbol: str) -> List[Dict]:
        data = self._load(f"daily_{symbol}.json")
        return data.get("bars", []) if isinstance(data, dict) else []

    def ensure_daily(self, symbol: str, start: str, end: str) -> List[Dict]:
        data = self._load(f"daily_{symbol}.json")
        pad_start = (datetime.strptime(start, "%Y-%m-%d").date()
                     - timedelta(days=90)).isoformat()
        if isinstance(data, dict):
            meta = data.get("meta", {})
            if meta.get("start", "9999") <= pad_start and meta.get("end", "") >= end:
                return data.get("bars", [])
        if self.offline:
            self._miss(f"daily:{symbol}")
            return data.get("bars", []) if isinstance(data, dict) else []
        bars = self.broker.get_bars(symbol, timeframe="1Day",
                                    start=pad_start, end=end, limit=2000)
        time.sleep(self.sleep)
        if bars:
            self._save(f"daily_{symbol}.json",
                       {"meta": {"start": pad_start, "end": end}, "bars": bars})
        return bars or []

    # -- premarket scan table ---------------------------------------------
    def scan_table(self, symbol: str) -> Dict[str, Dict]:
        data = self._load(f"scan_{symbol}.json")
        return data.get("days", {}) if isinstance(data, dict) else {}

    def ensure_scan_table(self, symbol: str, start: str, end: str) -> Dict[str, Dict]:
        data = self._load(f"scan_{symbol}.json")
        if isinstance(data, dict):
            meta = data.get("meta", {})
            if meta.get("start", "9999") <= start and meta.get("end", "") >= end:
                return data.get("days", {})
        if self.offline:
            self._miss(f"scan:{symbol}")
            return data.get("days", {}) if isinstance(data, dict) else {}

        days: Dict[str, Dict] = (data or {}).get("days", {}) if isinstance(data, dict) else {}
        cursor = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        while cursor <= end_d:
            chunk_end = min(cursor + timedelta(days=6), end_d)
            bars = self.broker.get_bars(
                symbol, timeframe="1Min",
                start=cursor.isoformat(),
                end=(chunk_end + timedelta(days=1)).isoformat(),
                limit=10000,
            )
            time.sleep(self.sleep)
            for b in bars or []:
                bt = _bar_time_et(b)
                if bt is None:
                    continue
                dkey = bt.date().isoformat()
                rec = days.setdefault(dkey, {"pm_vol": 0.0, "open": None})
                hm = (bt.hour, bt.minute)
                if (4, 0) <= hm < (9, 30):
                    rec["pm_vol"] += float(b.get("v", 0) or 0)
                elif (9, 30) <= hm < (16, 0) and rec["open"] is None:
                    rec["open"] = float(b.get("o", 0) or 0)
            cursor = chunk_end + timedelta(days=1)

        self._save(f"scan_{symbol}.json",
                   {"meta": {"start": start, "end": end}, "days": days})
        return days

    # -- regular-session minute bars ---------------------------------------
    def rth(self, symbol: str, day_str: str) -> Optional[List[Dict]]:
        data = self._load(f"rth_{symbol}_{day_str}.json")
        return data if isinstance(data, list) else None

    def ensure_rth(self, symbol: str, day_str: str) -> List[Dict]:
        cached = self.rth(symbol, day_str)
        if cached is not None:
            return cached
        if self.offline:
            self._miss(f"rth:{symbol}:{day_str}")
            return []
        start, end = session_window(day_str)
        bars = self.broker.get_bars(symbol, timeframe="1Min",
                                    start=start, end=end, limit=500)
        time.sleep(self.sleep)
        self._save(f"rth_{symbol}_{day_str}.json", bars or [])
        return bars or []


class SleeveBacktester(Backtester):
    """Replays the scanner-driven stock sleeve day by day.

    Inherits every fill/stop/EOD mechanic from Backtester._simulate_day
    unchanged — this class only supplies what the live engine supplies:
    the day's picks and their day-level context (daily ATR%).
    """

    def __init__(self, config: Config, cache_dir: str = "data/sleeve_cache",
                 rvol_mode: str = "premarket", offline: bool = False):
        super().__init__(config)
        if rvol_mode not in ("premarket", "off"):
            raise ValueError(f"rvol_mode must be 'premarket' or 'off', got {rvol_mode!r}")
        self.rvol_mode = rvol_mode
        self.cache = BarCache(cache_dir, self.broker, offline=offline)
        self.offline = offline
        self.day_log: List[Dict] = []
        self._rvol_fallbacks = 0     # symbol-days where the scan table was missing
        self._apply_sleeve_risk()

    # ------------------------------------------------------------------
    def _apply_sleeve_risk(self):
        """Mirror engine._apply_sleeve_risk: in stocks-only mode the global
        risk caps ARE the sleeve caps; explicit env always wins."""
        s = self.config.strategy
        if os.getenv("MAX_POSITIONS") is None:
            self.config.risk.max_positions = s.stock_sleeve_max_positions
        if os.getenv("MAX_POSITION_PCT") is None:
            self.config.risk.max_position_pct = s.stock_sleeve_max_position_pct
        if os.getenv("ORB_MAX_ENTRIES_PER_DAY") is None:
            s.orb_max_entries_per_day = max(
                s.orb_max_entries_per_day, s.stock_sleeve_max_positions)
        if os.getenv("MAX_DAILY_TRADES") is None:
            self.config.risk.max_daily_trades = max(
                self.config.risk.max_daily_trades, s.stock_sleeve_max_positions * 2)
        # The live sleeve never carries positions overnight; a stray env flag
        # must not quietly turn the replay into a different strategy.
        if s.orb_hold_overnight:
            logger.warning("sleeve replay: forcing orb_hold_overnight OFF "
                           "(live sleeve flattens EOD)")
            s.orb_hold_overnight = False

    # ------------------------------------------------------------------
    def prefetch(self, start_date: str, end_date: str) -> None:
        """Populate daily + premarket caches for the whole scan universe.
        RTH minute bars are fetched lazily per pick during the replay."""
        universe = self.config.stock_sleeve_scan_universe()
        syms = list(dict.fromkeys(universe + [self.config.strategy.primary_symbol]))
        logger.info(f"Prefetch: {len(syms)} symbols, {start_date}..{end_date} "
                    f"(daily always; premarket tables: rvol={self.rvol_mode})")
        for i, sym in enumerate(syms, 1):
            self.cache.ensure_daily(sym, start_date, end_date)
            if self.rvol_mode == "premarket" and sym != self.config.strategy.primary_symbol:
                self.cache.ensure_scan_table(sym, start_date, end_date)
            logger.info(f"  [{i}/{len(syms)}] {sym} cached")

    # ------------------------------------------------------------------
    def simulate_scan(self, day_str: str, universe: List[str]) -> List[SimCandidate]:
        """Reproduce scanner.scan_candidates for one historical morning:
        same price/volume filters, same score, same long-bias ordering, same
        trim — EXCEPT the live rvol>=0.5 floor, which is deliberately not
        applied (see the module docstring: IEX premarket volume maxes at ~0.1x
        the prior day, so the floor vetoes all of history; reconstructed rvol
        is a ranking weight only, with |gap| as the tiebreaker so zero-premarket
        mornings degrade to pure gap ranking instead of arbitrary order)."""
        sc = self.config.scanner
        long_bias = self.config.strategy.stock_sleeve_long_bias
        cands: List[SimCandidate] = []
        for sym in universe:
            dailies = self.cache.daily(sym)
            if not dailies:
                continue
            prior = prior_daily(dailies, day_str)
            today = daily_bar_for(dailies, day_str)
            if prior is None or today is None:
                continue
            prev_close = float(prior.get("c", 0) or 0)
            prev_vol = float(prior.get("v", 0) or 0)
            open_price = float(today.get("o", 0) or 0)
            if open_price <= 0 or prev_close <= 0:
                continue
            # Scanner filters, in scanner._evaluate_snapshot order.
            if open_price < sc.min_price or open_price > sc.max_price:
                continue
            if prev_vol < sc.min_volume:
                continue
            gap_pct = (open_price - prev_close) / prev_close * 100.0
            if self.rvol_mode == "premarket":
                rec = self.cache.scan_table(sym).get(day_str)
                if rec is None:
                    rvol = 1.0
                    self._rvol_fallbacks += 1
                else:
                    rvol = (float(rec.get("pm_vol", 0)) / prev_vol) if prev_vol > 0 else 0.0
            else:
                rvol = 1.0
            # NOTE: no rvol >= min_relative_volume gate here. The live floor
            # operates on snapshot semantics that IEX history cannot reproduce
            # (measured max pm/prev ratio 0.095 → the floor would drop every
            # name every day). Reconstructed rvol only weights the ranking.
            cands.append(SimCandidate(sym, gap_pct, rvol, open_price,
                                      prev_close, prev_vol))

        # |gap| tiebreaker: on mornings where premarket IEX volume is zero for
        # most names (the median morning), scores collapse to 0 and the ranking
        # degrades to pure gap size instead of list order.
        if long_bias:
            cands.sort(key=lambda c: (c.gap_pct < 0, -c.score, -abs(c.gap_pct)))
        else:
            cands.sort(key=lambda c: (-c.score, -abs(c.gap_pct)))
        return cands[: sc.max_candidates]

    def picks_for_day(self, day_str: str, universe: List[str]
                      ) -> Tuple[List[SimCandidate], List[SimCandidate]]:
        """Mirror engine._ensure_sleeve_symbols: (picks, displaced_gap_downs)."""
        candidates = self.simulate_scan(day_str, universe)
        n = self.config.strategy.stock_sleeve_max_candidates
        if not self.config.strategy.stock_sleeve_long_bias:
            return candidates[:n], []
        picks = [c for c in candidates if c.gap_pct >= 0][:n]
        floor = min((c.score for c in picks), default=0.0)
        displaced = [c for c in candidates
                     if c.gap_pct < 0 and (len(picks) < n or c.score >= floor)]
        return picks, displaced[:n]

    # ------------------------------------------------------------------
    def run_sleeve(self, start_date: str, end_date: str,
                   label: str = "", save: bool = True) -> BacktestResult:
        cfg = self.config
        universe = cfg.stock_sleeve_scan_universe()
        regime_symbol = cfg.strategy.primary_symbol
        regime_bars = self.cache.ensure_daily(regime_symbol, start_date, end_date)
        regime_detector = RegimeDetector(
            ema_period=cfg.strategy.vwap_regime_ema_period)

        # ORB only: the live sleeve keeps VWAP index-bound (applies_to), so no
        # sleeve name ever trades it. Instantiating just ORB makes that exact.
        strategies = [ORBStrategy(cfg)]
        risk = RiskManager(cfg.risk)

        capital = cfg.backtest.initial_capital
        all_trades: List = []
        equity_curve: List[Tuple[str, float]] = []
        open_positions: Dict[str, Dict] = {}
        self.day_log = []
        trading_days = 0

        logger.info(f"Sleeve replay: {start_date}..{end_date}, "
                    f"universe={len(universe)} names, rvol={self.rvol_mode}, "
                    f"band={'ATR %.2f-%.2f' % (cfg.strategy.orb_range_atr_lo, cfg.strategy.orb_range_atr_hi) if cfg.strategy.orb_range_band_atr else 'fixed %.2f-%.2f' % (cfg.strategy.orb_min_range_pct, cfg.strategy.orb_max_range_pct)}, "
                    f"entry_window={cfg.strategy.orb_entry_window_minutes}m, "
                    f"next_open_fill={cfg.backtest.entry_fill_next_open}, "
                    f"slippage={cfg.backtest.slippage_bps}bps")

        current = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            day_str = current.isoformat()
            current += timedelta(days=1)

            # Market open that day? (holiday ⇒ no regime-symbol daily bar)
            if daily_bar_for(regime_bars, day_str) is None:
                continue

            picks, displaced = self.picks_for_day(day_str, universe)
            rec: Dict = {
                "date": day_str,
                "picks": [{"symbol": c.symbol, "gap_pct": round(c.gap_pct, 2),
                           "rvol": round(c.rvol, 2)} for c in picks],
                "displaced_gap_downs": [
                    {"symbol": c.symbol, "gap_pct": round(c.gap_pct, 2)}
                    for c in displaced],
                "no_bars": [], "trades": 0, "pnl": 0.0,
            }
            # Index overnight gap, logged for analysis only — the live sleeve
            # keeps the QQQ overnight gate INERT for stocks, so the replay
            # passes overnight_gap_pct=None below to mirror that exactly.
            prior_r = prior_daily(regime_bars, day_str)
            today_r = daily_bar_for(regime_bars, day_str)
            if prior_r and today_r:
                pc, to = float(prior_r.get("c", 0) or 0), float(today_r.get("o", 0) or 0)
                if pc > 0 and to > 0:
                    rec["qqq_gap_pct"] = round((to - pc) / pc * 100.0, 2)

            if picks:
                day_bars: Dict[str, List[Dict]] = {}
                extra_ind: Dict[str, Dict] = {}
                for c in picks:
                    bars = self.cache.ensure_rth(c.symbol, day_str)
                    if bars:
                        day_bars[c.symbol] = bars
                        datr = daily_atr_pct(self.cache.daily(c.symbol), day_str)
                        extra_ind[c.symbol] = {"daily_atr_pct": datr}
                    else:
                        rec["no_bars"].append(c.symbol)

                if any(len(b) >= 20 for b in day_bars.values()):
                    trading_days += 1
                    risk.reset_daily(capital)
                    prior_regime_bars = [
                        b for b in regime_bars if str(b.get("t", ""))[:10] < day_str]
                    regime = regime_detector.update_from_bars(prior_regime_bars)
                    for s in strategies:
                        s.reset_daily()
                        s.set_market_regime(regime)

                    capital, day_trades = self._simulate_day(
                        day_bars, strategies, risk, capital, open_positions,
                        overnight_gap_pct=None,          # stock gate inert, as live
                        extra_indicators=extra_ind,
                    )
                    all_trades.extend(day_trades)
                    rec["trades"] = len(day_trades)
                    rec["pnl"] = round(sum(t.pnl for t in day_trades), 2)
                    if day_trades:
                        logger.info(f"  {day_str}: {len(day_trades)} trade(s), "
                                    f"P&L ${rec['pnl']:+,.2f} "
                                    f"picks={[c.symbol for c in picks]}")

            equity_curve.append((day_str, capital))
            self.day_log.append(rec)

        result = self._compute_metrics(
            all_trades, equity_curve, cfg.backtest.initial_capital,
            capital, start_date, end_date, trading_days)
        result.trades = all_trades
        result.symbol = "sleeve"

        n_days = len(self.day_log)
        n_with_picks = sum(1 for d in self.day_log if d["picks"])
        logger.info(f"Sleeve replay done: {n_days} sessions, {n_with_picks} with "
                    f"picks, {result.total_trades} trades "
                    f"(P&L ${result.total_pnl:+,.2f}, PF {result.profit_factor}, "
                    f"win {result.win_rate}%, maxDD {result.max_drawdown_pct}%)")
        if self._rvol_fallbacks:
            logger.warning(f"  rvol fallback (missing premarket table) on "
                           f"{self._rvol_fallbacks} symbol-days — run --fetch "
                           f"to fill, or use --rvol off")
        if self.cache.misses:
            logger.warning(f"  OFFLINE cache misses: {len(self.cache.misses)} "
                           f"(first: {self.cache.misses[:5]}) — results cover "
                           f"cached data only")
        if save:
            self._save_sleeve(result, label)
        return result

    # ------------------------------------------------------------------
    def _save_sleeve(self, result: BacktestResult, label: str):
        out_dir = "backtest_results"
        os.makedirs(out_dir, exist_ok=True)
        tag = f"{label + '_' if label else ''}{result.start_date}_to_{result.end_date}"

        summary = {
            "type": "sleeve_replay",
            "rvol_mode": self.rvol_mode,
            "universe_size": len(self.config.stock_sleeve_scan_universe()),
            "long_bias": self.config.strategy.stock_sleeve_long_bias,
            "band": ({"atr_lo": self.config.strategy.orb_range_atr_lo,
                      "atr_hi": self.config.strategy.orb_range_atr_hi}
                     if self.config.strategy.orb_range_band_atr else
                     {"min_pct": self.config.strategy.orb_min_range_pct,
                      "max_pct": self.config.strategy.orb_max_range_pct}),
            "entry_window_minutes": self.config.strategy.orb_entry_window_minutes,
            "entry_fill_next_open": self.config.backtest.entry_fill_next_open,
            "slippage_bps": self.config.backtest.slippage_bps,
            "start_date": result.start_date, "end_date": result.end_date,
            "total_return_pct": result.total_return_pct,
            "total_pnl": result.total_pnl,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "trades_per_day": result.trades_per_day,
            "days_with_picks": sum(1 for d in self.day_log if d["picks"]),
            "sessions": len(self.day_log),
        }
        with open(os.path.join(out_dir, f"sleeve_summary_{tag}.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)

        with open(os.path.join(out_dir, f"sleeve_days_{tag}.json"), "w") as f:
            json.dump(self.day_log, f, indent=2, default=str)

        if result.trades:
            fieldnames = ["symbol", "strategy", "direction", "qty",
                          "entry_time", "entry_price", "entry_reason",
                          "stop_loss", "take_profit", "exit_time", "exit_price",
                          "exit_reason", "pnl", "pnl_pct", "hold_time_minutes",
                          "is_winner"]
            with open(os.path.join(out_dir, f"sleeve_trades_{tag}.csv"),
                      "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                for t in result.trades:
                    w.writerow(asdict(t))
        logger.info(f"  saved: {out_dir}/sleeve_summary_{tag}.json (+days, trades)")

    # ------------------------------------------------------------------
    def calibrate(self, trade_logs_dir: str) -> Dict:
        """Compare simulated picks against the LIVE journal summaries the
        droplet wrote (trade_logs/summary_YYYY-MM-DD.json). This is the check
        that the reconstruction actually reproduces the live scanner before
        anyone trusts a sweep result. Live-only names outside the replay
        universe are broken out separately — those are the news hot-list."""
        universe = set(self.config.stock_sleeve_scan_universe())
        rows: List[Dict] = []
        for path in sorted(glob.glob(os.path.join(trade_logs_dir, "summary_*.json"))):
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            ctx = data.get("context", {}) or {}
            if ctx.get("mode") != "stock_sleeve":
                continue
            day = str(data.get("date", ""))[:10]
            live = list(ctx.get("picks", []) or [])
            if not day or not live:
                continue
            sim = [c.symbol for c in self.picks_for_day(day, sorted(universe))[0]]
            live_in_uni = [s for s in live if s in universe]
            rows.append({
                "date": day,
                "live": live,
                "sim": sim,
                "overlap": sorted(set(live) & set(sim)),
                "live_only_in_universe": sorted(set(live_in_uni) - set(sim)),
                "live_only_outside_universe": sorted(set(live) - universe),
                "sim_only": sorted(set(sim) - set(live)),
            })
        n = len(rows)
        comparable = [r for r in rows if r["live_only_in_universe"] or r["overlap"] or r["sim_only"]]
        hits = sum(len(r["overlap"]) for r in rows)
        live_total = sum(len([s for s in r["live"] if s in universe]) for r in rows)
        report = {
            "days_compared": n,
            "live_picks_in_universe": live_total,
            "matched_by_sim": hits,
            "match_rate_pct": round(hits / live_total * 100.0, 1) if live_total else None,
            "days": rows,
        }
        logger.info(f"Calibration: {n} live sleeve days; sim matched "
                    f"{hits}/{live_total} in-universe live picks "
                    f"({report['match_rate_pct']}%)")
        for r in rows:
            logger.info(f"  {r['date']}: live={r['live']} sim={r['sim']} "
                        f"overlap={r['overlap']} hotlist_only={r['live_only_outside_universe']}")
        out = os.path.join("backtest_results", "sleeve_calibration.json")
        os.makedirs("backtest_results", exist_ok=True)
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        logger.info(f"  saved: {out}  (comparable days: {len(comparable)})")
        return report


# ---------------------------------------------------------------------------
# Parameter sweep — every config reported on BOTH segments, no cherry-picking.
# ---------------------------------------------------------------------------

SWEEP_GRID: List[Dict] = [
    # The live band first, as the baseline row.
    {"name": "fixed_0.5-1.2_(live)", "atr": False, "lo": 0.5, "hi": 1.2},
    {"name": "fixed_0.3-2.0",        "atr": False, "lo": 0.3, "hi": 2.0},
    {"name": "fixed_0.5-nocap",      "atr": False, "lo": 0.5, "hi": 0.0},
    {"name": "fixed_0.3-nocap",      "atr": False, "lo": 0.3, "hi": 0.0},
    {"name": "atr_0.10-0.50",        "atr": True,  "lo": 0.10, "hi": 0.50},
    {"name": "atr_0.15-0.75",        "atr": True,  "lo": 0.15, "hi": 0.75},
    {"name": "atr_0.25-1.00",        "atr": True,  "lo": 0.25, "hi": 1.00},
]
SWEEP_WINDOWS = [3, 10]


def run_sweep(start_date: str, end_date: str, train_end: Optional[str],
              cache_dir: str, rvol_mode: str, offline: bool,
              label: str = "") -> List[Dict]:
    """Grid over range-band x entry-window, each run on a train segment and a
    test segment. Sorted by TRAIN profit factor so the classic failure mode —
    great train, dead test — is visible on the first screen. The verdict that
    matters is the TEST column of whatever the train column would have made
    you pick; picking by the test column itself is selection on the holdout."""
    if not train_end:
        s = datetime.strptime(start_date, "%Y-%m-%d").date()
        e = datetime.strptime(end_date, "%Y-%m-%d").date()
        train_end = (s + timedelta(days=int((e - s).days * 0.7))).isoformat()
    test_start = (datetime.strptime(train_end, "%Y-%m-%d").date()
                  + timedelta(days=1)).isoformat()
    logger.info(f"Sweep: train {start_date}..{train_end}, "
                f"test {test_start}..{end_date}, "
                f"{len(SWEEP_GRID) * len(SWEEP_WINDOWS)} configs")

    rows: List[Dict] = []
    for band in SWEEP_GRID:
        for window in SWEEP_WINDOWS:
            cfg = Config()
            st = cfg.strategy
            st.orb_range_band_atr = band["atr"]
            if band["atr"]:
                st.orb_range_atr_lo, st.orb_range_atr_hi = band["lo"], band["hi"]
            else:
                st.orb_min_range_pct, st.orb_max_range_pct = band["lo"], band["hi"]
            st.orb_entry_window_minutes = window

            row: Dict = {"band": band["name"], "entry_window": window}
            for seg, (s0, s1) in (("train", (start_date, train_end)),
                                  ("test", (test_start, end_date))):
                bt = SleeveBacktester(cfg, cache_dir=cache_dir,
                                      rvol_mode=rvol_mode, offline=offline)
                r = bt.run_sleeve(s0, s1, save=False)
                row[seg] = {
                    "trades": r.total_trades, "pnl": r.total_pnl,
                    "pf": r.profit_factor, "win_rate": r.win_rate,
                    "max_dd_pct": r.max_drawdown_pct,
                    "return_pct": r.total_return_pct,
                }
            rows.append(row)
            logger.info(f"  {band['name']} w={window}m  "
                        f"train: n={row['train']['trades']} PF={row['train']['pf']} "
                        f"${row['train']['pnl']:+,.0f} | "
                        f"test: n={row['test']['trades']} PF={row['test']['pf']} "
                        f"${row['test']['pnl']:+,.0f}")

    rows.sort(key=lambda r: (r["train"]["pf"] or 0), reverse=True)

    print(f"\n{'='*98}")
    print(f"SLEEVE SWEEP  train {start_date}..{train_end}  |  test {test_start}..{end_date}")
    print(f"{'='*98}")
    print(f"{'band':24s} {'win':>4s} | {'trades':>6s} {'PF':>6s} {'P&L':>10s} {'DD%':>6s} "
          f"| {'trades':>6s} {'PF':>6s} {'P&L':>10s} {'DD%':>6s}")
    print(f"{'':24s} {'':>4s} | {'-- train --':>31s} | {'-- test --':>31s}")
    for r in rows:
        tr, te = r["train"], r["test"]
        print(f"{r['band']:24s} {r['entry_window']:>3d}m | "
              f"{tr['trades']:>6d} {tr['pf']:>6.2f} {tr['pnl']:>+10,.0f} {tr['max_dd_pct']:>6.2f} | "
              f"{te['trades']:>6d} {te['pf']:>6.2f} {te['pnl']:>+10,.0f} {te['max_dd_pct']:>6.2f}")
    print(f"\nRead this table by choosing on the TRAIN columns, then looking at that")
    print(f"row's TEST columns. A config only graduates to paper if BOTH are positive")
    print(f"with enough trades to mean something (n>=30). Sorted by train PF.")

    out_dir = "backtest_results"
    os.makedirs(out_dir, exist_ok=True)
    name = f"sleeve_sweep_{label + '_' if label else ''}{start_date}_to_{end_date}.json"
    with open(os.path.join(out_dir, name), "w") as f:
        json.dump({"train": [start_date, train_end],
                   "test": [test_start, end_date],
                   "rows": rows}, f, indent=2)
    logger.info(f"Sweep saved: {out_dir}/{name}")
    return rows
