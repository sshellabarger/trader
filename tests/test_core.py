"""
Held core (core.py): targets, trend gate, drift-band planning, ownership.
Pure functions only; the broker is faked where needed.
"""
import json

from trader.advisor.model import default_targets
from trader.config import Config
from trader.core import (CorePortfolio, TrendStatus, account_targets, load_state,
                         plan_rebalance, save_state, sma_from_bars)


def _bars(closes, start_day=1):
    # daily bars dated in 2025-01 so they are all "before today"
    out = []
    for i, c in enumerate(closes):
        d = 1 + i
        out.append({"t": f"2024-{1 + d // 28:02d}-{1 + d % 28:02d}T05:00:00Z", "c": c})
    return out


# ---------------------------------------------------------------- targets

def test_default_model_sums_to_one_and_scales_to_alloc():
    w = default_targets()
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["AMZN"] == 0.05 and w["MSFT"] == 0.03
    cfg = Config().core
    cfg.enabled = True
    cfg.alloc_pct = 70
    acct = account_targets(cfg)
    assert abs(sum(acct.values()) - 0.70) < 1e-9
    assert abs(acct["AMZN"] - 0.035) < 1e-9


def test_custom_targets_override_model():
    cfg = Config().core
    cfg.alloc_pct = 50
    cfg.targets = "SPY:0.6,QQQ:0.4"
    acct = account_targets(cfg)
    assert acct == {"SPY": 0.3, "QQQ": 0.2}


# ------------------------------------------------------------------ trend

def test_sma_uses_completed_closes_only():
    bars = _bars([100.0] * 10 + [120.0])
    bars.append({"t": "2999-01-01T05:00:00Z", "c": 1.0})   # "today": ignored
    st = sma_from_bars(bars, 5, before="2999-01-01")
    assert st.close == 120.0
    assert st.above is True
    assert st.bars == 11


def test_sma_unknown_when_not_enough_bars():
    st = sma_from_bars(_bars([100.0] * 3), 5, before="2999-01-01")
    assert st.above is None and st.sma is None


# ------------------------------------------------------------------- plan

def _trend(sym, above):
    return TrendStatus(sym, 100.0, 90.0 if above else 110.0, above, 250)


def test_plan_buys_above_trend_and_exits_below():
    targets = {"SPY": 0.5, "GLD": 0.2}
    held = {"GLD": {"qty": 100, "value": 20_000, "price": 200.0}}
    trends = {"SPY": _trend("SPY", True), "GLD": _trend("GLD", False)}
    prices = {"SPY": 500.0, "GLD": 200.0}
    trades = plan_rebalance(100_000, held, targets, trends, prices,
                            trend_gate=True, band=0.25, min_dollars=200)
    d = {t.symbol: t for t in trades}
    assert d["GLD"].side == "SELL" and d["GLD"].quantity == 100
    assert "below" in d["GLD"].reason
    assert d["SPY"].side == "BUY" and d["SPY"].quantity == 100   # 50k / 500
    assert trades[0].side == "SELL"                               # sells first


def test_plan_leaves_unknown_trend_untouched():
    targets = {"SPY": 0.5}
    held = {"SPY": {"qty": 10, "value": 5_000, "price": 500.0}}
    trends = {"SPY": TrendStatus("SPY", 500.0, None, None, 3)}
    trades = plan_rebalance(100_000, held, targets, trends, {"SPY": 500.0},
                            trend_gate=True, band=0.25, min_dollars=200)
    assert trades == []


def test_plan_respects_band():
    targets = {"SPY": 0.5}
    held = {"SPY": {"qty": 90, "value": 45_000, "price": 500.0}}     # 10% under
    trends = {"SPY": _trend("SPY", True)}
    assert plan_rebalance(100_000, held, targets, trends, {"SPY": 500.0},
                          trend_gate=True, band=0.25, min_dollars=200) == []
    held = {"SPY": {"qty": 60, "value": 30_000, "price": 500.0}}     # 40% under
    tr = plan_rebalance(100_000, held, targets, trends, {"SPY": 500.0},
                        trend_gate=True, band=0.25, min_dollars=200)
    assert tr and tr[0].side == "BUY" and tr[0].quantity == 40


def test_gate_off_ignores_trend():
    targets = {"GLD": 0.2}
    trends = {"GLD": _trend("GLD", False)}
    tr = plan_rebalance(100_000, {}, targets, trends, {"GLD": 200.0},
                        trend_gate=False, band=0.25, min_dollars=200)
    assert tr and tr[0].side == "BUY" and tr[0].quantity == 100


# ---------------------------------------------------------------- ownership

class _Broker:
    def __init__(self):
        self.orders = []
        self.account = {"equity": "100000"}
        self.positions = []

    def get_account(self):
        return self.account

    def get_equity(self):
        return float(self.account["equity"])

    def get_positions(self):
        return list(self.positions)

    def get_bars(self, symbol, **kw):
        return _bars([100.0] * 200 + [110.0] * 10)   # above trend (sma ~100.5), close 110

    def mid_price(self, symbol):
        return 100.0

    def submit_order(self, **kw):
        self.orders.append(kw)
        return {"id": f"o{len(self.orders)}"}


def test_protected_symbols_and_split(tmp_path):
    cfg = Config()
    cfg.core.enabled = True
    cfg.core.state_file = str(tmp_path / "core_state.json")
    save_state(cfg.core.state_file, {"holdings": {"XYZ": 5}})
    core = CorePortfolio(cfg, _Broker())
    prot = core.protected_symbols()
    assert "XYZ" in prot and "SPY" in prot and "AMZN" in prot
    assert "TQQQ" not in prot
    core_pos, mine = core.split_positions([{"symbol": "SPY"}, {"symbol": "TQQQ"}, {"symbol": "XYZ"}])
    assert [p["symbol"] for p in core_pos] == ["SPY", "XYZ"]
    assert [p["symbol"] for p in mine] == ["TQQQ"]


def test_disabled_core_protects_only_recorded_holdings(tmp_path):
    cfg = Config()
    cfg.core.enabled = False
    cfg.core.state_file = str(tmp_path / "core_state.json")
    save_state(cfg.core.state_file, {"holdings": {"GLD": 3}})
    core = CorePortfolio(cfg, _Broker())
    assert core.protected_symbols() == {"GLD"}


def test_rebalance_places_whole_share_orders_and_records_state(tmp_path):
    cfg = Config()
    cfg.core.enabled = True
    cfg.core.alloc_pct = 70
    cfg.core.targets = "SPY:1.0"
    cfg.core.state_file = str(tmp_path / "core_state.json")
    broker = _Broker()
    core = CorePortfolio(cfg, broker)

    placed = core.rebalance()

    assert len(placed) == 1 and placed[0].side == "BUY" and placed[0].quantity == 700
    o = broker.orders[0]
    assert o["symbol"] == "SPY" and o["side"] == "buy" and o["qty"] == 700
    assert o["order_type"] == "market" and o["time_in_force"] == "day"
    assert o["client_order_id"].startswith("core-")
    state = load_state(cfg.core.state_file)
    assert state["holdings"] == {"SPY": 700.0}
    assert state["last_rebalance"]["trades"]


def test_rebalance_dry_run_places_nothing(tmp_path):
    cfg = Config()
    cfg.core.enabled = True
    cfg.core.targets = "SPY:1.0"
    cfg.core.state_file = str(tmp_path / "core_state.json")
    broker = _Broker()
    placed = CorePortfolio(cfg, broker).rebalance(dry_run=True)
    assert placed and broker.orders == []
    assert not (tmp_path / "core_state.json").exists()


def test_rebalance_disabled_is_noop(tmp_path):
    cfg = Config()
    cfg.core.enabled = False
    cfg.core.state_file = str(tmp_path / "core_state.json")
    broker = _Broker()
    assert CorePortfolio(cfg, broker).rebalance() == []
    assert broker.orders == []


def test_state_file_survives_corruption(tmp_path):
    p = tmp_path / "core_state.json"
    p.write_text("{not json")
    assert load_state(str(p)) == {"holdings": {}}
    save_state(str(p), {"holdings": {"A": 1}})
    assert json.loads(p.read_text())["holdings"] == {"A": 1}
