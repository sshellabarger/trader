import sys
from pathlib import Path

# Make the `trader` package importable: the git repo root IS the package
# directory, so its parent must be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
