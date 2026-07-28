"""
Kalshi client plumbing: cursor paging, param passing, 429 handling, ticker
guarding, and the no-credentials failure mode for authed endpoints. Mirrors
test_broker.py's monkeypatched-session style — no network, no sleeps.
"""
import json

import requests

from trader.kalshi.client import KalshiClient, valid_ticker
from trader.kalshi.config import KalshiConfig, taker_fee_cents


def _client(**overrides):
    cfg = KalshiConfig(**overrides)
    # Effectively disable the spacing throttle so tests never sleep.
    cfg.requests_per_second = 1e9
    c = KalshiClient(cfg)
    return c


def _response(status_code: int, payload=None):
    resp = requests.models.Response()
    resp.status_code = status_code
    resp._content = (json.dumps(payload).encode() if payload is not None
                     else b"")
    return resp


def test_get_markets_pages_through_cursor(monkeypatch):
    client = _client()
    pages = [
        {"markets": [{"ticker": "A-1"}, {"ticker": "A-2"}], "cursor": "next1"},
        {"markets": [{"ticker": "A-3"}], "cursor": ""},
    ]
    seen_params = []

    def fake_request(method, url, **kwargs):
        seen_params.append(dict(kwargs.get("params") or {}))
        return _response(200, pages[len(seen_params) - 1])

    monkeypatch.setattr(client.session, "request", fake_request)
    markets = client.get_markets(series_ticker="KXHIGHCHI", status="open")

    assert [m["ticker"] for m in markets] == ["A-1", "A-2", "A-3"]
    assert seen_params[0]["series_ticker"] == "KXHIGHCHI"
    assert seen_params[0]["status"] == "open"
    assert "cursor" not in seen_params[0]
    assert seen_params[1]["cursor"] == "next1"


def test_get_markets_stops_at_max_pages(monkeypatch):
    client = _client()
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return _response(200, {"markets": [{"ticker": f"M-{calls['n']}"}],
                               "cursor": "more"})

    monkeypatch.setattr(client.session, "request", fake_request)
    markets = client.get_markets(series_ticker="KXCPI", max_pages=3)

    assert calls["n"] == 3
    assert len(markets) == 3


def test_get_markets_returns_empty_on_http_error(monkeypatch):
    client = _client()

    def fake_request(method, url, **kwargs):
        return _response(500, {"error": "boom"})

    monkeypatch.setattr(client.session, "request", fake_request)
    assert client.get_markets(series_ticker="KXCPI") == []


def test_429_retries_after_wait_then_succeeds(monkeypatch):
    client = _client()
    naps = []
    monkeypatch.setattr("trader.kalshi.client.time.sleep",
                        lambda s: naps.append(s))
    responses = [_response(429), _response(200, {"markets": [], "cursor": ""})]

    def fake_request(method, url, **kwargs):
        resp = responses.pop(0)
        if resp.status_code == 429:
            resp.headers["Retry-After"] = "3"
        return resp

    monkeypatch.setattr(client.session, "request", fake_request)
    assert client.get_markets(series_ticker="KXCPI") == []
    assert 3 in naps


def test_ticker_guard_rejects_path_injection(monkeypatch):
    client = _client()
    called = {"n": 0}

    def fake_request(method, url, **kwargs):
        called["n"] += 1
        return _response(200, {})

    monkeypatch.setattr(client.session, "request", fake_request)
    assert client.get_market("../portfolio/balance") is None
    assert client.get_orderbook("lower-case") is None
    assert client.get_markets(series_ticker="bad ticker") == []
    assert called["n"] == 0

    assert valid_ticker("KXHIGHCHI-26JUL29-B86.5")
    assert not valid_ticker("")
    assert not valid_ticker(None)


def test_orderbook_normalizes_missing_sides(monkeypatch):
    client = _client()

    def fake_request(method, url, **kwargs):
        return _response(200, {"orderbook": {"yes": [[45, 100]], "no": None}})

    monkeypatch.setattr(client.session, "request", fake_request)
    book = client.get_orderbook("KXCPI-26JUL-T0.2")
    assert book == {"yes": [[45, 100]], "no": []}


def test_settlements_passes_status_and_min_close(monkeypatch):
    client = _client()
    seen = {}

    def fake_request(method, url, **kwargs):
        seen.update(kwargs.get("params") or {})
        return _response(200, {"markets": [], "cursor": ""})

    monkeypatch.setattr(client.session, "request", fake_request)
    client.get_settlements("KXHIGHCHI", since_epoch=1_700_000_000)
    assert seen["status"] == "settled"
    assert seen["min_close_ts"] == 1_700_000_000


def test_authed_call_without_credentials_raises():
    client = _client()
    try:
        client.get_balance()
        assert False, "expected RuntimeError for missing credentials"
    except RuntimeError as exc:
        assert "KALSHI_API_KEY_ID" in str(exc)


def test_taker_fee_matches_published_formula():
    # Peak: 0.07 * 0.5 * 0.5 = 1.75c -> ceil -> 2c per contract at 50c.
    assert taker_fee_cents(50, 1) == 2
    # 10 contracts at 50c: 17.5c -> 18c.
    assert taker_fee_cents(50, 10) == 18
    # Near-certain contracts are nearly free: 0.07*0.99*0.01 = 0.069c -> 1c.
    assert taker_fee_cents(99, 1) == 1
    # Degenerate prices never go negative.
    assert taker_fee_cents(0, 1) == 0
    assert taker_fee_cents(100, 1) == 0
