"""
Minimal .env loader — no external dependencies.
Loads key=value pairs from a .env file into os.environ.
Skips comments (#) and blank lines.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Set to the path actually loaded (str) after load_dotenv(), None if nothing
# was loaded. Lets the operator confirm WHICH credentials file is in effect.
loaded_env_path: str | None = None


def load_dotenv(filepath: str = ".env") -> int:
    """
    Load .env file into os.environ.
    Looks only in explicit/known locations (see _find_env_file).
    Returns number of variables loaded.
    """
    global loaded_env_path
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

    loaded_env_path = str(env_path)
    logger.debug(f"Loaded {count} vars from {env_path}")
    return count


def _find_env_file(filename: str) -> Path | None:
    """Resolve the .env file from explicit config or known locations ONLY.

    Order: $TRADER_ENV_FILE (explicit path, wins) → CWD → the package
    directory (trader/.env, the documented home for dev credentials).

    Parent-directory walking was removed deliberately: on a shared or
    multi-project machine it could silently load some OTHER project's .env,
    and since APCA_API_BASE_URL is honored from the environment, that could
    point credentials and orders at an unintended endpoint.
    """
    explicit = os.environ.get("TRADER_ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        logger.warning(f"TRADER_ENV_FILE set but not a file: {explicit}")
        return None

    candidate = Path.cwd() / filename
    if candidate.is_file():
        return candidate

    candidate = Path(__file__).parent / filename
    if candidate.is_file():
        return candidate

    return None
