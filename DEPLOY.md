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

## Updating later

```bash
git pull
docker compose up -d --build
```

## Notes

- Secrets live only in `.env`, which is gitignored and excluded from the image.
- The default market-data feed is `iex` (free, partial). Consider the `sip`
  feed (paid Alpaca plan) before live trading.
