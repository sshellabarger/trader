"""
Gate scorer: settlement join, pre-game filter, one Brier row and one paper
trade per game, taker fill math, gate verdict. No network.
"""
from trader.kalshi.gate import (load_jsonl, outcomes, score, taker_pnl_cents,
                                verdict)

EV = "KXMLBGAME-26SEP051611MIACHC"
HOME, AWAY = f"{EV}-CHC", f"{EV}-MIA"
START = "2026-09-05T20:11:00Z"


def scan(ticker, t, fair, bid, ask, edge, side="yes", event=EV, start=START):
    return {"t": t, "series": "KXMLBGAME", "ticker": ticker, "event": event,
            "commence": start, "fair": fair, "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2, "net_edge": edge, "side": side}


def settle(ticker, result):
    return {"type": "settle", "ticker": ticker, "result": result}


def test_outcomes_skip_void_and_blank():
    y = outcomes([settle("A", "yes"), settle("B", "no"), settle("C", "void"),
                  settle("D", None)])
    assert y == {"A": 1, "B": 0}


def test_taker_fill_math_yes_win_and_no_loss():
    win = taker_pnl_cents("yes", 54, 56, 1, 2500)
    assert win["n"] == 43 and win["fee"] == 75 and win["pnl_cents"] == 1817
    loss = taker_pnl_cents("no", 54, 56, 1, 2500)
    assert loss["price"] == 46 and loss["n"] == 52
    assert loss["pnl_cents"] == -52 * 46 - loss["fee"]


def test_postgame_scans_ignored_and_last_pregame_home_row_scored():
    scans = [
        scan(AWAY, "2026-09-05T12:00:00+00:00", 40.0, 41, 43, -1.5, "no"),
        scan(HOME, "2026-09-05T12:00:00+00:00", 58.0, 57, 59, -1.8),
        scan(HOME, "2026-09-05T19:00:00+00:00", 62.0, 60, 62, -1.2),
        scan(HOME, "2026-09-05T21:00:00+00:00", 99.0, 90, 92, 5.0),  # in-game
    ]
    r = score(scans, [settle(HOME, "yes"), settle(AWAY, "no")])
    assert r["pregame_rows"] == 3 and r["games"] == 1
    assert r["brier_pairs"] == [(0.62, 0.61, 1)]
    assert r["trades"] == []            # the only 5c edge was after first pitch


def test_one_trade_per_game_at_first_qualifying_scan_and_pending():
    ev2 = "KXMLBGAME-26SEP051910PITMIL"
    scans = [
        scan(HOME, "2026-09-05T12:00:00+00:00", 60.0, 54, 56, 2.0),
        scan(HOME, "2026-09-05T13:00:00+00:00", 70.0, 54, 56, 12.0),
        scan(f"{ev2}-MIL", "2026-09-05T13:00:00+00:00", 70.0, 54, 56, 3.0,
             event=ev2, start="2026-09-05T23:10:00Z"),
    ]
    r = score(scans, [settle(HOME, "yes")])
    assert len(r["trades"]) == 1
    assert r["trades"][0]["edge"] == 2.0 and r["trades"][0]["pnl_cents"] == 1817
    assert r["pending"] == [f"{ev2}-MIL"]


def test_verdict_fails_small_sample_and_reports_brier():
    scans = [scan(HOME, "2026-09-05T12:00:00+00:00", 60.0, 54, 56, 2.0)]
    v = verdict(score(scans, [settle(HOME, "yes")]))
    assert v["n_trades"] == 1 and v["pf"] == float("inf")
    assert v["checks"] == {"trades": False, "profit_factor": True,
                           "brier": True}          # 0.16 vs 0.2025
    assert not v["passed"]


def test_empty_sample_fails_every_check():
    v = verdict(score([], []))
    assert v["pf"] is None and v["model_brier"] is None
    assert v["checks"] == {"trades": False, "profit_factor": False,
                           "brier": False}


def test_load_jsonl_skips_torn_line(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"a": 1}\n{"b": 2}\n{"c": ', encoding="utf-8")
    assert load_jsonl(str(p)) == [{"a": 1}, {"b": 2}]
    assert load_jsonl(str(tmp_path / "missing.jsonl")) == []
