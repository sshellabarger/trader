"""
Kalshi recorder behavior: snapshot line schema, hot-vs-base cadence, empty
series warning, book snapshots near close, settlement dedupe across sweeps and
restarts. Uses a stub client and an injected clock — no network, no sleeps.
"""
import json
import os
from datetime import datetime, timezone

from trader.kalshi.config import KalshiConfig
from trader.kalshi.recorder import KalshiRecorder

NOW = 1_785_300_000.0  # fixed epoch for deterministic file names / cadence


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _market(ticker, close_epoch, **extra):
    m = {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "close_time": _iso(close_epoch),
        "status": "active",
        "yes_bid": 44, "yes_ask": 46, "no_bid": 54, "no_ask": 56,
        "last_price": 45, "volume": 1000, "volume_24h": 250,
        "open_interest": 900,
    }
    m.update(extra)
    return m


class StubClient:
    def __init__(self, markets_by_series=None, settlements_by_series=None,
                 books=None):
        self.markets_by_series = markets_by_series or {}
        self.settlements_by_series = settlements_by_series or {}
        self.books = books or {}
        self.book_requests = []

    def get_markets(self, series_ticker="", status="", **kwargs):
        return list(self.markets_by_series.get(series_ticker, []))

    def get_settlements(self, series_ticker, since_epoch):
        return list(self.settlements_by_series.get(series_ticker, []))

    def get_orderbook(self, ticker, depth=10):
        self.book_requests.append(ticker)
        return self.books.get(ticker, {"yes": [], "no": []})

    def get_exchange_status(self):
        return {"exchange_active": True}


def _config(tmp_path, series="KXTEST", **overrides):
    cfg = KalshiConfig(series=series, data_dir=str(tmp_path / "kalshi"))
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _lines(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _snapshot_file(cfg):
    day = datetime.fromtimestamp(NOW, tz=timezone.utc).strftime("%Y%m%d")
    return os.path.join(cfg.data_dir, f"snapshots-{day}.jsonl")


def test_snapshot_lines_have_compact_schema(tmp_path):
    far_close = NOW + 48 * 3600  # comfortably outside hot/book windows
    client = StubClient({"KXTEST": [_market("KXTEST-26AUG01-T90", far_close)]})
    rec = KalshiRecorder(client, _config(tmp_path), now_fn=lambda: NOW)

    rec.poll_series("KXTEST")

    lines = _lines(_snapshot_file(rec.config))
    assert len(lines) == 1
    md = lines[0]
    assert md["type"] == "md"
    assert md["ticker"] == "KXTEST-26AUG01-T90"
    assert md["yes_bid"] == 44 and md["yes_ask"] == 46
    assert md["vol24"] == 250 and md["oi"] == 900
    assert md["t"].startswith("2026-")


def test_hot_market_gets_fast_cadence_and_book(tmp_path):
    soon = NOW + 30 * 60          # closes in 30 min -> hot + book window
    client = StubClient(
        {"KXTEST": [_market("KXTEST-26JUL28-T90", soon)]},
        books={"KXTEST-26JUL28-T90": {"yes": [[45, 10], [44, 20]],
                                      "no": [[54, 5]]}},
    )
    cfg = _config(tmp_path)
    rec = KalshiRecorder(client, cfg, now_fn=lambda: NOW)

    candidates = rec.poll_series("KXTEST")
    rec.snapshot_books(candidates)

    assert rec._next_poll["KXTEST"] == NOW + cfg.hot_interval_sec
    types = [ln["type"] for ln in _lines(_snapshot_file(cfg))]
    assert types == ["md", "book"]
    assert client.book_requests == ["KXTEST-26JUL28-T90"]


def test_quiet_market_gets_base_cadence_and_no_book(tmp_path):
    far_close = NOW + 48 * 3600
    client = StubClient({"KXTEST": [_market("KXTEST-26AUG01-T90", far_close)]})
    cfg = _config(tmp_path)
    rec = KalshiRecorder(client, cfg, now_fn=lambda: NOW)

    candidates = rec.poll_series("KXTEST")
    rec.snapshot_books(candidates)

    assert candidates == []
    assert rec._next_poll["KXTEST"] == NOW + cfg.base_interval_sec
    assert client.book_requests == []


def test_book_budget_prioritizes_soonest_close(tmp_path):
    m1 = _market("KXTEST-26JUL28-A", NOW + 90 * 60)
    m2 = _market("KXTEST-26JUL28-B", NOW + 10 * 60)   # closes first
    client = StubClient({"KXTEST": [m1, m2]})
    cfg = _config(tmp_path, book_max_per_cycle=1)
    rec = KalshiRecorder(client, cfg, now_fn=lambda: NOW)

    rec.snapshot_books(rec.poll_series("KXTEST"))

    assert client.book_requests == ["KXTEST-26JUL28-B"]


def test_empty_series_warns_once_per_day_and_keeps_polling(tmp_path, caplog):
    client = StubClient({})  # nothing listed
    cfg = _config(tmp_path)
    rec = KalshiRecorder(client, cfg, now_fn=lambda: NOW)

    with caplog.at_level("WARNING"):
        rec.poll_series("KXTEST")
        rec.poll_series("KXTEST")

    warnings = [r for r in caplog.records if "no open markets" in r.message]
    assert len(warnings) == 1
    assert rec._next_poll["KXTEST"] == NOW + cfg.base_interval_sec


def test_settlement_sweep_dedupes_across_sweeps_and_restarts(tmp_path):
    settle = {"ticker": "KXTEST-26JUL27-T88", "event_ticker": "KXTEST-26JUL27",
              "result": "yes", "close_time": _iso(NOW - 3600)}
    client = StubClient(settlements_by_series={"KXTEST": [settle]})
    cfg = _config(tmp_path)

    rec = KalshiRecorder(client, cfg, now_fn=lambda: NOW)
    assert rec.sweep_settlements() == 1
    assert rec.sweep_settlements() == 0     # same sweep result deduped

    # Restart: dedupe set is rebuilt from disk, so still no double-write.
    rec2 = KalshiRecorder(client, cfg, now_fn=lambda: NOW)
    assert rec2.sweep_settlements() == 0

    path = os.path.join(cfg.data_dir, "settlements.jsonl")
    lines = _lines(path)
    assert len(lines) == 1
    assert lines[0]["result"] == "yes"
    assert lines[0]["type"] == "settle"


def test_cycle_polls_due_series_and_sweeps_once(tmp_path):
    far_close = NOW + 48 * 3600
    client = StubClient(
        {"KXA": [_market("KXA-26AUG01-T1", far_close)],
         "KXB": [_market("KXB-26AUG01-T1", far_close)]})
    cfg = _config(tmp_path, series="KXA,KXB")
    clock = {"now": NOW}
    rec = KalshiRecorder(client, cfg, now_fn=lambda: clock["now"])

    rec.cycle()          # both series due at 0.0 -> polled; sweep runs
    first_lines = len(_lines(_snapshot_file(cfg)))
    assert first_lines == 2

    clock["now"] = NOW + 5   # nothing due yet (base interval is 60s)
    rec.cycle()
    assert len(_lines(_snapshot_file(cfg))) == first_lines

    clock["now"] = NOW + cfg.base_interval_sec + 1
    rec.cycle()
    assert len(_lines(_snapshot_file(cfg))) == first_lines + 2
