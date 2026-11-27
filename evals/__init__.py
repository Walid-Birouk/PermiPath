"""Evaluation harness: golden question set, retrieval runs, metrics and report comparison.

Run from the repository root with the backend environment, for example
`uv run --project backend python -m evals.validate`. The backend packages (`db`, `rag`, ...) are
imported by putting `backend/` on the import path (D-001).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
