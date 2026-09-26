import sys
from pathlib import Path

# Lets `pytest` run from agent/ (or the repo root) and import the `triage`
# package without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
