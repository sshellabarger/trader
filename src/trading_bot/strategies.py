from __future__ import annotations
from typing import Dict, Tuple

def _mover_from_snap(snap: dict) -> float:
    mb = (snap.get("minuteBar") or {}).get("c")
    db = (snap.get("dailyBar") or {}).get("c")
    try:
        if mb and db and float(db) > 0:
            return float(mb)/float(db) - 1.0
    except Exception:
        pass
    return 0.0

def score_stock_candidates(snaps: Dict[str, dict], news_counts: Dict[str,int], earnings: Dict[str,str]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for sym, snap in (snaps or {}).items():
        mover = _mover_from_snap(snap)
        news_boost = 0.05 * min(5, int(news_counts.get(sym, 0)))
        earn_boost = 0.2 if sym in earnings else 0.0
        score = max(0.0, min(1.0, 0.5 + 4.0*mover + news_boost + earn_boost))
        out[sym] = {"mover": mover, "score": score}
    return out
