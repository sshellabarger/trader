# Trader — always-on Alpaca day-trading bot.
# Paper by default; going live is ONLY an env change (live keys + live base URL).
# This repo's files ARE the `trader` package, so the image copies them into
# /app/trader and runs `python -m trader` from /app.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# The package (this repo) -> /app/trader. .dockerignore keeps secrets, venv,
# logs and caches out of the image.
COPY . /app/trader/

RUN useradd --create-home --uid 10001 trader && chown -R trader:trader /app
USER trader

# The engine loops on --interval seconds and idles while the market is closed,
# so this is a true 24/7 process. Credentials come from the environment.
CMD ["python", "-m", "trader", "run", "--interval", "30"]
