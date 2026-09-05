"""
Sports devig model: power-method math, team-blob splitting, event parsing
(MLB embedded times, NFL date-only), game→event matching incl. doubleheader
disambiguation, net-edge rule, real Odds-API payload shape. No network.
"""
from trader.kalshi.sports import (devig_power, evaluate, fair_probs_from_game,
                                  match_games_to_events, parse_event,
                                  split_team_blob, MLB_CODES)

# Trimmed from a real api.the-odds-api.com response, 2026-09-05.
GAME = {"home_team": "Chicago Cubs", "away_team": "Miami Marlins",
        "commence_time": "2026-09-05T20:11:00Z",
        "bookmakers": [{"key": "pinnacle", "markets": [
            {"key": "h2h", "outcomes": [
                {"name": "Chicago Cubs", "price": 1.76},
                {"name": "Miami Marlins", "price": 2.2}]}]}]}


def test_devig_power_sums_to_one_and_beats_proportional_for_favorite():
    q = [1 / 1.45, 1 / 3.1]
    fair = devig_power(q)
    assert abs(sum(fair) - 1.0) < 1e-6
    prop = [x / sum(q) for x in q]
    assert fair[0] >= prop[0] - 1e-9          # favorite keeps more mass
    assert fair[0] > 0.66 and fair[1] < 0.34


def test_devig_degenerate_falls_back_proportional():
    fair = devig_power([1.2, 0.3])            # q >= 1 is nonsense input
    assert abs(sum(fair) - 1.0) < 1e-9
    assert abs(fair[0] - 0.8) < 1e-9


def test_fair_probs_from_real_payload():
    fair = fair_probs_from_game(GAME)
    assert abs(sum(fair.values()) - 1.0) < 1e-6
    assert fair["Chicago Cubs"] > 0.55 > fair["Miami Marlins"]


def test_split_team_blob_variable_lengths():
    codes = set(MLB_CODES.values())
    assert split_team_blob("MIACHC", codes) == ("MIA", "CHC")
    assert split_team_blob("KCCHC", codes) == ("KC", "CHC")
    assert split_team_blob("BOSLAD", codes) == ("BOS", "LAD")
    assert split_team_blob("XXYYZZ", codes) is None


def test_parse_event_mlb_time_and_nfl_dateonly():
    p = parse_event("KXMLBGAME", "KXMLBGAME-26SEP051611MIACHC")
    assert p["away"] == "MIA" and p["home"] == "CHC"
    assert p["date_et"] == "2026-09-05"
    from datetime import datetime, timezone
    expected = datetime(2026, 9, 5, 20, 11, tzinfo=timezone.utc).timestamp()
    assert p["start_utc"] == expected          # 16:11 ET + 4h = 20:11Z
    n = parse_event("KXNFLGAME", "KXNFLGAME-26SEP09NESEA")
    assert n["away"] == "NE" and n["home"] == "SEA" and n["start_utc"] is None


def test_match_with_doubleheader_disambiguation():
    events = ["KXMLBGAME-26SEP051305MIACHC", "KXMLBGAME-26SEP051611MIACHC",
              "KXMLBGAME-26SEP051910PITMIL"]
    matched, unmatched = match_games_to_events("KXMLBGAME", [GAME], events)
    assert list(matched) == ["KXMLBGAME-26SEP051611MIACHC"]  # 16:11 ET = 20:11Z
    assert not unmatched
    bad = dict(GAME); bad = {**GAME, "away_team": "Springfield Isotopes"}
    matched2, unmatched2 = match_games_to_events("KXMLBGAME", [bad], events)
    assert not matched2 and "unknown team code" in unmatched2[0]


def test_net_edge_rule_matches_weather_formula():
    out = evaluate(60.0, 54, 56)
    assert out["mid"] == 55.0 and out["fee"] == 2
    assert abs(out["net_edge"] - 2.0) < 1e-9 and out["side"] == "yes"
