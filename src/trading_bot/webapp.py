
from __future__ import annotations
import os, time, logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .state import get_kv, get_health, get_events
from .settings import get_settings, update_settings
log = logging.getLogger("web")
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    settings = get_settings(); positions = get_kv("positions", []); candidates = get_kv("candidates", []); health = get_health(); events = get_events(50)
    return templates.TemplateResponse("index.html", {"request": request, "settings": settings, "positions": positions, "candidates": candidates, "health": health, "events": events, "refresh_sec": 20})
@app.get("/api/health")
def api_health(): return {"health": get_health(), "ts": time.time()}
@app.get("/api/candidates")
def api_candidates(): return {"candidates": get_kv("candidates", []), "ts": time.time()}
@app.get("/api/positions")
def api_positions(): return {"positions": get_kv("positions", []), "ts": time.time()}
@app.get("/api/settings")
def api_settings(): return get_settings()
@app.post("/api/settings")
async def api_update_settings(request: Request):
    body = await request.json(); new_settings = update_settings(body or {}); return new_settings
