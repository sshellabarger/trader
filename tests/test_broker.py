"""
Broker request handling around empty response bodies.

A successful single-order cancel (DELETE /v2/orders/{id}) returns 204 No Content.
The old code called resp.json() unconditionally, which raised JSONDecodeError —
a RequestException subclass — so a successful cancel was logged as an error and
retried twice (the three identical errors seen on 2026-06-22). These tests pin
the 204/empty-body path as success-without-retry while keeping JSON parsing for
normal bodies.
"""
import requests

from trader.broker import AlpacaBroker
from trader.config import BrokerConfig


def _broker():
    return AlpacaBroker(BrokerConfig(api_key="k", api_secret="s"))


def _response(status_code: int, content: bytes = b""):
    resp = requests.models.Response()
    resp.status_code = status_code
    resp._content = content
    return resp


def test_cancel_order_204_is_success_without_retry(monkeypatch):
    broker = _broker()
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        return _response(204, b"")

    monkeypatch.setattr(broker.session, "request", fake_request)
    result = broker.cancel_order("order-123")

    assert result is not None        # 204 now treated as success (was None)
    assert calls["n"] == 1           # no spurious retries on an empty body


def test_request_still_parses_json_body(monkeypatch):
    broker = _broker()

    def fake_request(method, url, **kwargs):
        return _response(200, b'{"id": "abc", "status": "open"}')

    monkeypatch.setattr(broker.session, "request", fake_request)
    out = broker.cancel_order("abc")

    assert out == {"id": "abc", "status": "open"}
