
from typing import Dict
def score_momentum(snap: dict) -> float:
    try:
        last = snap.get("latestTrade", {}).get("p") or snap.get("minuteBar", {}).get("c")
        openp = snap.get("dailyBar", {}).get("o") or snap.get("prevDailyBar", {}).get("o")
        if last and openp:
            delta = (last/openp - 1.0); return max(0.0, min(1.0, 0.5 + delta*10))
    except Exception: pass
    return 0.5
def score_mean_reversion(snap: dict) -> float:
    last = snap.get("latestTrade", {}).get("p"); prevc = snap.get("prevDailyBar", {}).get("c")
    if last and prevc:
        diff = (prevc - last)/prevc; return max(0.0, min(1.0, 0.5 + diff*8))
    return 0.5
def score_news(news_hits: int) -> float:
    if news_hits <= 0: return 0.5
    if news_hits >= 5: return 0.8
    return 0.55 + 0.05*news_hits
def combine_scores(parts: Dict[str, float], weights: Dict[str, float]) -> float:
    s=0.0; w=0.0
    for k,v in parts.items():
        w_k = weights.get(k.split(":")[0], 0.0); s += v*w_k; w += w_k
    return max(0.0, min(1.0, s/(w if w else 1.0)))
