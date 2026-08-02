"""
Kalshi REST client — mirrors the AlpacaBroker shape (requests.Session, proactive
throttle, retry with 429 Retry-After, defensive returns) so the rest of the bot
reads the same either side of the venue boundary.

Market data endpoints are PUBLIC: the recorder runs with no credentials at all.
Portfolio/trading endpoints require RSA-PSS request signing (Kalshi's scheme:
sign `timestamp_ms + METHOD + path` with the account's private key). Signing
needs the `cryptography` package, which is intentionally NOT in
requirements.txt yet — the phase-0 recorder must not widen the deployed
container's supply chain. Install and pin it deliberately when order code is
actually wired (demo exchange first), per the requirements.txt policy.

Prices everywhere are integer CENTS (Kalshi native). No float money.
"""
from __future__ import annotations

import base64
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from .config import KalshiConfig

logger = logging.getLogger(__name__)

# Kalshi tickers land in URL paths (e.g. KXHIGHCHI-26JUL29-B86.5), so anything
# that reaches the client from outside must be ticker-shaped — same
# defense-in-depth as broker._guard_symbol.
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,99}$")


def valid_ticker(ticker: object) -> bool:
    return isinstance(ticker, str) and bool(_TICKER_RE.match(ticker))


def price_cents(market: Dict, field: str) -> Optional[int]:
    """Price in integer cents from either Kalshi payload generation: legacy
    integer-cent fields ('yes_bid': 5) or 2026 dollar-strings
    ('yes_bid_dollars': '0.0500'). The live API dropped the legacy fields
    (observed 2026-08-02) and the recorder's first week captured no prices at
    all; reading both generations is the fix. None when absent/unparseable."""
    v = market.get(field)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(round(v))
    v = market.get(f"{field}_dollars")
    if v is None:
        return None
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        return None


def quantity_fp(market: Dict, field: str) -> Optional[float]:
    """Contract quantity from either generation: legacy ints ('volume': 1000)
    or fractional '_fp' strings ('volume_fp': '1181.87'). Floats — Kalshi
    introduced fractional contracts with the _fp payloads."""
    v = market.get(field)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    v = market.get(f"{field}_fp")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _levels_from_dollars(levels) -> List[List[float]]:
    """[["0.0300", "295.08"], ...] -> [[3, 295.08], ...] (cents, contracts)."""
    out: List[List[float]] = []
    for lv in levels or []:
        try:
            out.append([int(round(float(lv[0]) * 100)), float(lv[1])])
        except (TypeError, ValueError, IndexError):
            continue
    return out


def _guard_ticker(ticker: object, where: str) -> bool:
    if valid_ticker(ticker):
        return True
    logger.error(f"{where}: rejecting invalid ticker {ticker!r}")
    return False


class KalshiClient:
    """Synchronous Kalshi trade-api/v2 client."""

    def __init__(self, config: Optional[KalshiConfig] = None):
        self.config = config or KalshiConfig()
        self.base_url = self.config.base_url.rstrip("/")
        # The signed path must include the API prefix (e.g. /trade-api/v2/...),
        # which is whatever path component the base URL carries.
        self._path_prefix = urlparse(self.base_url).path
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._min_interval = 1.0 / max(0.5, self.config.requests_per_second)
        self._last_request = 0.0
        self._private_key = None  # lazy-loaded on first authed call

    # ------------------------------------------------------------------
    # Internal request plumbing
    # ------------------------------------------------------------------

    def _throttle(self):
        """Simple spacing limiter: never exceed requests_per_second."""
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _request(
        self, method: str, path: str, params: Optional[Dict] = None,
        json_body: Optional[Dict] = None, auth: bool = False, retries: int = 2,
    ) -> Optional[Any]:
        url = f"{self.base_url}{path}"
        for attempt in range(retries + 1):
            self._throttle()
            headers = self._auth_headers(method, path) if auth else None
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body,
                    headers=headers, timeout=15,
                )
                if resp.status_code == 429:
                    wait = min(30, int(resp.headers.get("Retry-After", 2) or 2))
                    logger.warning(f"Kalshi rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {}
            except requests.exceptions.HTTPError as exc:
                body = exc.response.text[:200] if exc.response is not None else ""
                code = exc.response.status_code if exc.response is not None else "?"
                logger.error(f"Kalshi HTTP {code} {method} {path}: {body}")
                return None
            except requests.exceptions.RequestException as exc:
                logger.error(f"Kalshi request error {method} {path}: {exc}")
                if attempt < retries:
                    time.sleep(1)
                    continue
                return None
        return None

    # ------------------------------------------------------------------
    # Auth (trading/portfolio only — market data never calls this)
    # ------------------------------------------------------------------

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        if not self.config.api_key_id or not self.config.private_key_path:
            raise RuntimeError(
                "Kalshi auth requested but KALSHI_API_KEY_ID / "
                "KALSHI_PRIVATE_KEY_PATH are not configured")
        if self._private_key is None:
            self._private_key = self._load_private_key(self.config.private_key_path)
        ts_ms = str(int(time.time() * 1000))
        # Sign timestamp + METHOD + full path (prefix included, query excluded).
        message = ts_ms + method.upper() + self._path_prefix + path.split("?")[0]
        signature = self._sign_pss(message)
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        }

    @staticmethod
    def _load_private_key(path: str):
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError(
                "Kalshi trading auth needs the 'cryptography' package. It is "
                "deliberately not in requirements.txt (recorder is public-data "
                "only); install and pin it when wiring order code.") from exc
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)

    def _sign_pss(self, message: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        sig = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("utf-8")

    # ------------------------------------------------------------------
    # Exchange / reference data (public)
    # ------------------------------------------------------------------

    def get_exchange_status(self) -> Optional[Dict]:
        return self._request("GET", "/exchange/status")

    def get_series_list(self, category: str = "") -> List[Dict]:
        params = {"category": category} if category else None
        result = self._request("GET", "/series", params=params)
        if not isinstance(result, dict):
            return []
        return result.get("series") or []

    # ------------------------------------------------------------------
    # Markets (public)
    # ------------------------------------------------------------------

    def get_markets(
        self,
        series_ticker: str = "",
        event_ticker: str = "",
        status: str = "",
        tickers: Optional[List[str]] = None,
        min_close_ts: Optional[int] = None,
        limit: int = 1000,
        max_pages: int = 20,
    ) -> List[Dict]:
        """Fetch markets with cursor paging. Returns [] on failure so callers
        can treat 'venue unreachable' and 'nothing listed' the same way the
        broker's list calls do."""
        params: Dict[str, Any] = {"limit": min(1000, max(1, limit))}
        if series_ticker:
            if not _guard_ticker(series_ticker, "get_markets"):
                return []
            params["series_ticker"] = series_ticker
        if event_ticker:
            if not _guard_ticker(event_ticker, "get_markets"):
                return []
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        if tickers:
            good = [t for t in tickers if _guard_ticker(t, "get_markets")]
            if not good:
                return []
            params["tickers"] = ",".join(good)
        if min_close_ts is not None:
            params["min_close_ts"] = int(min_close_ts)

        out: List[Dict] = []
        cursor = ""
        for _ in range(max_pages):
            if cursor:
                params["cursor"] = cursor
            result = self._request("GET", "/markets", params=params)
            if not isinstance(result, dict):
                break
            page = result.get("markets") or []
            out.extend(page)
            cursor = result.get("cursor") or ""
            if not cursor or not page:
                break
        return out

    def get_market(self, ticker: str) -> Optional[Dict]:
        if not _guard_ticker(ticker, "get_market"):
            return None
        result = self._request("GET", f"/markets/{ticker}")
        if not isinstance(result, dict):
            return None
        return result.get("market")

    def get_orderbook(self, ticker: str, depth: int = 10) -> Optional[Dict]:
        """Order book as {"yes": [[price_cents, qty], ...], "no": [...]}.
        Accepts both payload generations: legacy {"orderbook": {"yes":
        [[cents, count], ...]}} and the 2026 {"orderbook_fp": {"yes_dollars":
        [["0.0300", "295.08"], ...]}} (dollar-string prices, fractional
        quantities). Either side can be missing/None on an empty book —
        normalized to []."""
        if not _guard_ticker(ticker, "get_orderbook"):
            return None
        result = self._request("GET", f"/markets/{ticker}/orderbook",
                               params={"depth": depth})
        if not isinstance(result, dict):
            return None
        book = result.get("orderbook")
        if isinstance(book, dict) and ("yes" in book or "no" in book):
            return {"yes": book.get("yes") or [], "no": book.get("no") or []}
        fp = result.get("orderbook_fp") or {}
        return {"yes": _levels_from_dollars(fp.get("yes_dollars")),
                "no": _levels_from_dollars(fp.get("no_dollars"))}

    def get_trades(self, ticker: str = "", min_ts: Optional[int] = None,
                   limit: int = 100) -> List[Dict]:
        """Public trade tape — later the honest check that a paper maker fill
        would actually have happened (price traded through the limit)."""
        params: Dict[str, Any] = {"limit": min(1000, max(1, limit))}
        if ticker:
            if not _guard_ticker(ticker, "get_trades"):
                return []
            params["ticker"] = ticker
        if min_ts is not None:
            params["min_ts"] = int(min_ts)
        result = self._request("GET", "/markets/trades", params=params)
        if not isinstance(result, dict):
            return []
        return result.get("trades") or []

    def get_settlements(self, series_ticker: str,
                        since_epoch: int) -> List[Dict]:
        """Settled markets for a series since a timestamp — the recorder's
        nightly sweep. Settled markets carry a `result` field (yes/no)."""
        return self.get_markets(series_ticker=series_ticker, status="settled",
                                min_close_ts=since_epoch)

    # ------------------------------------------------------------------
    # Portfolio / orders (auth; DEMO EXCHANGE FIRST — see module docstring)
    # ------------------------------------------------------------------

    def get_balance(self) -> Optional[Dict]:
        return self._request("GET", "/portfolio/balance", auth=True)

    def create_order(
        self,
        ticker: str,
        action: str,            # "buy" | "sell"
        side: str,              # "yes" | "no"
        count: int,
        yes_price: Optional[int] = None,   # cents; exactly one of yes/no price
        no_price: Optional[int] = None,
        order_type: str = "limit",
        client_order_id: str = "",
    ) -> Optional[Dict]:
        """Minimal limit-order submit, for exercising the DEMO exchange.
        Phase 0 never calls this against production — the paper engine
        simulates fills from recorded books instead."""
        if not _guard_ticker(ticker, "create_order"):
            return None
        body: Dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": int(count),
            "type": order_type,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }
        if yes_price is not None:
            body["yes_price"] = int(yes_price)
        if no_price is not None:
            body["no_price"] = int(no_price)
        logger.info(f"KALSHI ORDER → {action} {count} {ticker} {side} "
                    f"(yes={yes_price} no={no_price} {order_type})")
        return self._request("POST", "/portfolio/orders", json_body=body,
                             auth=True)

    def cancel_order(self, order_id: str) -> Optional[Dict]:
        if not order_id or not isinstance(order_id, str):
            return None
        return self._request("DELETE", f"/portfolio/orders/{order_id}",
                             auth=True)
