"""api/index.py — Serverless function entrypoint for Vercel deployment.

Vercel Python runtime discovers this file and serves the FastAPI application.
All routes under /api/* are rewritten here by vercel.json.
"""
import sys
from pathlib import Path

# Ensure repository root is on sys.path so modules like sdoc_* and security_layer can be imported
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import app  # noqa: E402, F401
