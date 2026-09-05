"""
Kalshi sports devig model (phase 0 — measurement only, no orders).

Fair values for KXMLBGAME/KXNFLGAME winner markets from Pinnacle moneylines
(The Odds API, regions=eu), devigged with the power method. The signal is
pure arithmetic per the 2026-07-28 decision: fair − mid − fee − half-spread.

The Odds API free tier serves LIVE odds only (no history), so there is no
retrospective backtest against the recorded tape. Instead every scan appends
a scoreboard row (fair vs Kalshi quote, timestamped) to a JSONL log; rows
join settlements later, accumulating the forward sample the Oct-1 gate
needs. The market baseline to beat, measured on 427 settled games from the
recorder's own tape: Brier 0.2411 at game start.

Mapping gotchas:
- Kalshi MLB tickers embed {ETdate}{HHMM}{AWAY}{HOME} with variable-length
  team codes ("KCCHC" = KC + CHC); the splitter tries every split where both
  halves are known codes. NFL tickers have no time component.
- Team-name→code tables below are validated at RUNTIME: any game or ticker
  that fails to match is reported, never silently guessed.
- Doubleheaders: two tickers share a team pair + date; the ticker whose
  embedded time is closest to the odds feed's commence_time wins.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .config import taker_fee_cents

logger = logging.getLogger(__name__)

ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds/"
SPORT_KEYS = {"KXMLBGAME": "baseball_mlb", "KXNFLGAME": "americanfootball_nfl"}
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}

MLB_CODES = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Athletics": "ATH",
    "Oakland Athletics": "ATH", "Sacramento Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD", "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}
NFL_CODES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN",
    "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE",
    "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WSH",
}
CODES = {"KXMLBGAME": MLB_CODES, "KXNFLGAME": NFL_CODES}


def devig_power(implied: List[float]) -> List[float]:
    """Power-method devig: find k with sum(q_i^k) = 1, fair_i = q_i^k.
    Falls back to proportional when inputs are degenerate. Handles the
    favorite-longshot bias better than proportional at higher vig."""
    if any(q <= 0.0 or q >= 1.0 for q in implied) or len(implied) < 2:
        s = sum(implied) or 1.0
        return [q / s for q in implied]
    lo, hi = 0.5, 8.0
    for _ in range(80):
        k = (lo + hi) / 2.0
        s = sum(q ** k for q in implied)
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    fair = [q ** k for q in implied]
    s = sum(fair)
    return [f / s for f in fair]


def fair_probs_from_game(game: dict, book: str = "pinnacle") -> Optional[Dict[str, float]]:
    """{team_name: fair_prob} from one Odds API game entry, or None."""
    for bk in game.get("bookmakers") or []:
        if bk.get("key") != book:
            continue
        for mkt in bk.get("markets") or []:
            if mkt.get("key") != "h2h":
                continue
            outs = mkt.get("outcomes") or []
            prices = [o.get("price") for o in outs]
            if len(outs) < 2 or any(not isinstance(p, (int, float)) or p <= 1.0
                                    for p in prices):
                return None
            fair = devig_power([1.0 / p for p in prices])
            return {o["name"]: f for o, f in zip(outs, fair)}
    return None


def fetch_odds(series: str, api_key: str,
               fetch_json: Optional[Callable] = None) -> List[dict]:
    """One credit per call. Returns the raw game list."""
    def _default(url, params):
        import requests
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        remaining = resp.headers.get("x-requests-remaining")
        if remaining is not None:
            logger.info(f"odds api credits remaining: {remaining}")
        return resp.json()
    fetch = fetch_json or _default
    url = ODDS_API_URL.format(sport=SPORT_KEYS[series])
    data = fetch(url, {"regions": "eu", "markets": "h2h",
                       "bookmakers": "pinnacle", "oddsFormat": "decimal",
                       "apiKey": api_key})
    return data if isinstance(data, list) else []


def split_team_blob(blob: str, codes: set) -> Optional[Tuple[str, str]]:
    """'KCCHC' -> ('KC', 'CHC') where both halves are known codes."""
    hits = [(blob[:i], blob[i:]) for i in range(2, len(blob) - 1)
            if blob[:i] in codes and blob[i:] in codes]
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def parse_event(series: str, event: str) -> Optional[dict]:
    """KXMLBGAME-26SEP051910MIACHC -> {date_et, start_utc?, away, home}."""
    m = re.match(rf"^{series}-(\d{{2}})([A-Z]{{3}})(\d{{2}})(\d{{4}})?([A-Z]+)$",
                 event or "")
    if not m or m.group(2) not in _MON:
        return None
    teams = split_team_blob(m.group(5), set(CODES[series].values()))
    if not teams:
        return None
    date_et = f"20{m.group(1)}-{_MON[m.group(2)]:02d}-{int(m.group(3)):02d}"
    start = None
    if m.group(4):
        hh, mi = int(m.group(4)[:2]), int(m.group(4)[2:])
        start = datetime(2000 + int(m.group(1)), _MON[m.group(2)],
                         int(m.group(3)), hh, mi,
                         tzinfo=timezone.utc).timestamp() + 4 * 3600  # ET->UTC
    return {"date_et": date_et, "start_utc": start,
            "away": teams[0], "home": teams[1]}


def match_games_to_events(series: str, games: List[dict],
                          events: List[str]) -> Tuple[Dict[str, dict], List[str]]:
    """event_ticker -> matched game. Unmatched games reported, not guessed."""
    codes = CODES[series]
    parsed = {e: parse_event(series, e) for e in events}
    out: Dict[str, dict] = {}
    unmatched: List[str] = []
    for g in games:
        away = codes.get(g.get("away_team", ""))
        home = codes.get(g.get("home_team", ""))
        ct = g.get("commence_time", "")
        if not away or not home or not ct:
            unmatched.append(f"{g.get('away_team')}@{g.get('home_team')}: unknown team code")
            continue
        start = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        date_et = (start - timedelta(hours=4)).strftime("%Y-%m-%d")
        cands = [e for e, p in parsed.items() if p
                 and p["away"] == away and p["home"] == home
                 and p["date_et"] == date_et]
        if not cands:
            unmatched.append(f"{away}@{home} {date_et}: no Kalshi event")
            continue
        if len(cands) > 1:  # doubleheader: nearest embedded start time wins
            cands.sort(key=lambda e: abs((parsed[e]["start_utc"] or 0)
                                         - start.timestamp()))
        out[cands[0]] = g
    return out, unmatched


def evaluate(fair: float, yes_bid: int, yes_ask: int) -> dict:
    """net_edge = |fair − mid| − taker_fee(mid) − half_spread (cents)."""
    mid = (yes_bid + yes_ask) / 2.0
    half = (yes_ask - yes_bid) / 2.0
    fee = taker_fee_cents(int(round(mid)), 1)
    edge = abs(fair - mid) - fee - half
    return {"fair": round(fair, 1), "bid": yes_bid, "ask": yes_ask,
            "mid": mid, "fee": fee, "net_edge": round(edge, 2),
            "side": "yes" if fair > mid else "no"}


def append_scan_log(path: str, rows: List[dict]):
    """Forward-sample scoreboard: joined against settlements later."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
