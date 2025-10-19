from __future__ import annotations
import os, csv, pathlib
from typing import List

def load_universe(default: List[str]|None=None) -> List[str]:
    if default is None: default = ["AAPL","MSFT","NVDA"]
    env = (os.environ.get("DAYTRADER_UNIVERSE") or "").strip()
    if env:
        p = pathlib.Path(env)
        if p.exists():
            syms: List[str] = []
            try:
                with p.open() as f:
                    r = csv.DictReader(f)
                    if "Symbol" in r.fieldnames:
                        for row in r:
                            s = (row.get("Symbol") or "").strip().upper()
                            if s: syms.append(s)
                    else:
                        f.seek(0)
                        r2 = csv.reader(f)
                        for row in r2:
                            if not row: continue
                            s = str(row[0]).strip().upper()
                            if s and s!="SYMBOL": syms.append(s)
                return sorted(list(dict.fromkeys(syms)))
            except Exception:
                pass
        # comma separated
        syms = [s.strip().upper() for s in env.split(",") if s.strip()]
        if syms: return sorted(list(dict.fromkeys(syms)))
    return default
