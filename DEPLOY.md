# Deploying the trader 24/7

The bot runs continuously and trades only while the market is open. It starts on
Alpaca **paper** by default; going live is only a credentials change.

## What you need

- A small always-on Linux host. A $4–6/month DigitalOcean Droplet (1 vCPU,
  1 GB RAM) is plenty. Use the **Docker** Marketplace image so Docker and
  Compose are preinstalled.
- Your Alpaca **paper** API key id + secret.

## First deploy (paper)

On the server (the DigitalOcean Droplet **Console** in the browser works fine):

```bash
git clone https://github.com/sshellabarger/trader.git
cd trader
cp .env.example .env
nano .env        # paste your PAPER key id + secret, then Ctrl-O, Enter, Ctrl-X
docker compose up -d --build
```

`restart: always` keeps it running across crashes and reboots, so it is
effectively 24/7. The engine wakes on its interval, trades when the market is
open, and idles when it is closed.

- **Watch it:** `docker compose logs -f`
- **See trades/positions:** the Alpaca **paper** dashboard at app.alpaca.markets.
- **Trade journal** persists on the host in `./trade_logs`.

## Going live (real money)

Edit `.env` and change **only** the three credential lines to your LIVE keys and
`https://api.alpaca.markets`, then:

```bash
docker compose up -d --force-recreate
```

No code change. Validate on paper first, confirm the limits in `RiskConfig`
(daily_loss_limit_pct, max_position_pct, max_risk_dollars), and watch the first
live sessions closely. Automated real-money trading can lose money fast.

## Stock sleeve (paper experiment)

By default the bot trades the index legs (TQQQ/SQQQ). It can instead trade a
basket of individual **high-growth stocks** picked each morning by the scanner
(premarket gappers/movers), using the same long-only ORB breakout, bracket
stops, and end-of-day flatten. It is **off** until you switch it on in `.env`,
so deploying the code changes nothing on its own.

Enable it on the **paper** account by adding to `.env`:

```bash
STOCK_SLEEVE_ENABLED=true
# optional — defaults shown:
# STOCK_SLEEVE_UNIVERSE=tech_volatile,volatile_movers   # what to scan
# STOCK_SLEEVE_SYMBOLS=NVDA,PLTR,SMCI,ARM,CRWD           # explicit list (overrides universe)
# STOCK_SLEEVE_MAX_CANDIDATES=5                          # names traded per day
# STOCK_SLEEVE_MAX_POSITIONS=3                           # held at once
# STOCK_SLEEVE_MAX_POSITION_PCT=25                       # per-name cap, % of equity
```

then `docker compose up -d --force-recreate`. Confirm the log line
`STOCK SLEEVE ON — stocks-only, scanner-driven` and watch the morning picks in
`docker compose logs -f`. To go back to the index profile, set
`STOCK_SLEEVE_ENABLED=false` (or remove the line) and recreate. See
`.env.example` for the full list of toggles and risk caps.

## Updating later

```bash
git pull
docker compose up -d --build
```

## Notes

- Secrets live only in `.env`, which is gitignored and excluded from the image.
- The default market-data feed is `iex` (free, partial). Consider the `sip`
  feed (paid Alpaca plan) before live trading.
