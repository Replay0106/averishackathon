"""
Gateway-side state the gates consult: a continuous trust score per device
(gates 2 and 5), sessions (gate 3), the replay window (gate 6), a freshness
baseline (gate 7), and the corroboration buffer for borderline anomalies
(gate 8). Kept as small, independently testable classes rather than one
grab-bag object.
"""

import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

TRUST_START = 70
TRUST_FLOOR = 20
TRUST_MAX = 100
TRUST_PENALTY = 8
TRUST_SOFT_PENALTY = 4
TRUST_REWARD = 1


@dataclass
class DeviceRecord:
    device_id: str
    secret_key: bytes
    trust_score: int = TRUST_START
    revoked: bool = False


class TrustLedger:
    """Continuous per-device trust score, in place of a binary ACTIVE/REVOKED
    registry. A device below the floor is treated exactly like a revoked one,
    but everything above the floor degrades gradually instead of as a cliff."""

    def __init__(self):
        self._devices: Dict[str, DeviceRecord] = {}

    def register(self, device_id: str, secret_key: bytes, initial_score: int = TRUST_START):
        self._devices[device_id] = DeviceRecord(device_id, secret_key, initial_score)

    def get(self, device_id: str) -> Optional[DeviceRecord]:
        return self._devices.get(device_id)

    def revoke(self, device_id: str):
        rec = self._devices.get(device_id)
        if rec:
            rec.revoked = True

    def check(self, device_id: str) -> Tuple[bool, Optional[DeviceRecord], str]:
        rec = self._devices.get(device_id)
        if rec is None:
            return False, None, "unknown device (not enrolled)"
        if rec.revoked:
            return False, rec, "device revoked"
        if rec.trust_score < TRUST_FLOOR:
            return False, rec, f"trust score {rec.trust_score} below floor {TRUST_FLOOR}"
        return True, rec, "ok"

    def reward(self, device_id: str, amount: int = TRUST_REWARD):
        rec = self._devices.get(device_id)
        if rec:
            rec.trust_score = min(TRUST_MAX, rec.trust_score + amount)

    def penalize(self, device_id: str, amount: int = TRUST_PENALTY):
        rec = self._devices.get(device_id)
        if rec:
            rec.trust_score = max(0, rec.trust_score - amount)


class SessionStore:
    """Tracks the one active session id per device."""

    def __init__(self):
        self._sessions: Dict[str, str] = {}

    def is_valid(self, device_id: str, session_id: str) -> bool:
        return self._sessions.get(device_id) == session_id

    def issue(self, device_id: str) -> str:
        sid = "s" + secrets.token_hex(4)
        self._sessions[device_id] = sid
        return sid


class ReplayWindow:
    """64-bit sliding replay window per device: bit 0 is the highest sequence
    number seen, each higher bit an older one. A duplicate or too-old sequence
    number is silently dropped rather than treated as a parse failure."""

    WINDOW = 64
    MASK = (1 << WINDOW) - 1

    def __init__(self):
        self._highest: Dict[str, int] = {}
        self._bitmap: Dict[str, int] = {}

    def check_and_mark(self, device_id: str, seq: int) -> Tuple[bool, str]:
        highest = self._highest.get(device_id, -1)
        bitmap = self._bitmap.get(device_id, 0)

        if seq > highest:
            shift = seq - highest
            bitmap = 0 if shift >= self.WINDOW else (bitmap << shift) & self.MASK
            bitmap |= 1
            self._highest[device_id] = seq
            self._bitmap[device_id] = bitmap
            return True, "ok"

        offset = highest - seq
        if offset >= self.WINDOW:
            return False, f"seq {seq} is older than the {self.WINDOW}-message replay window"
        if bitmap & (1 << offset):
            return False, f"seq {seq} already seen — replay"
        self._bitmap[device_id] = bitmap | (1 << offset)
        return True, "ok"


class FreshnessTracker:
    """Flags a message that arrived much later than its own uptime clock says
    it should have — the classic sign of a captured message replayed after a
    delay. Compares elapsed *uptime* to elapsed *wall clock* since the first
    message from that device, so it never needs the edge and gateway clocks
    to be synchronized — only the gateway's own clock to be self-consistent."""

    def __init__(self):
        self._baseline: Dict[str, Tuple[int, int]] = {}  # device_id -> (uptime_ms, received_ms)

    def check(self, device_id: str, uptime_ms: int, received_at_ms: int,
              max_delay_ms: int) -> Tuple[bool, str]:
        base = self._baseline.get(device_id)
        if base is None:
            self._baseline[device_id] = (uptime_ms, received_at_ms)
            return True, "ok (baseline established)"

        base_uptime, base_received = base
        expected_elapsed = uptime_ms - base_uptime
        actual_elapsed = received_at_ms - base_received
        delay = max(0, actual_elapsed - expected_elapsed)
        if delay > max_delay_ms:
            return False, (f"arrived {delay}ms later than its own uptime clock accounts for "
                            f"(limit {max_delay_ms}ms) — possible hold-and-replay")
        return True, "ok"


@dataclass
class HeldReading:
    seq: int
    value: int
    fields: dict
    ts: float


class CorroborationBuffer:
    """Holds a borderline plausibility call instead of dropping it on the spot.
    Resolved by the *next* reading from the same device: if it lands close to
    the held value, the held reading is corroborated and committed; if not,
    it's quarantined as noise. Sustained anomalies still get caught — they
    just aren't punished for a single ambiguous sample."""

    TOLERANCE = 25  # scaled units the next reading may differ by and still "confirm"

    def __init__(self):
        self._pending: Dict[str, HeldReading] = {}

    def hold(self, device_id: str, seq: int, value: int, fields: dict):
        self._pending[device_id] = HeldReading(seq, value, fields, time.time())

    def has_pending(self, device_id: str) -> bool:
        return device_id in self._pending

    def resolve(self, device_id: str, next_value: int) -> Optional[Tuple[HeldReading, bool]]:
        """Call when the next reading for this device arrives. Returns
        (held_reading, confirmed) and clears the hold, or None if nothing was held."""
        held = self._pending.pop(device_id, None)
        if held is None:
            return None
        confirmed = abs(next_value - held.value) <= self.TOLERANCE
        return held, confirmed
