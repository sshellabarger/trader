"""
Minimal .env loader — no external dependencies.
Loads key=value pairs from a .env file into os.environ.
Skips comments (#) and blank lines.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(filepath: str = ".env") -> int:
    """
    Load .env file into os.environ.
    Searches upward from CWD to find the file.
    Returns number of variables loaded.
    """
    env_path = _find_env_file(filepath)
    if env_path is None:
        return 0

    count = 0
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue

            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # Remove surrounding quotes if present
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]

            # Only set if not already in environment (env vars take precedence)
            if key not in os.environ:
                os.environ[key] = value
                count += 1

    return count


def _find_env_file(filename: str) -> Path | None:
    """Search for .env file in current dir, then parent dirs, then package dir."""
    # Check current directory
    cwd = Path.cwd()
    candidate = cwd / filename
    if candidate.is_file():
        return candidate

    # Check parent directories (up to 3 levels)
    for _ in range(3):
        cwd = cwd.parent
        candidate = cwd / filename
        if candidate.is_file():
            return candidate

    # Check package directory
    pkg_dir = Path(__file__).parent
    candidate = pkg_dir / filename
    if candidate.is_file():
        return candidate

    return None
