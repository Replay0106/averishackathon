"""
Canonical wire format for edge -> gateway telemetry messages.

Approach A (pure authentication): a fixed-order plaintext ASCII header plus an
HMAC-SHA256 signature. No payload encryption — the goal is a format that is
trivial to parse strictly, easy to debug on the wire, and cheap to verify,
so gate 1 can reject anything malformed before any cryptography runs at all.

    T <device_id> <session_id> <seq> <metric> <scaled_value> <uptime_ms> <hmac>
"""

import hmac
import hashlib
import re

_HEADER_RE = re.compile(
    r"^T (?P<device_id>[A-Za-z0-9_-]{1,32}) (?P<session_id>[A-Za-z0-9_-]{1,32}) "
    r"(?P<seq>\d{1,20}) (?P<metric>[A-Z_]{1,16}) (?P<scaled_value>-?\d{1,12}) "
    r"(?P<uptime_ms>\d{1,20})$"
)
_MAC_RE = re.compile(r"^[0-9a-f]{64}$")


class WireFormatError(ValueError):
    """Raised for any message that does not match the canonical grammar."""


def encode_header(device_id: str, session_id: str, seq: int, metric: str,
                   scaled_value: int, uptime_ms: int) -> str:
    for name, val in (("device_id", device_id), ("session_id", session_id), ("metric", metric)):
        if not val or " " in val:
            raise WireFormatError(f"invalid {name}: {val!r}")
    return f"T {device_id} {session_id} {seq} {metric} {scaled_value} {uptime_ms}"


def sign(secret_key: bytes, header: str) -> str:
    return hmac.new(secret_key, header.encode("ascii"), hashlib.sha256).hexdigest()


def encode(device_id: str, session_id: str, seq: int, metric: str,
           scaled_value: int, uptime_ms: int, secret_key: bytes) -> str:
    header = encode_header(device_id, session_id, seq, metric, scaled_value, uptime_ms)
    return f"{header} {sign(secret_key, header)}"


def parse(raw: str) -> dict:
    """Strict canonical parse (gate 1). Raises WireFormatError on anything that
    doesn't match the grammar exactly — this runs before any crypto check."""
    parts = raw.strip().split(" ")
    if len(parts) != 8:
        raise WireFormatError(f"expected 8 space-separated fields, got {len(parts)}")

    header = " ".join(parts[:7])
    mac = parts[7]

    match = _HEADER_RE.match(header)
    if not match:
        raise WireFormatError(f"header does not match canonical grammar: {header!r}")
    if not _MAC_RE.match(mac):
        raise WireFormatError("mac is not a 64-character lowercase hex SHA-256 digest")

    fields = match.groupdict()
    fields["seq"] = int(fields["seq"])
    fields["scaled_value"] = int(fields["scaled_value"])
    fields["uptime_ms"] = int(fields["uptime_ms"])
    fields["header"] = header
    fields["mac"] = mac
    fields["raw"] = raw
    return fields


def verify(secret_key: bytes, header: str, mac: str) -> bool:
    expected = sign(secret_key, header)
    return hmac.compare_digest(expected, mac)
