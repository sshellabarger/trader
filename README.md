# Day Trader — full app source

See `.env.example` for required environment variables. Start with:

```bash
python -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
export ALPACA_API_KEY_ID=...
export ALPACA_API_SECRET_KEY=...
python -m trading_bot.cli
```
Open http://localhost:8000
