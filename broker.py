"""
Alpaca Broker — clean wrapper around Alpaca's REST API for stocks.
Handles: account info, market clock, orders, positions, historical bars, snapshots.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from .config import BrokerConfig

logger = logging.getLogger(__name__)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an Alpaca ISO 8601 timestamp (e.g. '2026-06-15T13:36:37Z') into an
    aware datetime, or None if it is missing/unparseable."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


class AlpacaBroker:
    """Synchronous Alpaca REST client for stocks."""

    def __init__(self, config: BrokerConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.data_url = config.data_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "APCA-API-KEY-ID": config.api_key,
            "APCA-API-SECRET-KEY": config.api_secret,
            "Accept": "application/json",
        })
        # Rate limiter: track request timestamps
        self._request_times: list = []
        self._max_requests_per_minute: int = 180  # stay under Alpaca's 200/min

    # ------------------------------------------------------------------
    # Internal request helper
    # ------------------------------------------------------------------

    def _throttle(self):
        """Proactive rate limiting — pause if approaching the limit."""
        now = time.time()
        # Prune requests older than 60 seconds
        self._request_times = [t for t in self._request_times if now - t < 60]
        if len(self._request_times) >= self._max_requests_per_minute:
            wait = 60 - (now - self._request_times[0]) + 0.5
            if wait > 0:
                logger.debug(f"Throttling: {wait:.1f}s pause to avoid rate limit")
                time.sleep(wait)

    def _request(
        self, method: str, url: str, params: Optional[Dict] = None,
        json_body: Optional[Dict] = None, retries: int = 2
    ) -> Optional[Any]:
        self._throttle()
        for attempt in range(retries + 1):
            self._request_times.append(time.time())
            try:
                resp = self.session.request(method, url, params=params, json=json_body, timeout=15)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 2))
                    logger.warning(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                # A successful DELETE (e.g. cancelling a single order) returns
                # 204 No Content. Calling .json() on an empty body raises
                # JSONDecodeError, which subclasses RequestException — so it was
                # caught below, logged as an error, and retried twice. The cancel
                # had actually succeeded. Treat an empty body as success.
                if resp.status_code == 204 or not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    # 2xx with a non-JSON body is still a success, just no payload.
                    return {}
            except requests.exceptions.HTTPError as exc:
                logger.error(f"HTTP {exc.response.status_code} {method} {url}: {exc.response.text[:200]}")
                return None
            except requests.exceptions.RequestException as exc:
                logger.error(f"Request error {method} {url}: {exc}")
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None
        return None

    # ------------------------------------------------------------------
    # Account & Clock
    # ------------------------------------------------------------------

    def get_account(self) -> Optional[Dict]:
        return self._request("GET", f"{self.base_url}/v2/account")

    def get_clock(self) -> Optional[Dict]:
        return self._request("GET", f"{self.base_url}/v2/clock")

    def is_market_open(self) -> bool:
        clock = self.get_clock()
        return bool(clock and clock.get("is_open"))

    def minutes_until_close(self) -> Optional[float]:
        clock = self.get_clock()
        if not clock or not clock.get("is_open"):
            return None
        next_close = clock.get("next_close", "")
        try:
            close_dt = datetime.fromisoformat(next_close.replace("Z", "+00:00"))
            now = datetime.now(close_dt.tzinfo)
            return (close_dt - now).total_seconds() / 60.0
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def get_positions(self) -> List[Dict]:
        result = self._request("GET", f"{self.base_url}/v2/positions")
        return result if isinstance(result, list) else []

    def get_position(self, symbol: str) -> Optional[Dict]:
        return self._request("GET", f"{self.base_url}/v2/positions/{symbol}")

    def close_position(self, symbol: str, qty: Optional[int] = None) -> Optional[Dict]:
        params = {}
        if qty is not None:
            params["qty"] = str(qty)
        return self._request("DELETE", f"{self.base_url}/v2/positions/{symbol}", params=params)

    def close_all_positions(self, cancel_orders: bool = False) -> Optional[Any]:
        """Liquidate all positions. With cancel_orders=True, Alpaca cancels all
        open orders first so held shares are freed for the liquidating order."""
        params = {"cancel_orders": "true"} if cancel_orders else None
        return self._request("DELETE", f"{self.base_url}/v2/positions", params=params)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_order_id: Optional[str] = None,
    ) -> Optional[Dict]:
        body: Dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            body["limit_price"] = str(round(limit_price, 2))
        if stop_price is not None:
            body["stop_price"] = str(round(stop_price, 2))
        if client_order_id:
            body["client_order_id"] = client_order_id

        logger.info(f"ORDER → {side.upper()} {qty} {symbol} @ {order_type} "
                     f"(limit={limit_price}, stop={stop_price})")
        return self._request("POST", f"{self.base_url}/v2/orders", json_body=body)

    def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        limit_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
        stop_loss_limit_price: Optional[float] = None,
    ) -> Optional[Dict]:
        """Submit an OCO bracket order (entry + take profit + stop loss)."""
        body: Dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit" if limit_price else "market",
            "time_in_force": "day",
            "order_class": "bracket",
        }
        if limit_price is not None:
            body["limit_price"] = str(round(limit_price, 2))
        if take_profit_price is not None:
            body["take_profit"] = {"limit_price": str(round(take_profit_price, 2))}
        if stop_loss_price is not None:
            sl: Dict[str, str] = {"stop_price": str(round(stop_loss_price, 2))}
            if stop_loss_limit_price is not None:
                sl["limit_price"] = str(round(stop_loss_limit_price, 2))
            body["stop_loss"] = sl

        logger.info(f"BRACKET → {side.upper()} {qty} {symbol} TP={take_profit_price} SL={stop_loss_price}")
        return self._request("POST", f"{self.base_url}/v2/orders", json_body=body)

    def get_orders(
        self,
        status: str = "open",
        symbols: Optional[List[str]] = None,
        limit: int = 100,
        direction: str = "desc",
        nested: bool = False,
    ) -> List[Dict]:
        params: Dict[str, Any] = {
            "status": status,
            "limit": limit,
            "direction": direction,
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        if nested:
            params["nested"] = "true"
        result = self._request("GET", f"{self.base_url}/v2/orders", params=params)
        return result if isinstance(result, list) else []

    def cancel_order(self, order_id: str) -> Optional[Dict]:
        return self._request("DELETE", f"{self.base_url}/v2/orders/{order_id}")

    def cancel_all_orders(self) -> Optional[Any]:
        return self._request("DELETE", f"{self.base_url}/v2/orders")

    def cancel_orders_for_symbol(self, symbol: str) -> int:
        """Cancel all open orders for one symbol (e.g. the live bracket's TP/SL
        legs) so the held shares are freed before liquidating the position.
        Returns the number of cancel requests issued."""
        open_orders = self.get_orders(status="open", symbols=[symbol])
        count = 0
        for order in open_orders:
            oid = order.get("id")
            if oid:
                self.cancel_order(oid)
                count += 1
        return count

    def last_filled_exit(
        self, symbol: str, entry_side: str, after: Optional[str] = None
    ) -> Optional[Dict]:
        """Find the most recently filled order that CLOSED a position in
        `symbol` (i.e. the side opposite the entry), and classify it.

        Returns {"price": float, "reason": "take_profit"|"stop_loss"|"close",
        "filled_at": str} or None if no closing fill is found. Used to journal
        a bracket exit at its real fill price instead of guessing.

        `after` (ISO 8601), when provided, restricts matches to fills that
        occurred strictly after that moment — normally this trade's entry time.
        Without it a stale closing fill from an EARLIER session (still in the
        last-50 closed orders) can be misattributed to the current trade, which
        booked a phantom −$9k "take_profit" on 2026-06-15.
        """
        exit_side = "sell" if entry_side == "buy" else "buy"
        closed = self.get_orders(status="closed", symbols=[symbol], limit=50)
        after_dt = _parse_iso(after)
        best: Optional[Dict] = None
        for o in closed:
            if o.get("side") != exit_side:
                continue
            if not o.get("filled_at") or o.get("status") != "filled":
                continue
            price = o.get("filled_avg_price")
            if price is None:
                continue
            if after_dt is not None:
                filled_dt = _parse_iso(o.get("filled_at"))
                if filled_dt is None or filled_dt <= after_dt:
                    continue  # stale: predates this trade's entry
            if best is None or o["filled_at"] > best["filled_at"]:
                otype = (o.get("type") or o.get("order_type") or "").lower()
                if "stop" in otype:
                    reason = "stop_loss"
                elif "limit" in otype:
                    reason = "take_profit"
                else:
                    reason = "close"
                best = {"price": float(price), "reason": reason, "filled_at": o["filled_at"]}
        return best

    # ------------------------------------------------------------------
    # Market Data — Historical Bars
    # ------------------------------------------------------------------

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 1000,
        feed: Optional[str] = None,
    ) -> List[Dict]:
        """
        Fetch historical bars.
        timeframe: "1Min", "5Min", "15Min", "1Hour", "1Day"
        start/end: ISO 8601 datetime strings
        Returns list of bar dicts with keys: t, o, h, l, c, v, n, vw
        """
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        params: Dict[str, Any] = {
            "timeframe": timeframe,
            "limit": limit,
            "feed": feed or self.config.data_feed,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        all_bars: List[Dict] = []
        page_token = None

        while True:
            if page_token:
                params["page_token"] = page_token
            result = self._request("GET", url, params=params)
            if not result:
                break
            bars = result.get("bars") or []
            all_bars.extend(bars)
            page_token = result.get("next_page_token")
            if not page_token or len(all_bars) >= limit:
                break

        return all_bars[:limit]

    def get_bars_multi(
        self, symbols: List[str], timeframe: str = "1Min",
        start: Optional[str] = None, end: Optional[str] = None,
        limit: int = 1000,
    ) -> Dict[str, List[Dict]]:
        """Fetch bars for multiple symbols. Returns {symbol: [bars]}."""
        url = f"{self.data_url}/v2/stocks/bars"
        params: Dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "limit": limit,
            "feed": self.config.data_feed,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        result = self._request("GET", url, params=params)
        if not result:
            return {}
        return result.get("bars", {})

    # ------------------------------------------------------------------
    # Market Data — Snapshots
    # ------------------------------------------------------------------

    def get_snapshot(self, symbol: str) -> Optional[Dict]:
        url = f"{self.data_url}/v2/stocks/{symbol}/snapshot"
        return self._request("GET", url, params={"feed": self.config.data_feed})

    def get_snapshots(self, symbols: List[str]) -> Dict[str, Dict]:
        """Batch snapshots for up to 100 symbols at a time."""
        if not symbols:
            return {}
        all_snaps: Dict[str, Dict] = {}
        batch_size = 100
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i: i + batch_size]
            url = f"{self.data_url}/v2/stocks/snapshots"
            params = {"symbols": ",".join(batch), "feed": self.config.data_feed}
            result = self._request("GET", url, params=params)
            if result and isinstance(result, dict):
                all_snaps.update(result)
        return all_snaps

    # ------------------------------------------------------------------
    # Reference data — Assets (seed universe for the screener)
    # ------------------------------------------------------------------

    def list_assets(self, status: str = "active",
                    asset_class: str = "us_equity") -> List[str]:
        """Tradable, active US-equity symbols on the major exchanges — the seed
        universe the screener filters down. Drops OTC and non-plain symbols
        (preferreds/warrants/units with dots or >5 chars). Returns a sorted
        symbol list, or [] on failure so callers can fall back to a static seed.
        """
        result = self._request(
            "GET", f"{self.base_url}/v2/assets",
            params={"status": status, "asset_class": asset_class},
        )
        if not isinstance(result, list):
            return []
        syms = set()
        for a in result:
            if not isinstance(a, dict) or not a.get("tradable"):
                continue
            if a.get("exchange") == "OTC":
                continue
            sym = (a.get("symbol") or "").upper()
            if sym.isalpha() and 1 <= len(sym) <= 5:
                syms.add(sym)
        return sorted(syms)

    # ------------------------------------------------------------------
    # Market Data — Latest Quote / Trade
    # ------------------------------------------------------------------

    def get_latest_quote(self, symbol: str) -> Optional[Dict]:
        url = f"{self.data_url}/v2/stocks/{symbol}/quotes/latest"
        return self._request("GET", url, params={"feed": self.config.data_feed})

    def get_latest_trade(self, symbol: str) -> Optional[Dict]:
        url = f"{self.data_url}/v2/stocks/{symbol}/trades/latest"
        return self._request("GET", url, params={"feed": self.config.data_feed})

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_equity(self) -> float:
        acct = self.get_account()
        return float(acct.get("equity", 0)) if acct else 0.0

    def get_buying_power(self) -> float:
        acct = self.get_account()
        return float(acct.get("buying_power", 0)) if acct else 0.0

    def mid_price(self, symbol: str) -> Optional[float]:
        """Get mid price from latest quote."""
        q = self.get_latest_quote(symbol)
        if not q:
            return None
        trade = q.get("trade") or q
        bp = float(trade.get("bp", 0) or q.get("ap", 0))
        ap = float(trade.get("ap", 0) or q.get("bp", 0))
        if bp > 0 and ap > 0:
            return (bp + ap) / 2.0
        # Fallback to last trade
        lt = self.get_latest_trade(symbol)
        if lt and lt.get("trade"):
            return float(lt["trade"].get("p", 0))
        return None
