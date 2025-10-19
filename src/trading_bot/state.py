
import json, sqlite3, pathlib, os, time
from typing import Any, Optional, Dict, List
DB_PATH = pathlib.Path(os.environ.get("TRADING_BOT_DB", "./data/trading_bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
def _conn():
    con = sqlite3.connect(str(DB_PATH)); con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS health (name TEXT PRIMARY KEY, ok INTEGER, detail TEXT, ts REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS events (ts REAL, level TEXT, msg TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS trades (ts REAL, symbol TEXT, side TEXT, qty REAL, price REAL, note TEXT)")
    return con
def get_kv(key: str, default: Any=None) -> Any:
    con = _conn(); cur = con.execute("SELECT v FROM kv WHERE k=?", (key,)); row = cur.fetchone(); con.close()
    if not row: return default
    try: return json.loads(row[0])
    except: return row[0]
def set_kv(key: str, value: Any) -> None:
    con = _conn(); con.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (key, json.dumps(value))); con.commit(); con.close()
def set_health(name: str, ok: bool, detail: str, ts: Optional[float]=None) -> None:
    ts = ts or time.time(); con = _conn()
    con.execute("INSERT OR REPLACE INTO health(name, ok, detail, ts) VALUES(?,?,?,?)", (name, 1 if ok else 0, detail, ts)); con.commit(); con.close()
def get_health() -> List[Dict[str, Any]]:
    con = _conn(); cur = con.execute("SELECT name, ok, detail, ts FROM health ORDER BY name")
    out = [dict(r) for r in cur.fetchall()]; con.close(); return out
def add_event(level: str, msg: str) -> None:
    con = _conn(); con.execute("INSERT INTO events(ts, level, msg) VALUES(?,?,?)", (time.time(), level, msg)); con.commit(); con.close()
def get_events(limit: int=100) -> List[Dict[str, Any]]:
    con = _conn(); cur = con.execute("SELECT ts, level, msg FROM events ORDER BY ts DESC LIMIT ?", (int(limit),))
    out = [dict(r) for r in cur.fetchall()]; con.close(); return out
def record_trade(symbol: str, side: str, qty: float, price: float, note: str="") -> None:
    con = _conn(); con.execute("INSERT INTO trades(ts, symbol, side, qty, price, note) VALUES(?,?,?,?,?,?)", (time.time(), symbol, side, float(qty), float(price), note)); con.commit(); con.close()
