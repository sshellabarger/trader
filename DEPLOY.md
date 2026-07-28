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
# STOCK_SLEEVE_UNIVERSE=liquid_movers                    # what to scan
# STOCK_SLEEVE_SYMBOLS=NVDA,PLTR,SMCI,ARM,CRWD           # explicit list (overrides universe)
# STOCK_SLEEVE_MAX_CANDIDATES=5                          # names traded per day
# STOCK_SLEEVE_MAX_POSITIONS=3                           # held at once
# STOCK_SLEEVE_MAX_POSITION_PCT=25                       # per-name cap, % of equity
```

**Data feed matters for the sleeve.** On the free **IEX** feed Alpaca only prints
intraday bars for liquid names, so the sleeve's default universe is
`liquid_movers` (curated for IEX coverage). Low-priced / low-float names (the old
`volatile_movers` pool — BITF, SOUN, etc.) return *no bars* on IEX and never
trade. To trade the wider, more volatile pool, subscribe to Alpaca **Algo Trader
Plus** (SIP real-time, ~$99/month), set `APCA_DATA_FEED=sip` in `.env`, then
switch `STOCK_SLEEVE_UNIVERSE` to `tech_volatile,volatile_movers`.

The daily journal (`./trade_logs/summary_<date>.json`) now records a `context`
block every day — the day's picks, per-symbol bar status (which names got no
data), and skip reasons — so a no-trade day is legible without reading the
container logs.

then `docker compose up -d --force-recreate`. Confirm the log line
`STOCK SLEEVE ON — stocks-only, scanner-driven` and watch the morning picks in
`docker compose logs -f`. To go back to the index profile, set
`STOCK_SLEEVE_ENABLED=false` (or remove the line) and recreate. See
`.env.example` for the full list of toggles and risk caps.

### Keeping the candidate pool fresh (weekly screen)

By default the sleeve scans a static high-growth list. To keep that pool current
you can rebuild it from a liquidity + volatility screen (average dollar volume
and ATR%) over all active US equities, and point the sleeve at the result:

```bash
docker compose run --rm trader python -m trader screen-universe --out data/pool.json
```

That writes `data/pool.json` (persisted via the `./data` volume). Then add
`STOCK_SLEEVE_POOL_FILE=data/pool.json` to `.env` and recreate; the sleeve will
scan the screened pool instead of the static list (an explicit
`STOCK_SLEEVE_SYMBOLS` still wins if set). Tune with `--min-dollar-vol`,
`--min-atr-pct`, `--min-price/--max-price`, and `--max` (pool size). Run it on a
schedule (e.g. a weekly cron entry on the droplet) to refresh automatically:

```
# /etc/cron.d/trader-pool  — rebuild the sleeve pool every Sunday 18:00
0 18 * * 0 root cd /root/trader && docker compose run --rm trader python -m trader screen-universe --out data/pool.json >> /var/log/trader-pool.log 2>&1
```

### News and catalysts (optional, default off)

The sleeve can also lean on the day's news. With `STOCK_SLEEVE_NEWS_ENABLED=true`
it pulls recent market-wide headlines from Alpaca's free news feed each morning,
adds the names with the most fresh coverage to the scan pool (so today's
catalyst movers get considered even if they aren't in the static pool), and
skips breakout longs into strongly-negative headlines. It runs inside the bot
(no separate job) and needs no extra keys. Tune with `STOCK_SLEEVE_NEWS_HOTLIST`
(how many catalyst names to add), `STOCK_SLEEVE_NEWS_LOOKBACK_MIN` (how far back
to scan), and `STOCK_SLEEVE_NEWS_BLOCK_BELOW` (the sentiment floor for the entry
gate). Watch the `News catalysts: N articles -> hot-list [...]` log line to see
the day's picks.

## Updating later

```bash
git pull
docker compose up -d --build
```

## Notes

- Secrets live only in `.env`, which is gitignored and excluded from the image.
- The default market-data feed is `iex` (free, partial). Consider the `sip`
  feed (paid Alpaca plan) before live trading.

## Kalshi phase-0 recorder

A second compose service, `kalshi-recorder`, records prediction-market data
24/7 (see `trader/kalshi/`). It is measurement only: public endpoints, no
credentials, no orders. It shares the trader image, so deploying is the same
`git pull && docker compose build && docker compose up -d` — compose starts
both services.

Data lands in `data/kalshi/` on the host:

- `snapshots-YYYYMMDD.jsonl` — every tracked market's top-of-book each poll
  (`"type":"md"`), plus order-book depth near close (`"type":"book"`). Rough
  budget at defaults: ~50 MB/day plain text; watch it the first week.
- `settlements.jsonl` — settled outcomes, deduped, swept daily.

Sanity checks after deploy:

    docker compose logs -f kalshi-recorder      # "Kalshi recorder up: ..."
    tail -1 data/kalshi/snapshots-$(date -u +%Y%m%d).jsonl
    docker compose run --rm trader python -m trader kalshi-discover

Tune series/cadence via the `KALSHI_*` block in `.env.example`. A series that
lists nothing (off-season NFL, renamed ticker) logs a WARNING once per day —
that is expected until the season's markets list, not a failure.
