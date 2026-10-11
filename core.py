"""
Core portfolio — the slow book the paper account HOLDS, which the intraday
engine trades around.

Why: the repo's own research (CLAUDE.md, 2026-07-19 verdict) says the return
in the QQQ family accrues overnight and over long holds, not intraday, and the
account spent July–October 2026 flat at $100,000 with nothing held. The core
puts CORE_ALLOC_PCT of equity into the same target model the IRA follows
(advisor/model.py) so the paper account is a live sandbox for that model, and
leaves the remainder to the ORB engine.

Rules (deliberately few, all from the validated research):
  * Target weight per name = model weight x CORE_ALLOC_PCT / 100 of equity.
  * Trend gate (default on): a name is held only while its last COMPLETED
    daily close is above its 200-day simple average. Below trend its target
    is 0 and the money sits in cash (not redistributed) — the defensive switch
    that beat buy-and-hold on drawdown in every window containing a bear.
    Unknown trend (not enough bars) = leave that name exactly as it is.
  * Rebalance a held name only when |actual - target| > band x target
    (default 25% relative), so calm markets don't churn. Entries and trend
    exits always act. Sells go first so cash is freed before it is spent.
  * Whole shares, market orders, day time-in-force, placed once per day
    after the open on prior-close signals (next-open fills, like the
    backtests). Never shorts, never leverage: the core can hold at most
    CORE_ALLOC_PCT of equity.
  * Ownership is recorded in data/core_state.json so the engine can tell its
    own day trades from core holdings and never flattens the core at the
    close (Engine.protected_symbols).

Nothing here is tuned to look good; it is the IRA model, held with a trend
gate, measured honestly against buy-and-hold QQQ in the daily heartbeat.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set

from .advisor.engine import Trade, suggest_trades
from .advisor.model import default_targets, parse_targets
from .config import Config, CoreConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def model_weights(cfg: CoreConfig) -> Dict[str, float]:
    """Weights WITHIN the core (sum ~1). CORE_TARGETS overrides the IRA model."""
    custom = parse_targets(cfg.targets) if cfg.targets.strip() else {}
    return custom or default_targets()


def account_targets(cfg: CoreConfig) -> Dict[str, float]:
    """Weights of ACCOUNT equity: model weight scaled by the core's share."""
    scale = cfg.alloc_pct / 100.0
    return {s: w * scale for s, w in model_weights(cfg).items()}


# ---------------------------------------------------------------------------
# State (what the core owns) — the engine's exclusion list
# ---------------------------------------------------------------------------

def load_state(path: str) -> Dict:
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("holdings", {})
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not crash the bot
        logger.error("core: unreadable state file %s: %s", path, exc)
    return {"holdings": {}}


def save_state(path: str, state: Dict) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Trend
# ---------------------------------------------------------------------------

@dataclass
class TrendStatus:
    symbol: str
    close: Optional[float]      # last completed daily close
    sma: Optional[float]
    above: Optional[bool]       # None = not enough data
    bars: int
    as_of: str = ""

    @property
    def label(self) -> str:
        if self.above is None:
            return "unknown"
        return "above" if self.above else "BELOW"


def sma_from_bars(bars: List[Dict], n: int, before: Optional[str] = None) -> TrendStatus:
    """Compute close vs SMA(n) from Alpaca daily bars. Only bars dated strictly
    before `before` (default today) count, so a partial intraday bar is never
    treated as a close."""
    cutoff = before or date.today().isoformat()
    closes = []
    last_t = ""
    for b in bars:
        t = str(b.get("t", ""))[:10]
        if t and t < cutoff:
            try:
                c = float(b.get("c", 0) or 0)
            except (TypeError, ValueError):
                continue
            if c > 0:
                closes.append(c)
                last_t = t
    sym = str(bars[0].get("S", bars[0].get("symbol", ""))) if bars else ""
    if len(closes) < n:
        return TrendStatus(sym, closes[-1] if closes else None, None, None, len(closes), last_t)
    sma = sum(closes[-n:]) / n
    return TrendStatus(sym, closes[-1], sma, closes[-1] > sma, len(closes), last_t)


def fetch_trend(broker, symbol: str, n: int) -> TrendStatus:
    """Daily bars from the broker (IEX feed is fine for closes)."""
    start = (datetime.now(timezone.utc) - timedelta(days=int(n * 1.6) + 30)).strftime("%Y-%m-%d")
    try:
        bars = broker.get_bars(symbol, timeframe="1Day", start=start, limit=n + 40)
    except Exception as exc:  # noqa: BLE001
        logger.warning("core: daily bars failed for %s: %s", symbol, exc)
        bars = []
    st = sma_from_bars(bars or [], n)
    st.symbol = symbol
    return st


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass
class SymbolView:
    symbol: str
    target_pct: float            # of account equity, before the trend gate
    effective_pct: float         # after the trend gate
    actual_pct: float
    qty: float
    value: float
    price: float
    trend: TrendStatus
    action: str = ""             # "", "BUY n", "SELL n"

    def as_dict(self) -> Dict:
        d = asdict(self)
        d["trend"] = {"label": self.trend.label, "close": self.trend.close,
                      "sma": self.trend.sma, "bars": self.trend.bars}
        return d


def plan_rebalance(equity: float, core_positions: Dict[str, dict],
                   targets: Dict[str, float], trends: Dict[str, TrendStatus],
                   prices: Dict[str, float], *, trend_gate: bool,
                   band: float, min_dollars: float) -> List[Trade]:
    """Pure: turn (equity, held core, targets, trend) into whole-share trades.

    core_positions: {sym: {'qty','value','price'}} — ONLY core holdings.
    Uses advisor.engine.suggest_trades with cash = equity - core value, so the
    model's dollar targets are weights of total equity. Symbols with unknown
    trend are removed from both sides (no trade either way)."""
    eff: Dict[str, float] = {}
    pos = dict(core_positions)
    for s, w in targets.items():
        t = trends.get(s)
        if trend_gate:
            if t is None or t.above is None:
                pos.pop(s, None)          # unknown: leave untouched
                continue
            if not t.above:
                eff[s] = 0.0              # below trend: exit / stay out
                continue
        eff[s] = w
    core_value = sum(p.get("value", 0.0) for p in pos.values())
    cash = max(0.0, equity - core_value)
    trades = suggest_trades(pos, cash, eff, prices, band=band, min_dollars=min_dollars)
    # Annotate trend exits so the heartbeat reads naturally.
    for tr in trades:
        t = trends.get(tr.symbol)
        if tr.side == "SELL" and eff.get(tr.symbol, 0.0) == 0.0 and t is not None and t.above is False:
            tr.reason = f"below {t.label} trend ({t.close:.2f} < sma {t.sma:.2f}) -> exit"
    return trades


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class CorePortfolio:
    """Everything the core does against the live broker."""

    def __init__(self, config: Config, broker, notifier=None):
        self.config = config
        self.cfg: CoreConfig = config.core
        self.broker = broker
        self.notifier = notifier
        self.state = load_state(self.cfg.state_file)

    # ---- ownership --------------------------------------------------------
    def reload_state(self) -> None:
        """Re-read the state file (another process, the scheduler, writes it)."""
        self.state = load_state(self.cfg.state_file)

    @property
    def targets(self) -> Dict[str, float]:
        return account_targets(self.cfg) if self.cfg.enabled else {}

    def protected_symbols(self) -> Set[str]:
        """Symbols the intraday engine must never touch: anything the core
        recorded as owned, plus the model's names while the core is enabled
        (so a pre-first-rebalance sleeve pick can't collide with a core buy)."""
        held = {s for s, q in (self.state.get("holdings") or {}).items() if _f(q) > 0}
        return held | set(self.targets)

    def split_positions(self, broker_positions: Iterable[Dict]):
        """(core_positions, intraday_positions) from the broker's list."""
        prot = self.protected_symbols()
        core, intraday = [], []
        for p in broker_positions or []:
            (core if str(p.get("symbol", "")) in prot else intraday).append(p)
        return core, intraday

    @staticmethod
    def positions_map(core_positions: Iterable[Dict]) -> Dict[str, dict]:
        out = {}
        for p in core_positions:
            qty = _f(p.get("qty"))
            val = _f(p.get("market_value"))
            price = _f(p.get("current_price")) or (val / qty if qty else 0.0)
            out[str(p["symbol"])] = {"qty": qty, "value": val, "price": price}
        return out

    # ---- status (heartbeat) ----------------------------------------------
    def snapshot(self, broker_positions: Optional[List[Dict]] = None,
                 equity: Optional[float] = None, with_trend: bool = True):
        """Per-symbol view + planned trades, without placing anything."""
        if broker_positions is None:
            broker_positions = self.broker.get_positions()
        if equity is None:
            equity = self.broker.get_equity()
        core_pos, _ = self.split_positions(broker_positions)
        pos = self.positions_map(core_pos)
        targets = self.targets
        symbols = sorted(set(targets) | set(pos))
        trends = {s: (fetch_trend(self.broker, s, self.cfg.trend_sma) if with_trend
                      else TrendStatus(s, None, None, None, 0)) for s in symbols}
        prices = {s: pos[s]["price"] for s in pos}
        for s in symbols:
            if prices.get(s, 0) <= 0:
                prices[s] = self._quote(s) or (trends[s].close or 0.0)
        trades = plan_rebalance(equity, pos, targets, trends, prices,
                                trend_gate=self.cfg.trend_gate, band=self.cfg.band,
                                min_dollars=self.cfg.min_trade_dollars)
        by_sym = {t.symbol: t for t in trades}
        views: List[SymbolView] = []
        for s in symbols:
            tgt = targets.get(s, 0.0)
            t = trends[s]
            # Gate off, trend above, or trend unknown (left untouched): full target.
            eff = 0.0 if (self.cfg.trend_gate and t.above is False) else tgt
            val = pos.get(s, {}).get("value", 0.0)
            tr = by_sym.get(s)
            views.append(SymbolView(
                symbol=s, target_pct=tgt * 100, effective_pct=eff * 100,
                actual_pct=(val / equity * 100) if equity else 0.0,
                qty=pos.get(s, {}).get("qty", 0.0), value=val,
                price=prices.get(s, 0.0), trend=t,
                action=(f"{tr.side} {tr.quantity}" if tr else ""),
            ))
        core_value = sum(v.value for v in views)
        return {
            "equity": equity,
            "core_value": core_value,
            "core_pct": (core_value / equity * 100) if equity else 0.0,
            "alloc_pct": self.cfg.alloc_pct,
            "trend_gate": self.cfg.trend_gate,
            "trend_sma": self.cfg.trend_sma,
            "views": views,
            "trades": trades,
            "last_rebalance": self.state.get("last_rebalance"),
        }

    # ---- action -------------------------------------------------------------
    def rebalance(self, dry_run: bool = False) -> List[Trade]:
        """Place today's core trades (sells first). Records ownership in the
        state file BEFORE buying so a crash mid-way still leaves the engine
        aware of every symbol the core may now hold."""
        if not self.cfg.enabled:
            logger.info("core: disabled (CORE_ENABLED=false); nothing to do")
            return []
        acct = self.broker.get_account()
        if not acct:
            msg = "core: cannot read account (auth/network); rebalance skipped"
            logger.error(msg)
            if self.notifier:
                self.notifier.alert("trader core: rebalance skipped", msg)
            return []
        equity = _f(acct.get("equity"))
        positions = self.broker.get_positions()
        snap = self.snapshot(positions, equity)
        trades: List[Trade] = snap["trades"]
        today = date.today().isoformat()

        # Ownership first: every symbol we hold or are about to buy.
        core_pos, _ = self.split_positions(positions)
        holdings = {p["symbol"]: _f(p.get("qty")) for p in core_pos}
        for t in trades:
            if t.side == "BUY":
                holdings[t.symbol] = holdings.get(t.symbol, 0.0) + t.quantity
            else:
                holdings[t.symbol] = max(0.0, holdings.get(t.symbol, 0.0) - t.quantity)
        self.state["holdings"] = {s: q for s, q in holdings.items() if q > 0}
        self.state["updated"] = datetime.now(timezone.utc).isoformat()
        if not dry_run:
            save_state(self.cfg.state_file, self.state)

        if not trades:
            logger.info("core: within bands; no trades (equity $%,.0f, core %.1f%%)",
                        equity, snap["core_pct"])
            return []

        placed: List[Trade] = []
        failed: List[str] = []
        for t in trades:
            logger.info("core: %s %d %s ~$%,.0f  %s", t.side, t.quantity, t.symbol,
                        t.est_value, t.reason)
            if dry_run:
                placed.append(t)
                continue
            res = self.broker.submit_order(
                symbol=t.symbol, qty=t.quantity, side=t.side.lower(),
                order_type="market", time_in_force="day",
                client_order_id=f"core-{today}-{t.symbol}-{t.side.lower()}"[:48],
            )
            if res and res.get("id"):
                placed.append(t)
            else:
                failed.append(f"{t.side} {t.quantity} {t.symbol}")
                logger.error("core: order REJECTED: %s %d %s", t.side, t.quantity, t.symbol)

        if not dry_run:
            self.state["last_rebalance"] = {
                "date": today, "equity": equity,
                "trades": [f"{t.side} {t.quantity} {t.symbol} ({t.reason})" for t in placed],
                "failed": failed,
            }
            save_state(self.cfg.state_file, self.state)
            if failed and self.notifier:
                self.notifier.alert("trader core: order(s) rejected",
                                    "Rejected: " + "; ".join(failed))
        return placed

    # ---- helpers --------------------------------------------------------------
    def _quote(self, symbol: str) -> float:
        try:
            m = self.broker.mid_price(symbol)
            if m:
                return float(m)
        except Exception:  # noqa: BLE001
            pass
        return 0.0


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default
