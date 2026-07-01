"""
Trade Journal — records every trade with full context for analysis.

Tracks:
  - Entry/exit prices, times, and reasons
  - Strategy attribution
  - P&L per trade, per strategy, per day
  - Win rate, average win/loss, risk-reward achieved
  - Indicator values at entry for later review
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Complete record of a single trade."""
    symbol: str
    strategy: str
    direction: str               # "long" or "short"
    qty: int

    entry_time: str
    entry_price: float
    entry_reason: str

    stop_loss: float
    take_profit: float

    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None

    pnl: float = 0.0
    pnl_pct: float = 0.0
    risk_reward_target: Optional[float] = None
    risk_reward_actual: Optional[float] = None

    hold_time_minutes: float = 0.0
    indicators_at_entry: Dict = field(default_factory=dict)

    # Computed after exit
    is_winner: Optional[bool] = None

    def close(self, exit_price: float, exit_reason: str, exit_time: Optional[str] = None):
        self.exit_price = exit_price
        self.exit_reason = exit_reason
        self.exit_time = exit_time or datetime.now().isoformat()

        # Calculate P&L
        if self.direction == "long":
            self.pnl = (exit_price - self.entry_price) * self.qty
            self.pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100
        else:
            self.pnl = (self.entry_price - exit_price) * self.qty
            self.pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100

        self.is_winner = self.pnl > 0

        # Actual R:R
        risk = abs(self.entry_price - self.stop_loss)
        if risk > 0:
            self.risk_reward_actual = abs(exit_price - self.entry_price) / risk
            if not self.is_winner:
                self.risk_reward_actual = -self.risk_reward_actual

        # Hold time
        try:
            entry_dt = datetime.fromisoformat(self.entry_time)
            exit_dt = datetime.fromisoformat(self.exit_time)
            self.hold_time_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
        except Exception:
            pass


class TradeJournal:
    """Manages trade records and daily performance summaries."""

    def __init__(self, log_dir: str = "trade_logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.open_trades: Dict[str, TradeRecord] = {}  # symbol → record
        self.closed_trades: List[TradeRecord] = []
        self._today = date.today().isoformat()

        # Session diary — context captured even on a NO-TRADE day, so a later
        # review can see what the bot did and why it passed. It is folded into
        # the daily summary JSON (see daily_summary), which persists on the host.
        # Every setter is best-effort and must never raise into the trade loop.
        self.session: Dict = {}                    # config snapshot + equity + picks
        self.symbol_status: Dict[str, Dict] = {}   # symbol -> {status, bars, price}
        self.skips: List[Dict] = []                # [{symbol, stage, reason}]

    # ------------------------------------------------------------------
    # Trade lifecycle
    # ------------------------------------------------------------------

    def open_trade(
        self,
        symbol: str,
        strategy: str,
        direction: str,
        qty: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        entry_reason: str = "",
        indicators: Optional[Dict] = None,
    ) -> TradeRecord:
        record = TradeRecord(
            symbol=symbol,
            strategy=strategy,
            direction=direction,
            qty=qty,
            entry_time=datetime.now().isoformat(),
            entry_price=entry_price,
            entry_reason=entry_reason,
            stop_loss=stop_loss,
            take_profit=take_profit,
            indicators_at_entry=indicators or {},
        )
        # Target R:R
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        record.risk_reward_target = reward / risk if risk > 0 else None

        self.open_trades[symbol] = record
        logger.info(f"JOURNAL OPEN: {direction.upper()} {qty} {symbol} @ {entry_price:.2f} "
                     f"[{strategy}] SL={stop_loss:.2f} TP={take_profit:.2f}")
        return record

    def update_entry_fill(self, symbol: str, fill_price: float) -> None:
        """Reconcile an open trade's entry to the broker's REAL fill price.

        The engine journals the SIGNAL price at submit time (the only price it
        has); once the bracket parent reports filled_avg_price this replaces
        it, so P&L, R:R and any slippage calibration built on the journal use
        the price the account actually paid. Exits already get this honesty
        via last_filled_exit(); entries were the blind spot. Logs the realized
        entry slippage so live fills can be compared to the backtest's
        slippage_bps assumption.
        """
        record = self.open_trades.get(symbol)
        if record is None or not fill_price or fill_price <= 0:
            return
        signal_price = record.entry_price
        if abs(fill_price - signal_price) < 1e-9:
            return
        record.entry_price = fill_price
        risk = abs(fill_price - record.stop_loss)
        reward = abs(record.take_profit - fill_price)
        record.risk_reward_target = reward / risk if risk > 0 else None
        if signal_price > 0:
            adverse_bps = (fill_price - signal_price) / signal_price * 10_000
            if record.direction != "long":
                adverse_bps = -adverse_bps
            logger.info(
                f"JOURNAL ENTRY FILL: {symbol} @ {fill_price:.2f} "
                f"(signal {signal_price:.2f}, slippage {adverse_bps:+.1f} bps)"
            )

    def close_trade(
        self, symbol: str, exit_price: float, exit_reason: str
    ) -> Optional[TradeRecord]:
        record = self.open_trades.pop(symbol, None)
        if record is None:
            logger.warning(f"JOURNAL: No open trade for {symbol}")
            return None

        record.close(exit_price, exit_reason)
        self.closed_trades.append(record)

        emoji = "✅" if record.is_winner else "❌"
        logger.info(
            f"JOURNAL CLOSE {emoji}: {symbol} {record.pnl:+.2f} ({record.pnl_pct:+.2f}%) "
            f"[{record.strategy}] reason={exit_reason} hold={record.hold_time_minutes:.0f}min"
        )
        return record

    # ------------------------------------------------------------------
    # Session diary (recorded even when nothing trades)
    # ------------------------------------------------------------------

    def note_session(self, **context) -> None:
        """Merge once-per-day context: config snapshot, opening equity, mode."""
        self.session.update(context)

    def note_picks(self, symbols: List[str],
                   hotlist: Optional[List[str]] = None) -> None:
        """Record the day's candidate picks (and any news hot-list)."""
        self.session["picks"] = list(symbols or [])
        if hotlist is not None:
            self.session["news_hotlist"] = list(hotlist)

    def note_symbol(self, symbol: str, status: str,
                    bars: int = 0, price: Optional[float] = None) -> None:
        """Record the latest per-symbol data status for the day (e.g. "ok" or
        "no_bars"). Overwrites, so it reflects the most recent tick."""
        self.symbol_status[symbol] = {
            "status": status,
            "bars": bars,
            "price": round(price, 4) if isinstance(price, (int, float)) else None,
        }

    def note_skip(self, symbol: str, stage: str, reason: str) -> None:
        """Record a candidate that was considered but not entered, and why."""
        self.skips.append({"symbol": symbol, "stage": stage, "reason": str(reason)})
        if len(self.skips) > 1000:              # bound memory over a long session
            self.skips = self.skips[-1000:]

    def note_equity_close(self, equity: float) -> None:
        """Record end-of-day equity (best-effort)."""
        try:
            self.session["equity_close"] = round(float(equity), 2)
        except (TypeError, ValueError):
            pass

    def _session_context(self) -> Dict:
        """Assemble the day's diary block: config/equity/picks, per-symbol data
        status, and a compact tally of skip reasons."""
        ctx = dict(self.session)
        if self.symbol_status:
            ctx["symbols"] = self.symbol_status
            ctx["symbols_with_bars"] = sorted(
                s for s, v in self.symbol_status.items() if v.get("status") == "ok"
            )
            ctx["symbols_no_bars"] = sorted(
                s for s, v in self.symbol_status.items() if v.get("status") != "ok"
            )
        if self.skips:
            from collections import Counter
            counts = Counter((s["stage"], s["reason"]) for s in self.skips)
            ctx["skips"] = {
                "total": len(self.skips),
                "by_reason": [
                    {"stage": st, "reason": r, "count": c}
                    for (st, r), c in counts.most_common(20)
                ],
            }
        return ctx

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    def daily_summary(self) -> Dict:
        """Summary stats for today, ALWAYS including the session diary so a
        no-trade day is still legible (config, equity, picks, per-symbol data
        status, and skip reasons)."""
        trades = self.closed_trades
        total_pnl = sum(t.pnl for t in trades)

        summary: Dict = {
            "date": self._today,
            "trades": len(trades),
            "pnl": round(total_pnl, 2),
            "context": self._session_context(),
        }
        if not trades:
            return summary

        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]
        summary.update({
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": len(winners) / len(trades) * 100,
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(sum(t.pnl for t in winners) / len(winners), 2) if winners else 0,
            "avg_loss": round(sum(t.pnl for t in losers) / len(losers), 2) if losers else 0,
            "largest_win": round(max((t.pnl for t in winners), default=0), 2),
            "largest_loss": round(min((t.pnl for t in losers), default=0), 2),
            "avg_hold_minutes": round(
                sum(t.hold_time_minutes for t in trades) / len(trades), 1
            ) if trades else 0,
        })

        # Per-strategy breakdown
        strategies: Dict[str, Dict] = {}
        for t in trades:
            s = strategies.setdefault(t.strategy, {"trades": 0, "pnl": 0, "wins": 0})
            s["trades"] += 1
            s["pnl"] += t.pnl
            if t.is_winner:
                s["wins"] += 1
        for s in strategies.values():
            s["pnl"] = round(s["pnl"], 2)
            s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0
        summary["by_strategy"] = strategies

        return summary

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_daily_csv(self):
        """Append today's trades to a CSV file."""
        if not self.closed_trades:
            return

        filepath = os.path.join(self.log_dir, f"trades_{self._today}.csv")
        file_exists = os.path.exists(filepath)

        fieldnames = [
            "symbol", "strategy", "direction", "qty",
            "entry_time", "entry_price", "entry_reason",
            "stop_loss", "take_profit",
            "exit_time", "exit_price", "exit_reason",
            "pnl", "pnl_pct", "risk_reward_target", "risk_reward_actual",
            "hold_time_minutes", "is_winner",
        ]

        with open(filepath, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            for trade in self.closed_trades:
                writer.writerow(asdict(trade))

        logger.info(f"Saved {len(self.closed_trades)} trades to {filepath}")

    def save_daily_summary(self):
        """Save daily summary as JSON."""
        summary = self.daily_summary()
        filepath = os.path.join(self.log_dir, f"summary_{self._today}.json")
        with open(filepath, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        logger.info(f"Saved daily summary to {filepath}")
