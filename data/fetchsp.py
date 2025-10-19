# examples/fetch_sp500_symbols.py
import requests, pandas as pd, pathlib, sys
URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
hdrs = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Safari/537.36"}
html = requests.get(URL, headers=hdrs, timeout=15).text
tables = pd.read_html(html)  # requires: pip install pandas lxml
tbl = next(t for t in tables if 'Symbol' in t.columns)
syms = (tbl['Symbol'].astype(str).str.upper().str.strip()
        .str.replace(r'\.$','',regex=True).drop_duplicates().sort_values())
pathlib.Path("data").mkdir(parents=True, exist_ok=True)
out = pathlib.Path("sp500_symbols.csv")
syms.to_frame("Symbol").to_csv(out, index=False)
print(f"Saved {out} with {len(syms)} symbols")