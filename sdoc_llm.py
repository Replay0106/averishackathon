"""Small shared helper for the text-only Gemini calls (Ask Navis question translation, optional classifier fallback,
evaluation). Scanned-PDF vision has its own client handling in sdoc_extractor.py.

GEMINI_API_KEYS or GEMINI_API_KEY may hold one key or a comma-separated pool; a key that fails is skipped for the
next one.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TEXT_MODEL = "gemini-3.5-flash-lite"
_clients: Dict[str, Any] = {}


def api_keys() -> List[str]:
    raw = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    return [k.strip() for k in raw.split(",") if k.strip()]


def available() -> bool:
    """True when a key is configured. Under pytest/unittest it is off (a developer's real keys in .env must not make
    the test suite call Gemini) unless NAVIS_ALLOW_LIVE_LLM=1."""
    from sdoc_store import _under_test_runner

    if _under_test_runner() and os.environ.get("NAVIS_ALLOW_LIVE_LLM") != "1":
        return False
    try:
        from google import genai  # noqa: F401
    except ImportError:
        return False
    return bool(api_keys())


def text_model() -> str:
    return os.environ.get("NAVIS_TEXT_MODEL") or DEFAULT_TEXT_MODEL


def _client(key: str):
    if key not in _clients:
        from google import genai

        _clients[key] = genai.Client(api_key=key)
    return _clients[key]


def generate_json(prompt: str, model: Optional[str] = None) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Ask for a JSON reply. Returns (parsed JSON or None, call record with model, latency and token counts).
    Never raises: a failure is reported in the record so callers can fall back to the rule-based path."""
    model = model or text_model()
    rec: Dict[str, Any] = {"model": model, "ok": False}
    t0 = time.perf_counter()
    last_error = "no Gemini API key configured"
    for key in api_keys():
        try:
            r = _client(key).models.generate_content(model=model, contents=prompt,
                                                     config={"response_mime_type": "application/json", "temperature": 0})
            data = json.loads(r.text)
            u = r.usage_metadata
            rec.update(ok=True, prompt_tokens=u.prompt_token_count or 0,
                       output_tokens=(u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0))
            return data, rec
        except Exception as e:  # noqa: BLE001 — try the next key, then give up quietly
            last_error = f"{type(e).__name__}: {str(e)[:160]}"
            logger.warning("Gemini text call failed with one key: %s", last_error)
        finally:
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    rec["error"] = last_error
    return None, rec
