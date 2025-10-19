
import os, pathlib, pandas as pd
def load_universe() -> list[str]:
    env = os.environ.get("DAYTRADER_UNIVERSE", "data/sp500_symbols.csv")
    if "," in env:
        return [x.strip().upper() for x in env.split(",") if x.strip()]
    path = pathlib.Path(env)
    if path.exists():
        try:
            df = pd.read_csv(path)
            col = None
            for c in df.columns:
                if c.strip().lower() in ("symbol","symbols","ticker","tickers"):
                    col = c; break
            if col:
                return sorted({str(x).upper().strip() for x in df[col].dropna().tolist()})
        except Exception:
            pass
    return ["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA"]
