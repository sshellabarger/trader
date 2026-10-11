"""
Notifier, heartbeat, scheduler and IRA review — all offline.
The guarantee under test: the reports render from a fake broker without
network, say the right thing when the account is unreachable, never place
an order, and the notifier drops nothing silently (it logs) and leaks no
secret into its error text.
"""
import datetime as _dt
import json
from zoneinfo import ZoneInfo

from trader import heartbeat as hb
from trader.advisor import daily_review as ira
from trader.config import Config
from trader.notify import Notifier, NotifyConfig
from trader.scheduler import Scheduler

_ET = ZoneInfo("America/New_York")


# ----------------------------------------------------------------- notifier

class _Resp:
    def __init__(self, code=200):
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Session:
    def __init__(self, code=200):
        self.posts = []
        self.code = code

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url, data, headers))
        return _Resp(self.code)


def test_notifier_push_only_and_truncation():
    cfg = NotifyConfig(ntfy_topic="secret-topic")
    sess = _Session()
    n = Notifier(cfg, session=sess)
    res = n.send("hello", "x" * 2000)
    assert res == {"push": True}
    url, data, headers = sess.posts[0]
    assert url.endswith("/secret-topic")
    assert len(data) < 1000 and headers["Title"] == "hello"


def test_notifier_unconfigured_drops_with_warning(caplog):
    n = Notifier(NotifyConfig())
    assert n.send("s", "b") == {}
    assert "no channels configured" in caplog.text


def test_notifier_push_failure_is_contained_and_scrubbed(caplog):
    cfg = NotifyConfig(ntfy_topic="secret-topic")
    n = Notifier(cfg, session=_Session(code=500))
    assert n.send("s", "b") == {"push": False}
    assert "secret-topic" not in caplog.text or "***" in caplog.text


class _JsonSession(_Session):
    def post(self, url, data=None, headers=None, timeout=None, json=None):
        self.posts.append((url, json))
        return _Resp(self.code)


def test_notifier_telegram_full_body_in_chunks():
    cfg = NotifyConfig(telegram_token="123:abc", telegram_chat_id="42")
    sess = _JsonSession()
    n = Notifier(cfg, session=sess)
    body = "\n".join(f"line {i} <b> & more" for i in range(400))      # ~8k chars
    assert n.send("hb", body) == {"telegram": True}
    assert len(sess.posts) >= 2                                       # chunked
    url, payload = sess.posts[0]
    assert "123:abc" in url and payload["chat_id"] == "42"
    assert payload["text"].startswith("<b>hb</b>\n<pre>")
    assert "&lt;b&gt; &amp;" in payload["text"]                       # escaped
    assert all(len(p["text"]) < 4096 for _, p in sess.posts)
    joined = "".join(p["text"] for _, p in sess.posts)
    assert "line 399" in joined                                       # nothing dropped


def test_notifier_telegram_failure_scrubs_token(caplog):
    cfg = NotifyConfig(telegram_token="123:abc", telegram_chat_id="42")
    n = Notifier(cfg, session=_JsonSession(code=400))
    assert n.send("hb", "x") == {"telegram": False}
    assert "123:abc" not in caplog.text


def test_notifier_describe_hides_secrets():
    cfg = NotifyConfig(email_to="a@b.c", smtp_user="u", smtp_password="pw", ntfy_topic="t0p",
                       telegram_token="9:tok", telegram_chat_id="7")
    d = cfg.describe()
    assert "telegram" in d and "a@b.c" in d
    for secret in ("pw", "t0p", "9:tok"):
        assert secret not in d and secret not in repr(cfg)


# ----------------------------------------------------------------- heartbeat

def _bars(closes):
    out = []
    for i, c in enumerate(closes):
        d = 1 + i
        out.append({"t": f"2024-{1 + d // 28:02d}-{1 + d % 28:02d}T05:00:00Z", "c": c})
    return out


class _Broker:
    def __init__(self, account=None, positions=None, unreachable=False):
        self.account = None if unreachable else (account or {
            "equity": "101000", "last_equity": "100000", "cash": "31000",
            "buying_power": "62000", "status": "ACTIVE"})
        self.positions = positions or []
        self.orders = []

    def get_account(self):
        return self.account

    def get_equity(self):
        return float(self.account["equity"]) if self.account else 0.0

    def get_positions(self):
        return list(self.positions)

    def get_orders(self, status="open", limit=100, **kw):
        now = _dt.datetime.now(_ET).isoformat()
        return [{"status": "filled", "filled_at": now, "symbol": "SPY", "side": "buy",
                 "filled_qty": "10", "filled_avg_price": "500", "client_order_id": "core-x"}]

    def get_bars(self, symbol, timeframe="1Min", **kw):
        return _bars([100.0] * 200 + [110.0] * 10) if timeframe == "1Day" else []

    def get_portfolio_history(self, period="3M", timeframe="1D"):
        return {"timestamp": [1_700_000_000, 1_700_086_400], "equity": [100000, 101000]}

    def mid_price(self, symbol):
        return 100.0

    def is_market_open(self):
        return True

    def get_calendar(self, start, end):
        return [{"date": start}]

    def submit_order(self, **kw):
        self.orders.append(kw)
        return {"id": "x"}


def _pos(sym, qty, price):
    return {"symbol": sym, "qty": str(qty), "current_price": str(price),
            "market_value": str(qty * price), "unrealized_pl": "10", "unrealized_plpc": "0.01"}


def test_heartbeat_renders_core_and_intraday(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "trade_logs").mkdir()
    today = _dt.datetime.now(_ET).date().isoformat()
    (tmp_path / "trade_logs" / f"summary_{today}.json").write_text(
        json.dumps({"date": today, "trades": 1, "pnl": 12.5, "context": {"equity_close": 101000}}))
    cfg = Config()
    cfg.core.enabled = True
    cfg.core.targets = "SPY:1.0"
    cfg.core.state_file = str(tmp_path / "core_state.json")
    broker = _Broker(positions=[_pos("SPY", 700, 100.0), _pos("TQQQ", 50, 70.0)])

    h = hb.build(cfg, broker, with_news=False, with_ira=False)
    text = hb.render_text(h)

    assert h.ok and h.equity == 101000 and round(h.day_change_pct, 2) == 1.0
    assert [p["symbol"] for p in h.core_positions] == ["SPY"]
    assert [p["symbol"] for p in h.intraday_positions] == ["TQQQ"]
    assert h.fills_today and h.fills_today[0]["core"] is True
    assert "TRADER HEARTBEAT" in text and "core" in text and "TQQQ" in text
    assert "Next rebalance" in text
    assert h.warnings == []
    assert broker.orders == []                        # a report never trades
    assert "$101,000" in hb.subject(h)
    assert "Paper $101,000" in hb.render_push(h)


def test_heartbeat_unreachable_account_is_loud(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    h = hb.build(Config(), _Broker(unreachable=True), with_news=False, with_ira=False)
    assert h.ok is False
    assert "UNREACHABLE" in hb.render_text(h) and "UNREACHABLE" in hb.subject(h)
    assert any("no engine journal" in w for w in h.warnings)


def test_heartbeat_flags_stale_journal_and_zero_equity_close(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "trade_logs").mkdir()
    (tmp_path / "trade_logs" / "summary_2020-01-01.json").write_text(
        json.dumps({"date": "2020-01-01", "trades": 0, "pnl": 0, "context": {"equity_close": 0.0}}))
    h = hb.build(Config(), _Broker(), with_news=False, with_ira=False)
    assert any("journal last written 2020-01-01" in w for w in h.warnings)


def test_heartbeat_run_sends_once_with_high_priority_on_warning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = []

    class _N:
        config = NotifyConfig()

        def send(self, subject, body, **kw):
            sent.append((subject, kw.get("priority")))
            return {}

    hb.run(Config(), _Broker(), _N(), send=True, with_news=False, with_ira=False)
    assert len(sent) == 1 and sent[0][1] == "high"       # no journal -> warning


# ----------------------------------------------------------------- scheduler

def test_scheduler_runs_each_job_once_in_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.core.enabled = True
    cfg.core.targets = "SPY:1.0"
    cfg.core.state_file = str(tmp_path / "core_state.json")
    broker = _Broker()
    sent = []

    class _N:
        config = NotifyConfig()

        def send(self, s, b, **kw):
            sent.append(s)
            return {}

        def alert(self, s, b):
            sent.append("ALERT " + s)
            return {}

    clock = {"now": _dt.datetime(2026, 10, 12, 9, 36, tzinfo=_ET)}   # Monday
    sch = Scheduler(cfg, broker, _N(), now=lambda: clock["now"])

    sch.tick()
    sch.tick()
    assert len(broker.orders) == 1                      # rebalance ran once
    assert any("core rebalance" in s for s in sent)

    clock["now"] = _dt.datetime(2026, 10, 12, 16, 16, tzinfo=_ET)
    sch.tick()
    sch.tick()
    assert sum("$101,000" in s for s in sent) == 1          # heartbeat once

    clock["now"] = _dt.datetime(2026, 10, 12, 20, 0, tzinfo=_ET)  # outside windows
    before = len(sent)
    sch.tick()
    assert len(sent) == before


def test_scheduler_skips_rebalance_on_non_trading_day(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.core.enabled = True
    cfg.core.state_file = str(tmp_path / "core_state.json")
    broker = _Broker()
    broker.get_calendar = lambda s, e: []              # holiday
    sch = Scheduler(cfg, broker, Notifier(NotifyConfig()),
                    now=lambda: _dt.datetime(2026, 10, 12, 9, 40, tzinfo=_ET))
    sch.tick()
    assert broker.orders == []


# ----------------------------------------------------------------- IRA review

def test_ira_review_from_csv_proposes_without_trading(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
    csv = tmp_path / "pos.csv"
    csv.write_text(
        "Symbol,Description,Qty (Quantity),Price,Mkt Val (Market Value)\n"
        "AMZN,AMAZON,1000,100,\"$100,000.00\"\n"
        "JUNK,JUNK CO,100,100,\"$10,000.00\"\n"
        "SWVXX,MONEY FUND,--,--,\"$5,000.00\"\n"
    )
    broker = _Broker()
    r = ira.build_review(broker, api_key="", api_secret="", csv_path=str(csv),
                         band=0.25, min_dollars=100, with_news=False)
    text = ira.render_text(r)

    assert r.source.startswith("csv")
    assert r.total == 126_000.0 and r.cash == 5_000.0   # revalued at the 110 close
    syms = {t.symbol: t for t in r.trades}
    assert syms["JUNK"].side == "SELL" and syms["JUNK"].quantity == 100
    assert syms["AMZN"].side == "SELL"                   # 91% -> trim toward 5%
    assert any(t.side == "BUY" and t.symbol == "SPY" for t in r.trades)
    assert "propose-only" in text and "approve each trade" in text
    assert broker.orders == []


def test_ira_review_falls_back_to_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "ira_positions.json").write_text(json.dumps({
        "positions": {"SPY": {"qty": 10, "value": 4000, "price": 400}},
        "cash": 0, "total": 4000, "fetched_at": "2026-10-01T00:00:00+00:00"}))
    r = ira.build_review(_Broker(), api_key="", api_secret="", with_news=False)
    assert r.source == "cache (2026-10-01)"
    assert r.total == 1100.0                             # revalued at the last close (110)
    assert "cache (2026-10-01)" in ira.render_push(r)


def test_ira_review_without_any_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
    monkeypatch.delenv("IRA_POSITIONS_CSV", raising=False)
    r = ira.build_review(_Broker(), api_key="", api_secret="", with_news=False)
    assert r.source == "none" and r.trades == []
    assert "no IRA position source" in ira.render_text(r)
