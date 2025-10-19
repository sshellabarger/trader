from fastapi.testclient import TestClient
from trading_bot.webapp import app

def test_routes_health_and_settings():
    c = TestClient(app)
    r = c.get("/api/settings"); assert r.status_code == 200
    r = c.get("/api/health"); assert r.status_code == 200
    r = c.post("/api/health/run"); assert r.status_code == 200
