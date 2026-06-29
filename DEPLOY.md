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
