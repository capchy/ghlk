"""Vercel Python function: serves the FastAPI backend (/api/* and /health).

vercel.json rewrites those paths here; FastAPI still sees the original path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402

__all__ = ["app"]
