from __future__ import annotations

import os, sqlite3, json, time, threading, pathlib
from typing import Any, Dict, List, Optional

_DB_PATH = os.environ.get("TRADING_BOT_DB", "./data/trading_bot.db")
pathlib.Path(os.path.dirname(_DB_PATH) or ".").mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn

_CONN = _connect()

def init_db() -> None:
    with _lock, _CONN:
        _CONN.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_ts REAL
        )
        """)
        _CONN.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            level TEXT,
            msg TEXT,
            meta TEXT
        )
        """)
        _CONN.execute("""
        CREATE TABLE IF NOT EXISTS health (
            name TEXT PRIMARY KEY,
            ok INTEGER,
            detail TEXT,
            ts REAL
        )
        """)
        _CONN.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            qty REAL,
            avg_entry REAL,
            updated_ts REAL
        )
        """)

init_db()

def set_kv(key: str, value: Any) -> None:
    ts = time.time()
    with _lock, _CONN:
        _CONN.execute(
            "INSERT INTO kv(key,value,updated_ts) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts=excluded.updated_ts",
            (key, json.dumps(value), ts)
        )

def get_kv(key: str, default: Any=None) -> Any:
    with _lock, _CONN:
        cur = _CONN.execute("SELECT value FROM kv WHERE key=?", (key,))
        row = cur.fetchone()
    if not row: return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default

def add_event(level: str, msg: str, meta: Optional[Dict[str, Any]]=None) -> None:
    ts = time.time()
    with _lock, _CONN:
        _CONN.execute(
            "INSERT INTO events(ts,level,msg,meta) VALUES(?,?,?,?)",
            (ts, level.upper(), msg, json.dumps(meta or {}))
        )

def list_events(n: int=200) -> List[Dict[str, Any]]:
    with _lock, _CONN:
        cur = _CONN.execute("SELECT ts,level,msg,meta FROM events ORDER BY ts DESC LIMIT ?", (n,))
        rows = cur.fetchall()
    out = []
    for r in rows:
        d = {"ts": r["ts"], "level": r["level"], "msg": r["msg"]}
        try:
            d["meta"] = json.loads(r["meta"] or "{}")
        except Exception:
            d["meta"] = {}
        out.append(d)
    return out[::-1]  # chronological

def set_health(name: str, ok: bool, detail: str="", ts: Optional[float]=None) -> None:
    if ts is None: ts = time.time()
    with _lock, _CONN:
        _CONN.execute(
            "INSERT INTO health(name,ok,detail,ts) VALUES(?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET ok=excluded.ok, detail=excluded.detail, ts=excluded.ts",
            (name, 1 if ok else 0, detail, ts)
        )

def get_health() -> List[Dict[str, Any]]:
    with _lock, _CONN:
        cur = _CONN.execute("SELECT name, ok, detail, ts FROM health ORDER BY name ASC")
        rows = cur.fetchall()
    return [{"name": r["name"], "ok": bool(r["ok"]), "detail": r["detail"], "ts": r["ts"]} for r in rows]

def upsert_position(symbol: str, qty: float, avg_entry: float) -> None:
    with _lock, _CONN:
        _CONN.execute(
            "INSERT INTO positions(symbol, qty, avg_entry, updated_ts) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg_entry=excluded.avg_entry, updated_ts=excluded.updated_ts",
            (symbol.upper(), float(qty), float(avg_entry), time.time())
        )

def get_positions() -> List[Dict[str, Any]]:
    with _lock, _CONN:
        cur = _CONN.execute("SELECT symbol, qty, avg_entry, updated_ts FROM positions ORDER BY symbol ASC")
        rows = cur.fetchall()
    return [{"symbol": r["symbol"], "qty": r["qty"], "avg_entry": r["avg_entry"], "updated": r["updated_ts"]} for r in rows]
