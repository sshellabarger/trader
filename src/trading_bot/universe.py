"""
Universe loader - Fixed to properly handle CSV files
"""
from __future__ import annotations
import os
import csv
import pathlib
from typing import List, Any, Dict


def load_universe(settings: Any = None, default: List[str] | None = None) -> List[str]:
    """
    Load trading universe from environment variable or use default,
    then combine with crypto/forex/ETF symbols from settings

    Priority:
    1. DAYTRADER_UNIVERSE env var pointing to CSV file
    2. DAYTRADER_UNIVERSE env var with comma-separated symbols
    3. Default list provided
    4. Fallback default: ["AAPL", "MSFT", "NVDA"]

    Then adds:
    - Crypto symbols from settings if crypto strategy enabled
    - Forex symbols from settings if forex strategy enabled
    - ETF symbols from settings if etf strategy enabled
    """
    if default is None:
        default = ["AAPL", "MSFT", "NVDA"]

    # Load base universe (stocks from CSV or default)
    base_symbols = []
    env = (os.environ.get("DAYTRADER_UNIVERSE") or "").strip()

    if not env:
        base_symbols = default
    elif env.endswith('.csv') or '/' in env or '\\' in env:
        # It's a path - try to load CSV
        p = pathlib.Path(env)

        # Handle relative paths
        if not p.is_absolute():
            # Try relative to current directory
            if not p.exists():
                # Try relative to script directory
                script_dir = pathlib.Path(__file__).parent.parent.parent
                p = script_dir / env

        if p.exists() and p.is_file():
            try:
                base_symbols = _load_symbols_from_csv(p)
            except Exception as e:
                print(f"Error loading universe from {p}: {e}")
                print(f"Falling back to default universe")
                base_symbols = default
        else:
            print(f"Universe file not found: {env}")
            print(f"Falling back to default universe")
            base_symbols = default
    else:
        # Not a file path - treat as comma-separated list
        symbols = [s.strip().upper() for s in env.split(",") if s.strip()]

        if symbols:
            # Filter out invalid symbols (allow / for forex/crypto)
            valid_symbols = [
                s for s in symbols
                if (s.replace('/', '').isalpha() or s.isalpha()) and 1 <= len(s) <= 10
            ]

            if valid_symbols:
                base_symbols = sorted(list(dict.fromkeys(valid_symbols)))  # Remove duplicates
            else:
                print(f"No valid symbols found in: {env}")
                base_symbols = default
        else:
            base_symbols = default

    # Add crypto/forex/ETF symbols from settings if enabled
    all_symbols = base_symbols.copy()

    if settings:
        # Get settings dict
        if hasattr(settings, 'get'):
            settings_dict = settings.get if callable(settings.get) else settings
        elif hasattr(settings, 'as_dict'):
            settings_dict = settings.as_dict()
        elif isinstance(settings, dict):
            settings_dict = settings
        else:
            settings_dict = {}

        # Helper to get setting value
        def get_setting(category, key=None):
            if callable(settings_dict):
                # settings_dict is a callable (like Settings.get method)
                cat_data = settings_dict(category, {})
                if key and isinstance(cat_data, dict):
                    return cat_data.get(key)
                return cat_data
            elif isinstance(settings_dict, dict):
                cat_data = settings_dict.get(category, {})
                if key and isinstance(cat_data, dict):
                    return cat_data.get(key)
                return cat_data
            return None

        # Add crypto symbols if enabled
        crypto_enabled = get_setting('strategies', 'crypto') or get_setting('crypto', 'enabled')
        if crypto_enabled:
            crypto_universe = get_setting('crypto', 'universe') or []
            if crypto_universe:
                all_symbols.extend(crypto_universe)
                print(f"Added {len(crypto_universe)} crypto symbols from settings")

        # Add forex symbols if enabled
        forex_enabled = get_setting('strategies', 'forex') or get_setting('forex', 'enabled')
        if forex_enabled:
            forex_universe = get_setting('forex', 'universe') or []
            if forex_universe:
                all_symbols.extend(forex_universe)
                print(f"Added {len(forex_universe)} forex symbols from settings")

        # Add ETF symbols if enabled
        etf_enabled = get_setting('strategies', 'etf') or get_setting('etf', 'enabled')
        if etf_enabled:
            etf_universe = get_setting('etf', 'universe') or []
            if etf_universe:
                all_symbols.extend(etf_universe)
                print(f"Added {len(etf_universe)} ETF symbols from settings")

    # Remove duplicates and return
    return sorted(list(dict.fromkeys(all_symbols)))


def _load_symbols_from_csv(filepath: pathlib.Path) -> List[str]:
    """
    Load symbols from CSV file

    Handles two formats:
    1. CSV with 'Symbol' column header
    2. CSV with symbols in first column (no header or any header)
    """
    symbols: List[str] = []

    with filepath.open('r', encoding='utf-8') as f:
        # Try to detect if there's a header
        sample = f.read(1024)
        f.seek(0)

        # Check if first line looks like a header
        first_line = sample.split('\n')[0] if '\n' in sample else sample
        has_symbol_header = 'symbol' in first_line.lower()

        # Read CSV
        reader = csv.DictReader(f) if has_symbol_header else csv.reader(f)

        if has_symbol_header:
            # Use DictReader - look for 'Symbol' column
            for row in reader:
                # Try different possible column names
                symbol = (
                        row.get('Symbol') or
                        row.get('SYMBOL') or
                        row.get('symbol') or
                        row.get('Ticker') or
                        row.get('ticker')
                )

                if symbol:
                    s = symbol.strip().upper()
                    if s and s.isalpha() and 1 <= len(s) <= 5:
                        symbols.append(s)
        else:
            # Use regular reader - first column is symbol
            for row in reader:
                if not row:
                    continue

                symbol = str(row[0]).strip().upper()

                # Skip if it looks like a header
                if symbol.lower() in ['symbol', 'ticker', 'name']:
                    continue

                # Only accept valid stock symbols (allow / for forex/crypto)
                if (symbol.replace('/', '').isalpha() or symbol.replace('/', '').replace('-', '').isalpha()) and 1 <= len(symbol) <= 10:
                    symbols.append(symbol)

    # Remove duplicates and sort
    unique_symbols = sorted(list(dict.fromkeys(symbols)))

    print(f"Loaded {len(unique_symbols)} symbols from {filepath}")

    return unique_symbols


# For testing
if __name__ == '__main__':
    print("Testing universe loader...")

    # Test 1: No env var
    print("\n1. No env var (should use default):")
    symbols = load_universe()
    print(f"   Result: {symbols}")

    # Test 2: Comma-separated
    os.environ['DAYTRADER_UNIVERSE'] = "AAPL,MSFT,GOOGL,TSLA"
    print("\n2. Comma-separated:")
    symbols = load_universe()
    print(f"   Result: {symbols}")

    # Test 3: Invalid file path
    os.environ['DAYTRADER_UNIVERSE'] = "data/nonexistent.csv"
    print("\n3. Nonexistent file (should fallback to default):")
    symbols = load_universe()
    print(f"   Result: {symbols}")

    print("\n✓ Tests complete")