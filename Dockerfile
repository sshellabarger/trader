# Trader — always-on Alpaca day-trading bot.
# Paper by default; going live is ONLY an env change (live keys + live base URL).
# This repo's files ARE the `trader` package, so the image copies them into
# /app/trader and runs `python -m trader` from /app.
# Pinned to the bookworm variant so a Debian base-image jump can't arrive
# silently with a routine rebuild. For stricter supply-chain pinning, replace
# with an exact digest: docker pull python:3.11-slim-bookworm && docker images
# --digests, then FROM python:3.11-slim-bookworm@sha256:<digest>.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# gosu lets the entrypoint start as root (to fix bind-mount ownership) and then
# drop to the unprivileged 'trader' user before running the bot.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The package (this repo) -> /app/trader. .dockerignore keeps secrets, venv,
# logs and caches out of the image.
COPY . /app/trader/

# Create the unprivileged user and pre-create the journal directory it writes
# to. The entrypoint re-applies ownership at runtime so a root-owned bind mount
# (Docker's default for a freshly created host directory) is always made
# writable before privileges are dropped.
RUN useradd --create-home --uid 10001 trader \
    && mkdir -p /app/trade_logs \
    && chown -R trader:trader /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# We intentionally do NOT set `USER trader`: the entrypoint must start as root
# to fix bind-mount ownership, then it execs the bot as 'trader' via gosu.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# The engine loops on --interval seconds and idles while the market is closed,
# so this is a true 24/7 process. Credentials come from the environment.
CMD ["python", "-m", "trader", "run", "--interval", "30"]
