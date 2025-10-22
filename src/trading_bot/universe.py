"""
Universe loader - Fixed to properly handle CSV files
"""
from __future__ import annotations
import os
import csv
import pathlib
from typing import List


def load_universe(default: List[str] | None = None) -> List[str]:
    """
    Load trading universe from environment variable or use default

    Priority:
    1. DAYTRADER_UNIVERSE env var pointing to CSV file
    2. DAYTRADER_UNIVERSE env var with comma-separated symbols
    3. Default list provided
    4. Fallback default: ["AAPL", "MSFT", "NVDA"]
    """
    if default is None:
        default = ["AAPL", "MSFT", "NVDA"]

    env = (os.environ.get("DAYTRADER_UNIVERSE") or "").strip()

    if not env:
        return default

    # Check if it's a file path
    if env.endswith('.csv') or '/' in env or '\\' in env:
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
                return _load_symbols_from_csv(p)
            except Exception as e:
                print(f"Error loading universe from {p}: {e}")
                print(f"Falling back to default universe")
                return default
        else:
            print(f"Universe file not found: {env}")
            print(f"Falling back to default universe")
            return default

    # Not a file path - treat as comma-separated list
    symbols = [s.strip().upper() for s in env.split(",") if s.strip()]

    if symbols:
        # Filter out invalid symbols (no special characters, reasonable length)
        valid_symbols = [
            s for s in symbols
            if s.isalpha() and 1 <= len(s) <= 5
        ]

        if valid_symbols:
            return sorted(list(dict.fromkeys(valid_symbols)))  # Remove duplicates
        else:
            print(f"No valid symbols found in: {env}")
            return default

    return default


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

                # Only accept valid stock symbols
                if symbol.isalpha() and 1 <= len(s) <= 5:
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