"""Small shared helper for the text-only Gemini calls (Ask Navis question translation, optional classifier fallback,
evaluation). Scanned-PDF vision has its own client handling in sdoc_extractor.py.

GEMINI_API_KEYS or GEMINI_API_KEY may hold one key or a comma-separated pool; a key that fails is skipped for the
next one.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
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


# Per-key health for this process. A key whose project is denied (403) is skipped from then on; a key that hit its
# rate limit (429) rests for the delay Gemini asks for, so a burst of work does not keep hammering it.
_denied: set = set()
_resting: Dict[str, float] = {}
_state_lock = threading.Lock()
DEFAULT_REST_S = 30.0


def _retry_delay(err: str) -> float:
    m = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", err) or re.search(r"retry in (\d+(?:\.\d+)?)\s*s", err, re.I)
    return float(m.group(1)) + 1 if m else DEFAULT_REST_S


def usable_keys() -> List[str]:
    now = time.monotonic()
    with _state_lock:
        return [k for k in api_keys() if k not in _denied and _resting.get(k, 0) <= now]


def seconds_until_a_key_is_free() -> Optional[float]:
    """0 when a key can be used now, the wait until the first resting key is free, or None when every key is denied."""
    keys = [k for k in api_keys() if k not in _denied]
    if not keys:
        return None
    now = time.monotonic()
    return max(0.0, min(_resting.get(k, 0) - now for k in keys))


def generate_json(prompt: str, model: Optional[str] = None, wait_s: float = 0) -> Tuple[Optional[Any], Dict[str, Any]]:
    """Ask for a JSON reply. Returns (parsed JSON or None, call record with model, latency and token counts).
    Never raises: a failure is reported in the record so callers can fall back to the rule-based path.
    With wait_s > 0, waits up to that long for a rate-limited key to become free instead of giving up; callers on a
    request path leave it at 0 and only background work waits."""
    model = model or text_model()
    rec: Dict[str, Any] = {"model": model, "ok": False}
    t0 = time.perf_counter()
    deadline = time.monotonic() + wait_s
    last_error = "no Gemini API key configured" if not api_keys() else "every Gemini key is rate limited or denied"
    while True:
        for key in usable_keys():
            try:
                r = _client(key).models.generate_content(model=model, contents=prompt,
                                                         config={"response_mime_type": "application/json", "temperature": 0})
                data = json.loads(r.text)
                u = r.usage_metadata
                rec.update(ok=True, prompt_tokens=u.prompt_token_count or 0,
                           output_tokens=(u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0))
                rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
                return data, rec
            except Exception as e:  # noqa: BLE001 — try the next key, then give up quietly
                err = str(e)
                last_error = f"{type(e).__name__}: {err[:160]}"
                with _state_lock:
                    if "403" in err or "PERMISSION_DENIED" in err:
                        _denied.add(key)
                    elif "429" in err or "RESOURCE_EXHAUSTED" in err:
                        _resting[key] = time.monotonic() + _retry_delay(err)
                logger.warning("Gemini text call failed with one key: %s", last_error)
        free_in = seconds_until_a_key_is_free()
        if free_in is None or time.monotonic() + free_in > deadline:
            break
        time.sleep(max(free_in, 0.5))
    rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    rec["error"] = last_error
    rec["retry_after_s"] = seconds_until_a_key_is_free()
    return None, rec
