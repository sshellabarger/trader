"""
Trading Universe — curated stock lists optimized for intraday trading.

Selection criteria:
  - High average daily volume (>1M shares) for reliable fills
  - Tight bid-ask spreads for minimal slippage
  - Sufficient intraday volatility for profit opportunities
  - No OTC / penny stocks / low-float traps

Categories help the scanner prioritize — gap stocks from 'volatile_movers'
behave differently than gap stocks from 'mega_cap_liquid'.

Usage:
  from daytrader.universe import get_universe, load_universe_csv

  symbols = get_universe()                    # full default list
  symbols = get_universe(["mega_cap", "tech"]) # specific categories
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Categorized universe
# ---------------------------------------------------------------------------

UNIVERSE: Dict[str, List[str]] = {

    # ── Mega-cap liquid (always tight spreads, huge volume) ──────────
    "mega_cap": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
        "BRK.B", "AVGO", "JPM", "LLY", "V", "UNH", "MA", "XOM", "JNJ",
        "COST", "HD", "PG", "ABBV", "WMT", "NFLX", "CRM", "BAC", "ORCL",
        "CVX", "MRK", "KO", "ADBE", "AMD", "PEP", "TMO", "CSCO", "ACN",
        "LIN", "MCD", "ABT", "DHR", "WFC", "INTC", "DIS", "QCOM", "INTU",
        "AMGN", "CAT", "GS", "AXP", "IBM", "NOW",
    ],

    # ── High-beta tech (bigger intraday swings) ─────────────────────
    "tech_volatile": [
        "SMCI", "MRVL", "ARM", "CRWD", "PANW", "MU", "ANET", "SNPS",
        "CDNS", "KLAC", "LRCX", "ASML", "MSTR", "PLTR", "DELL", "FTNT",
        "TTD", "DDOG", "ZS", "NET", "SNOW", "SHOP", "MELI", "SE",
        "TEAM", "WDAY", "HUBS", "OKTA", "MDB", "CFLT",
    ],

    # ── Consumer / retail (earnings movers, gap candidates) ─────────
    "consumer": [
        "NKE", "SBUX", "TGT", "LOW", "TJX", "ROST", "CMG", "YUM",
        "LULU", "DECK", "BURL", "DG", "DLTR", "ULTA", "EL", "CPNG",
        "W", "ETSY", "CHWY", "ABNB", "BKNG", "EXPE", "MAR", "HLT",
    ],

    # ── Biotech / pharma (catalyst-driven gaps, high vol) ───────────
    "biotech": [
        "MRNA", "BIIB", "REGN", "VRTX", "GILD", "ILMN", "DXCM",
        "ISRG", "ALGN", "HOLX", "JAZZ", "BMRN", "SRPT", "ALNY",
        "PCVX", "IONS", "EXAS", "RARE", "HALO", "LEGN",
    ],

    # ── Financials (rate-sensitive, earnings movers) ────────────────
    "financials": [
        "C", "MS", "SCHW", "BLK", "SPGI", "ICE", "CME", "CB",
        "MMC", "AON", "AJG", "COIN", "HOOD", "SQ", "PYPL", "FIS",
        "AFRM", "SOFI", "NU", "MARA",
    ],

    # ── Energy (oil-price correlated, macro movers) ─────────────────
    "energy": [
        "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "HAL",
        "DVN", "FANG", "HES", "KMI", "WMB", "ET", "CTRA",
    ],

    # ── Volatile movers (small/mid cap, big intraday ranges) ────────
    # NOTE: many names here are low-priced / low-float and have SPARSE coverage
    # on the free IEX feed — the live sleeve often gets no intraday bars for them
    # (e.g. BITF), so they can never trigger. Prefer "liquid_movers" below unless
    # running on the paid SIP feed (APCA_DATA_FEED=sip).
    "volatile_movers": [
        "RIVN", "LCID", "NIO", "XPEV", "LI", "LAZR",
        "RIOT", "CLSK", "BITF", "CIFR",
        "SNAP", "PINS", "ROKU", "LYFT", "UBER", "DASH", "RBLX",
        "AI", "BBAI", "SOUN", "IONQ", "RGTI", "QUBT",
        "UPST", "OPEN", "CVNA", "CAVA", "DUOL", "RDDT",
    ],

    # ── Liquid movers (high-beta but heavily traded — reliable IEX bars) ──
    # Curated for the stock sleeve on the FREE IEX feed: every name is a big
    # intraday mover AND trades enough volume that IEX prints a 1-minute bar
    # essentially every minute, so the sleeve actually gets data and can enter.
    # Deliberately excludes sub-$15 / low-float names that IEX barely covers.
    # This is the recommended sleeve universe until the paid SIP feed is enabled.
    "liquid_movers": [
        "NVDA", "AMD", "TSLA", "META", "AVGO", "MU", "MRVL", "SMCI",
        "ARM", "PLTR", "MSTR", "CRWD", "PANW", "SNOW", "NET", "DDOG",
        "SHOP", "COIN", "HOOD", "UBER", "QCOM", "LRCX", "KLAC", "ANET",
        "MARA", "AAPL", "AMZN", "NFLX",
    ],

    # ── ETFs (sector plays, broad market, always liquid) ────────────
    "etfs": [
        "SPY", "QQQ", "IWM", "DIA",              # broad market
        "XLF", "XLE", "XLK", "XLV", "XLI",       # sector
        "ARKK", "ARKG",                            # innovation
        "GLD", "SLV", "USO",                       # commodities
        "TLT", "HYG",                              # bonds
        "SOXL", "TQQQ", "SQQQ", "SPXL", "SPXS",  # leveraged
        "VXX", "UVXY",                              # volatility
        "XBI", "SMH", "KWEB", "EEM",               # thematic
    ],
}


# ---------------------------------------------------------------------------
# Universe access functions
# ---------------------------------------------------------------------------

def get_universe(
    categories: Optional[List[str]] = None,
    exclude_etfs: bool = False,
    exclude_leveraged: bool = False,
) -> List[str]:
    """
    Get the trading universe as a deduplicated, sorted list.

    Args:
        categories: list of category names to include (None = all)
        exclude_etfs: if True, skip the 'etfs' category
        exclude_leveraged: if True, remove leveraged ETFs (SOXL, TQQQ, etc.)

    Returns:
        Sorted list of unique symbols.
    """
    symbols: Set[str] = set()

    if categories:
        for cat in categories:
            if cat in UNIVERSE:
                symbols.update(UNIVERSE[cat])
    else:
        for cat, syms in UNIVERSE.items():
            if exclude_etfs and cat == "etfs":
                continue
            symbols.update(syms)

    if exclude_leveraged:
        leveraged = {"SOXL", "TQQQ", "SQQQ", "SPXL", "SPXS", "UVXY", "VXX"}
        symbols -= leveraged

    return sorted(symbols)


def get_categories() -> Dict[str, int]:
    """Return category names and their symbol counts."""
    return {cat: len(syms) for cat, syms in UNIVERSE.items()}


def get_universe_stats() -> Dict:
    """Return summary statistics about the universe."""
    all_syms = get_universe()
    return {
        "total_symbols": len(all_syms),
        "categories": get_categories(),
        "stocks_only": len(get_universe(exclude_etfs=True)),
        "etfs_only": len(UNIVERSE.get("etfs", [])),
    }


# ---------------------------------------------------------------------------
# CSV loading (for custom universes)
# ---------------------------------------------------------------------------

def load_universe_csv(filepath: str) -> List[str]:
    """
    Load a universe from a CSV file.

    Supports:
      - CSV with 'Symbol' or 'Ticker' column header
      - Plain list of symbols (one per line or comma-separated)
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {filepath}")

    symbols: List[str] = []

    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    # Detect format
    if "," in content.split("\n")[0] and any(
        h in content.split("\n")[0].lower() for h in ("symbol", "ticker")
    ):
        # CSV with header
        lines = content.split("\n")
        reader = csv.DictReader(lines)
        for row in reader:
            sym = (
                row.get("Symbol") or row.get("symbol") or
                row.get("Ticker") or row.get("ticker") or ""
            ).strip().upper()
            if sym and sym.isalpha() and 1 <= len(sym) <= 5:
                symbols.append(sym)
    else:
        # Plain list — one per line or comma-separated
        for line in content.split("\n"):
            for token in line.split(","):
                sym = token.strip().upper()
                if sym and sym.replace(".", "").isalpha() and 1 <= len(sym) <= 5:
                    symbols.append(sym)

    return sorted(set(symbols))


def load_universe(config=None) -> List[str]:
    """
    Load universe with priority:
      1. DAYTRADER_UNIVERSE env var (CSV path or comma-separated)
      2. Full default universe
    """
    env = os.environ.get("DAYTRADER_UNIVERSE", "").strip()

    if env:
        if os.path.isfile(env):
            try:
                return load_universe_csv(env)
            except Exception as e:
                print(f"Error loading universe from {env}: {e}, using default")
        else:
            # Treat as comma-separated symbols
            syms = [s.strip().upper() for s in env.split(",") if s.strip()]
            if syms:
                return sorted(set(syms))

    return get_universe()


# ---------------------------------------------------------------------------
# CLI preview
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    stats = get_universe_stats()
    print(f"DayTrader v2 Universe: {stats['total_symbols']} symbols")
    print(f"  Stocks: {stats['stocks_only']}  |  ETFs: {stats['etfs_only']}")
    print()
    for cat, count in stats["categories"].items():
        syms = UNIVERSE[cat]
        preview = ", ".join(syms[:8])
        more = f"... +{count - 8}" if count > 8 else ""
        print(f"  {cat:20s} ({count:3d}): {preview}{more}")
