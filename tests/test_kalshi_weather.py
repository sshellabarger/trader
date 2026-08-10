"""
Weather model math: kernel CDF behavior, bucket-ladder coherence, spec
inference from tickers (the T-direction gotcha), correction knobs, payload
parsing against the real Open-Meteo key shape, and the net-edge rule.
No network anywhere.
"""
import math

from trader.kalshi.weather import (MarketSpec, STATIONS, WeatherConfig,
                                   corrected_pools, evaluate,
                                   fair_value_cents, fetch_ensemble_daymax,
                                   parse_event_date, prob_le,
                                   spec_from_api_market, specs_from_tickers)


def _cfg(**kw):
    cfg = WeatherConfig(**kw)
    cfg.bias = {s: 0.0 for s in STATIONS}
    cfg.spread = {s: 1.0 for s in STATIONS}
    return cfg


POOLS = {"gfs": [84.0, 85.0, 86.0], "ecmwf": [85.0, 86.0, 87.0]}


def test_prob_le_monotone_and_bounded():
    cfg = _cfg()
    lo, mid, hi = (prob_le(x, POOLS, cfg) for x in (70.0, 85.5, 100.0))
    assert 0.0 <= lo < mid < hi <= 1.0
    assert lo < 0.01 and hi > 0.99
    assert abs(prob_le(85.5, {"gfs": [85.5], "ecmwf": []}, cfg) - 0.5) < 1e-9


def test_bucket_ladder_sums_to_one():
    # Real CHI ladder shape: low tail T80, buckets B81.5..B86.5 offset by 2,
    # high tail T87 — together they cover every integer outcome exactly once.
    cfg = _cfg()
    tickers = ["X-26AUG11-T80", "X-26AUG11-B80.5", "X-26AUG11-B82.5",
               "X-26AUG11-B84.5", "X-26AUG11-B86.5", "X-26AUG11-T87"]
    specs, skipped = specs_from_tickers(tickers)
    assert not skipped and len(specs) == 6
    total = sum(fair_value_cents(s, POOLS, cfg) for s in specs.values())
    assert abs(total - 100.0) < 0.5


def test_spec_inference_direction():
    specs, _ = specs_from_tickers(
        ["E-T87", "E-T80", "E-B84.5"])
    assert specs["E-T80"].kind == "le" and specs["E-T80"].cap == 80.0
    assert specs["E-T87"].kind == "ge" and specs["E-T87"].floor == 87.0
    assert specs["E-B84.5"].kind == "between"
    assert specs["E-B84.5"].floor == 84.0 and specs["E-B84.5"].cap == 85.0
    # Single T with no buckets is ambiguous -> refused, not guessed.
    specs2, skipped2 = specs_from_tickers(["E-T90"])
    assert not specs2 and skipped2 == ["E-T90"]


def test_spec_from_api_market_matches_live_fields():
    ge = spec_from_api_market({"ticker": "A", "strike_type": "greater",
                               "floor_strike": 87, "cap_strike": None})
    le = spec_from_api_market({"ticker": "B", "strike_type": "less",
                               "floor_strike": None, "cap_strike": 80})
    bt = spec_from_api_market({"ticker": "C", "strike_type": "between",
                               "floor_strike": 86, "cap_strike": 87})
    assert ge.kind == "ge" and ge.floor == 87.0
    assert le.kind == "le" and le.cap == 80.0
    assert bt.kind == "between" and bt.floor == 86.0 and bt.cap == 87.0


def test_between_uses_half_degree_edges():
    # B86.5 = {86, 87}: with all mass exactly at 86, probability ~1.
    cfg = _cfg(kernel_sigma=0.3)
    spec = MarketSpec("t", "between", floor=86.0, cap=87.0)
    fair = fair_value_cents(spec, {"gfs": [86.0], "ecmwf": []}, cfg)
    assert fair > 90.0


def test_correction_shifts_and_widens():
    warm = corrected_pools(POOLS, bias=2.0, spread=1.0)
    assert warm["gfs"][0] == 86.0
    wide = corrected_pools(POOLS, bias=0.0, spread=2.0)
    members = [x for p in wide.values() for x in p]
    mu = sum(members) / len(members)
    assert abs(mu - 85.5) < 1e-9           # spread preserves the mean
    assert max(members) - min(members) > 5.9


def test_fetch_parse_single_model_key_shape():
    # Single-model responses drop the model suffix (real shape 2026-08-10);
    # the fetcher must classify members by the model it REQUESTED.
    hours = list(range(24))

    def fake(url, params):
        if params["models"] == "gfs_seamless":
            return {"hourly": {
                "time": hours,
                "temperature_2m": [70 + h % 12 for h in hours],
                "temperature_2m_member01": [71 + h % 12 for h in hours]}}
        return {"hourly": {
            "time": hours,
            "temperature_2m": [72 + h % 12 for h in hours],
            "temperature_2m_member01": [None] * 20 + [70, 71, 72, 73]}}

    pools = fetch_ensemble_daymax("KXHIGHCHI", "2026-08-10", fetch_json=fake)
    assert len(pools["gfs"]) == 2 and pools["gfs"][0] == 81
    assert len(pools["ecmwf"]) == 1        # gappy member dropped


def test_net_edge_rule():
    spec = MarketSpec("t", "ge", floor=60.0)
    out = evaluate(spec, fair=60.0, yes_bid=54, yes_ask=56)
    # mid 55, half-spread 1, fee ceil(0.07*55*45/100)=2 -> 60-55-2-1 = 2.0
    assert out["mid"] == 55.0 and out["fee"] == 2
    assert abs(out["net_edge"] - 2.0) < 1e-9 and out["side"] == "yes"


def test_parse_event_date():
    assert parse_event_date("KXHIGHCHI-26AUG11") == "2026-08-11"
    assert parse_event_date("KXHIGHNY-26DEC03") == "2026-12-03"
    assert parse_event_date("KXCPI-26JUL") is None
