#!/bin/sh
set -e

# The trade journal is written to a bind-mounted host directory. Docker creates
# that directory as root the first time it is mounted, and the unprivileged
# 'trader' user the bot runs as cannot write to a root-owned directory. So we
# start as root, make the journal directory writable, then drop to 'trader'
# (via gosu) before launching the bot. This keeps the journal on the host for
# easy inspection while guaranteeing the container can always write to it.
LOG_DIR="${TRADE_LOG_DIR:-/app/trade_logs}"
mkdir -p "$LOG_DIR"
chown -R trader:trader "$LOG_DIR"

# Same story for the screened-pool directory (bind-mounted, created root-owned),
# so `screen-universe` can write data/pool.json and the bot can read it.
DATA_DIR="${DATA_DIR:-/app/data}"
mkdir -p "$DATA_DIR"
chown -R trader:trader "$DATA_DIR"

exec gosu trader "$@"
