"""
The nine sequential validation gates plus the commit step. Cheapest checks
run first — a source can be rejected by IP rate limiting or a malformed
parse before the gateway ever spends a cycle on cryptography. Each gate is a
small pure-ish function so it can be unit-tested in isolation; pipeline.py
is what chains them in order and owns the pass/fail-and-drop control flow.
"""

import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from . import wire
from .state import TrustLedger, SessionStore, ReplayWindow, FreshnessTracker

FRESHNESS_MAX_DELAY_MS = 5000
PLAUSIBLE_RANGE = (-400, 1250)   # scaled 0.1 degC steps: -40.0C .. 125.0C
PLAUSIBLE_MAX_SLEW = 80          # max plausible change between consecutive readings
PLAUSIBLE_HOLD_BAND = 60         # slew beyond MAX_SLEW but within this band -> hold, not hard-fail


@dataclass
class GateResult:
    gate: int
    name: str
    passed: bool
    reason: str
    penalize: bool = True  # whether a failure here costs the device trust score
    held: bool = False     # True only for gate 8's "hold, don't decide yet" outcome


class RateLimiter:
    """Fixed-window counter. Capacity is passed in per call so gate 5 can
    scale it with a device's trust score without needing its own limiter."""

    def __init__(self, window_s: float = 1.0):
        self.window_s = window_s
        self._state: Dict[str, Tuple[float, int]] = {}

    def allow(self, key: str, capacity: int) -> bool:
        now = time.time()
        start, count = self._state.get(key, (now, 0))
        if now - start >= self.window_s:
            start, count = now, 0
        if count >= capacity:
            self._state[key] = (start, count)
            return False
        self._state[key] = (start, count + 1)
        return True


def gate0_rate_limit_ip(source_ip: str, limiter: RateLimiter, capacity: int = 20) -> GateResult:
    ok = limiter.allow(f"ip:{source_ip}", capacity)
    return GateResult(0, "rate_limit_ip", ok, "ok" if ok else f"source {source_ip} exceeded the IP rate limit")


def gate1_canonical_parse(raw_wire: str):
    try:
        fields = wire.parse(raw_wire)
        return GateResult(1, "canonical_parser", True, "ok"), fields
    except wire.WireFormatError as exc:
        return GateResult(1, "canonical_parser", False, f"malformed message: {exc}"), None


def gate2_trust_ledger(device_id: str, trust: TrustLedger):
    ok, record, reason = trust.check(device_id)
    return GateResult(2, "trust_ledger_check", ok, reason), record


def gate3_session_check(device_id: str, session_id: str, sessions: SessionStore):
    if sessions.is_valid(device_id, session_id):
        return GateResult(3, "session_check", True, "ok"), None
    new_sid = sessions.issue(device_id)
    # A missing session is routine, not an attack signal, so it never costs trust.
    return (GateResult(3, "session_check", False, "no active session — re-challenging",
                        penalize=False), new_sid)


def gate4_hmac_verify(header: str, mac: str, secret_key: bytes) -> GateResult:
    ok = wire.verify(secret_key, header, mac)
    return GateResult(4, "hmac_verify", ok, "ok" if ok else "HMAC mismatch — message forged or corrupted")


def gate5_adaptive_rate_limit(device_id: str, trust_score: int, limiter: RateLimiter,
                               base_capacity: int = 5) -> GateResult:
    capacity = base_capacity + trust_score // 10
    ok = limiter.allow(f"dev:{device_id}", capacity)
    return GateResult(5, "adaptive_rate_limit", ok,
                       "ok" if ok else f"exceeded trust-scaled rate limit ({capacity}/s at score {trust_score})")


def gate6_replay_window(device_id: str, seq: int, window: ReplayWindow) -> GateResult:
    ok, reason = window.check_and_mark(device_id, seq)
    return GateResult(6, "replay_window", ok, reason)


def gate7_freshness(device_id: str, uptime_ms: int, received_at_ms: int,
                     tracker: FreshnessTracker, max_delay_ms: int = FRESHNESS_MAX_DELAY_MS) -> GateResult:
    ok, reason = tracker.check(device_id, uptime_ms, received_at_ms, max_delay_ms)
    return GateResult(7, "freshness_check", ok, reason)


def gate8_plausibility(value: int, last_value: Optional[int]) -> GateResult:
    """Evaluates the *current* message only. Resolving a previously-held
    reading against this message's value is the pipeline's job (it needs to
    commit or quarantine a different, earlier message), not this gate's."""
    lo, hi = PLAUSIBLE_RANGE
    if not (lo <= value <= hi):
        return GateResult(8, "plausibility_hold", False,
                           f"value {value} outside physical range [{lo},{hi}]")

    slew = abs(value - last_value) if last_value is not None else 0
    if slew <= PLAUSIBLE_MAX_SLEW:
        return GateResult(8, "plausibility_hold", True, "ok")
    if slew <= PLAUSIBLE_MAX_SLEW + PLAUSIBLE_HOLD_BAND:
        return GateResult(8, "plausibility_hold", False,
                           f"slew {slew} borderline — held for corroboration",
                           penalize=False, held=True)
    return GateResult(8, "plausibility_hold", False,
                       f"slew {slew} exceeds any plausible sensor rate")
