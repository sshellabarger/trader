# Day Trader (Alpaca + Web UI)

**A practical, extensible day-trading bot** with:

- Alpaca account/positions, batch snapshots (stocks & crypto)
- Strategy toggles (momentum, mean-reversion, news, earnings, long-term)
- News & earnings schedulers with rate-limit handling
- Health checks (clock, snapshots, positions, account) surfaced in the UI
- Web UI (positions, candidates, health, events) with 20s auto-refresh + manual refresh
- JSON logging to console and `data/app.log`
- SQLite state store (settings/health/events/trades), resilient to restarts
- Basic tests and GitHub Actions CI

> ⚠️ **Disclaimer**: This project is for educational purposes. It is **not** financial advice. Markets are risky; there is no guarantee of profit (including “5% daily”). Use **paper trading** before any real capital and set strict risk limits.

---

## 1) Requirements

Tested with Python ≥ 3.10 (works with 3.13). Install system tools (macOS examples shown):

```bash
# macOS
xcode-select --install   # compilers, if you don't have them
brew install python git  # optional convenience
```

### Python dependencies

Use these pinned minimums (you can tighten to exact versions if you want reproducibility):

```
fastapi>=0.110
uvicorn[standard]>=0.23
requests>=2.31
python-dateutil>=2.8.2
pydantic>=2.7
Jinja2>=3.1.2
pandas>=2.2.2
numpy>=2.1.2
feedparser>=6.0.10
aiofiles>=23.2.1
pytest>=8.2.0
```

(They’re already listed in `requirements.txt`.)

---

## 2) Project Layout

```
.
├─ src/trading_bot/
│  ├─ cli.py               # runs engine + web UI
│  ├─ engine.py            # trading loop, schedulers, health, candidates, demo trades
│  ├─ broker_alpaca.py     # Alpaca account/positions, snapshots (stocks/crypto), orders
│  ├─ settings.py          # KV-backed config (strategy toggles, thresholds, scheduling)
│  ├─ state.py             # sqlite persistence (kv, events, health, trades)
│  ├─ news.py              # news counts (Alpaca→Finnhub→NewsAPI fallback & cooldown)
│  ├─ earnings.py          # Finnhub earnings calendar loader
│  ├─ strategies.py        # momentum, mean-reversion, news score, combiner
│  ├─ universe.py          # loads S&P 500 universe from CSV or env list
│  └─ logger.py            # JSON logging to stdout + data/app.log
├─ templates/index.html    # web UI (positions, candidates, health, events)
├─ static/app.js           # 20s countdown auto-refresh
├─ data/sp500_symbols.csv  # seed universe (replace with full S&P 500)
├─ tests/…                 # basic tests
├─ .github/workflows/ci.yml
├─ .env.example            # sample environment variables
├─ requirements.txt
├─ pyproject.toml
└─ README.md               # this file
```

---

## 3) Environment Variables

Copy `.env.example` to `.env` (or export in your shell). Required to run:

```bash
export ALPACA_API_KEY_ID=your_key_id
export ALPACA_API_SECRET_KEY=your_secret
export ALPACA_PAPER=true                 # true (paper) or false (live)
export ALPACA_DATA_FEED=iex              # 'iex' or 'sip' (requires plan)
export ALPACA_TIMEOUT=6.0
export DAYTRADER_UNIVERSE=$PWD/data/sp500_symbols.csv  # or "AAPL,MSFT,NVDA"
export TRADING_BOT_DB=./data/trading_bot.db
export WEB_HOST=0.0.0.0
export WEB_PORT=8000
export LOG_LEVEL=INFO
# Optional providers for news/earnings:
export NEWSAPI_KEY=...
export FINNHUB_API_KEY=...
# Optional crypto notional:
export CRYPTO_NOTIONAL_USD=25
```

> **Keys:** Alpaca uses `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` (exact names).  
> **Universe:** Can be a CSV or a comma-separated list.

---

## 4) Install & Run

### A) Virtualenv (recommended)

```bash
python -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
```

### B) Start the app

```bash
# ensure env is set before running (see Section 3)
python -m trading_bot.cli
```

Open the UI at: **http://localhost:8000**

> The CLI starts both the **engine** (trading loop + schedulers) and the **web server** (FastAPI) in one process.

---

## 5) Web UI

**Auto-refresh:** every 20s (with a visible countdown) + a manual refresh button.

**Sections:**
- **Positions** (top): Live positions snapshot (symbol, qty, avg price, market value, unrealized P/L).
- **Candidates:** Ranked by combined score (momentum + mean-reversion + news).
- **Health:** Clock, batch snapshots “smoke tests”, account checks, earnings/news schedulers status, last run time & details.
- **Events:** Recent log/event messages recorded by the engine (e.g., “BUY AAPL…”, warnings, market closed).

**API endpoints (JSON):**
- `GET /api/positions` — positions snapshot (sourced periodically)
- `GET /api/candidates` — current candidate list
- `GET /api/health` — health items
- `GET /api/settings` — merged settings (defaults + KV overrides)
- `POST /api/settings` — update settings (see examples in Section 8)

---

## 6) Strategy Overview

The engine ranks potential trades via a weighted score:

- **Momentum**: price vs daily open; stronger trend → higher score
- **Mean-reversion**: price below previous close → potential revert
- **News**: recent article count → higher interest → small bias
- **Earnings**: calendar can be used to auto-include reporting names (toggle)
- **Long-term**: placeholders available to extend (toggle)

**Weights & thresholds** are in `settings.py` and can be overwritten at runtime:
- `thresholds.enter` (default `0.62`) — minimum score to consider a buy
- `thresholds.exit` (default `0.45`) — (hook point if you implement exits)
- `thresholds.min_spread_bps` — ignore wide spreads
- `thresholds.trade_stop_loss_bps` & `thresholds.daily_stop_loss_pct` — risk controls (hook points; add your own enforcement in `engine.py`)

> The shipped trading action is **intentionally conservative** (demo): it may send a small **buy** if top candidate score ≥ `enter`. You should implement your complete position sizing, exit logic, and risk management before live usage.

---

## 7) Schedulers & Health Checks

### Schedulers

- **Candidates**: refresh top movers every `candidate_refresh_min` (default 20 min) using **batch snapshots**.
- **News**: every `news_interval_s` (default 1200s). Uses provider order:
  1. Alpaca News (if available)
  2. Finnhub Company News (`FINNHUB_API_KEY`)
  3. NewsAPI Everything (`NEWSAPI_KEY`) with **cooldown** on 429 rate limits
- **Earnings**: every `earnings_refresh_min` (default 60 min), next 7 days via Finnhub.
- **Health**: every `health_refresh_min` (default 20 min) — runs smoke tests and records last run.

### Health Items (UI → **Health** table)

- `clock`: `is_open` true/false (from Alpaca clock)
- `stock_snapshots_smoke`: count of snapshots returned for a small batch
- `crypto_snapshots_smoke`: if crypto enabled, count for a small batch
- `marketdata_stock` / `marketdata_crypto`: self-test snapshot counts at startup
- `account`: presence of `account_number`
- `earnings_calendar`: OK/empty
- `news_scheduler`: `total_hits` summed across the polled symbols

---

## 8) Settings & Tuning (Runtime)

Settings are read from defaults (`settings.py`) and merged with persisted KV (`state.py`).  
You can **update at runtime** via `POST /api/settings`.

#### Common knobs

```json
{
  "scheduling": {
    "candidate_refresh_min": 20,
    "candidate_max_symbols": 150,
    "news_interval_s": 1200,
    "health_refresh_min": 20
  },
  "strategies": {
    "momentum": true,
    "mean_reversion": true,
    "news": true,
    "earnings": true,
    "longterm_trend": false,
    "longterm_momentum": false,
    "crypto": false
  },
  "thresholds": {
    "enter": 0.62,
    "exit": 0.45,
    "min_spread_bps": 25.0,
    "trade_stop_loss_bps": 50.0,
    "daily_stop_loss_pct": 2.0
  },
  "news": {
    "provider_order": ["alpaca", "finnhub", "newsapi"],
    "window_hours": 6,
    "rotate_batch": 60,
    "newsapi_cooldown_min": 120
  },
  "crypto": {
    "enabled": false,
    "universe": ["BTC/USD", "ETH/USD"]
  },
  "data": {
    "strict_batch_only": true
  }
}
```

#### Examples (with `curl`)

- **Lower** `candidate_max_symbols`, **increase** candidate refresh, and **use batch snapshots only**:

```bash
curl -X POST http://localhost:8000/api/settings   -H 'Content-Type: application/json'   -d '{
    "scheduling":{"candidate_max_symbols":80,"candidate_refresh_min":30},
    "data":{"strict_batch_only":true}
  }'
```

- **Enable crypto** and add LTC:

```bash
curl -X POST http://localhost:8000/api/settings   -H 'Content-Type: application/json'   -d '{
    "strategies":{"crypto":true},
    "crypto":{"enabled":true,"universe":["BTC/USD","ETH/USD","LTC/USD"]}
  }'
```

- **Adjust entry threshold** to be more selective:

```bash
curl -X POST http://localhost:8000/api/settings   -H 'Content-Type: application/json'   -d '{"thresholds":{"enter":0.70}}'
```

---

## 9) Universe (S&P 500)

By default the app reads `data/sp500_symbols.csv` (column header can be `Symbol`, `Ticker`, `Symbols`, etc.).  
You can set a custom path via `DAYTRADER_UNIVERSE=/path/to/your.csv`, or pass a comma-separated list:

```bash
export DAYTRADER_UNIVERSE="AAPL,MSFT,NVDA,AMZN,META,GOOGL,TSLA"
```

If your CSV has multiple tickers per row, ensure a single column of tickers for best results:

```csv
Symbol
AAPL
MSFT
NVDA
...
```

---

## 10) Logging

- **Format:** JSON lines to stdout and `data/app.log`
- **Levels:**
  - `DEBUG`: full HTTP success calls to Alpaca, internal details
  - `INFO`: high-level lifecycle & trading events (market closed notice, BUY entries, schedulers starting)
  - `WARNING`: transient HTTP failures, non-fatal issues (e.g., rate limits)
  - `ERROR`: order failures, unhandled exceptions caught in try/except
- Change log level via `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR`

---

## 11) Tests & CI

Run tests:

```bash
pytest -q
```

CI (`.github/workflows/ci.yml`) installs deps and runs pytest on pushes/PRs.

If you change package layout, ensure `pyproject.toml` has `package-dir = {"" = "src"}` and tests add `pythonpath = ["src"]`.

---

## 12) Operating Notes

### Market hours vs data availability
- The bot uses Alpaca’s **batch snapshots** endpoint for market data.  
  If you see “batch snapshots empty” or frequent 429s:
  - You may be **outside market hours** and your plan/feed doesn’t support extended data.
  - Try reducing `candidate_max_symbols` and increasing refresh intervals.
  - Make sure `ALPACA_DATA_FEED` matches your plan (`iex` for most paper accounts).

### News rate limits
- NewsAPI has strict rate limits → the bot sets a **cooldown** on HTTP 429.  
  Use Alpaca News or Finnhub if available and place them earlier in `news.provider_order`.

### Positions don’t show up
- Positions are fetched in the **health** scheduler and written to KV for the UI.  
  If empty:
  - Confirm keys are correct (paper vs live).
  - Try `LOG_LEVEL=DEBUG` and check `data/app.log` for Alpaca `/v2/positions` call results.

### No trades happening
- The shipped logic is conservative and executes **buy** only on a high combined score.  
  To stimulate activity (paper accounts), lower `thresholds.enter` (e.g., `0.58`), enable more strategies, or broaden the universe.  
  Ensure market is **open** (UI Health → `clock`).

---

## 13) Customizing Strategies

Extend in `strategies.py` and wire into `engine._refresh_candidates`:

- Add new `score_*` functions (e.g., `score_vwap`, `score_liquidity`)
- Update `weights` in settings (and expose toggles via `/api/settings`)
- Enforce risk with **position sizing**, **max concurrent**, **hard stops**, and **daily stop** inside the main loop

---

## 14) Security

- Treat API keys as secrets; do **not** commit `.env`.
- Use paper trading (`ALPACA_PAPER=true`) until thoroughly tested.
- Add IP allowlists / 2FA in your Alpaca account as applicable.

---

## 15) Start/Stop Scripts (Optional)

### iTerm2/macOS: start everything

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .

# load env
export $(grep -v '^#' .env | xargs)

python -m trading_bot.cli
```

### Systemd (Linux) – very rough sketch

```
[Unit]
Description=Day Trader Bot
After=network.target

[Service]
WorkingDirectory=/opt/daytrader
EnvironmentFile=/opt/daytrader/.env
ExecStart=/opt/daytrader/venv/bin/python -m trading_bot.cli
Restart=always
User=trader

[Install]
WantedBy=multi-user.target
```

---

## 16) Roadmap Ideas

- Real exit logic (profit-taking, trailing stops)
- Portfolio risk: position sizing, exposure caps, sector diversification
- Better alpha: intraday features (VWAP, range breakouts), regime detection
- Model-based probability forecaster w/ backtests and walk-forward
- Async pipelines for higher throughput
- Docker image + compose stack

---

## 17) Troubleshooting Quick Hits

- **ModuleNotFoundError**: make sure you ran `pip install -e .` and use `python -m trading_bot.cli` from repo root.
- **HTTP 429**: reduce symbol counts, lengthen refresh intervals, prefer snapshots over per-symbol quotes.
- **“batch snapshots empty”**: check market hours, data feed (`iex` vs `sip`), or try fewer symbols.
- **NewsAPI rateLimited**: respected by cooldown—either add providers or wait it out.
- **Clock says closed**: the engine will idle; verify exchange hours and your timezone.

---

## 18) License & Credits

- You own your strategies and risk. This template is MIT-style “as-is”.  
- Thanks to Alpaca, Finnhub, and NewsAPI for their APIs (subject to their terms/limits).
