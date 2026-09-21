"""
The gateway: chains the nine gates in strict order, resolves the
corroboration buffer, commits accepted messages to the Merkle audit log, and
runs the trust-score feedback loop (gate outcomes adjust the score that
gates 2 and 5 read on the *next* message).
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import gates
from .ledger import MerkleAuditLog
from .state import (
    CorroborationBuffer,
    FreshnessTracker,
    ReplayWindow,
    SessionStore,
    TrustLedger,
)


@dataclass
class Decision:
    accepted: bool
    device_id: Optional[str]
    seq: Optional[int]
    failed_gate: Optional[str]
    reason: str
    trust_score_after: Optional[int] = None
    record_id: Optional[int] = None
    new_session_id: Optional[str] = None
    resolved_hold: Optional[Dict[str, Any]] = None  # set if this call also settled an earlier hold
    trace: List[Dict[str, Any]] = field(default_factory=list)  # per-gate results, in order, for this message


class Gateway:
    def __init__(self, ledger_path: Optional[str] = None):
        self.trust = TrustLedger()
        self.sessions = SessionStore()
        self.replay = ReplayWindow()
        self.freshness = FreshnessTracker()
        self.corroboration = CorroborationBuffer()
        self.ledger = MerkleAuditLog(ledger_path)

        self._ip_limiter = gates.RateLimiter(window_s=1.0)
        self._device_limiter = gates.RateLimiter(window_s=1.0)
        self._last_value: Dict[str, int] = {}

        self.quarantine: List[Decision] = []
        self.accepted: List[Decision] = []

    def register_device(self, device_id: str, secret_key: bytes, initial_trust: int = 70):
        self.trust.register(device_id, secret_key, initial_trust)

    def _drop(self, gate_name: str, reason: str, device_id: Optional[str] = None,
              seq: Optional[int] = None, penalize: bool = True,
              new_session_id: Optional[str] = None) -> Decision:
        if device_id and penalize:
            self.trust.penalize(device_id)
        record = self.trust.get(device_id) if device_id else None
        decision = Decision(
            accepted=False, device_id=device_id, seq=seq, failed_gate=gate_name,
            reason=reason, trust_score_after=record.trust_score if record else None,
            new_session_id=new_session_id,
        )
        self.quarantine.append(decision)
        return decision

    def _commit(self, fields: Dict[str, Any]) -> int:
        payload = {
            "device_id": fields["device_id"],
            "seq": fields["seq"],
            "metric": fields["metric"],
            "scaled_value": fields["scaled_value"],
            "uptime_ms": fields["uptime_ms"],
        }
        return self.ledger.append(payload)

    def ingest(self, raw_wire: str, source_ip: str, received_at_ms: Optional[int] = None) -> Decision:
        received_at_ms = received_at_ms if received_at_ms is not None else int(time.time() * 1000)
        trace: List[Dict[str, Any]] = []

        def record_step(r) -> None:
            trace.append({"gate": r.gate, "name": r.name, "passed": r.passed, "reason": r.reason})

        r0 = gates.gate0_rate_limit_ip(source_ip, self._ip_limiter)
        record_step(r0)
        if not r0.passed:
            d = self._drop(r0.name, r0.reason)
            d.trace = trace
            return d

        r1, fields = gates.gate1_canonical_parse(raw_wire)
        record_step(r1)
        if not r1.passed:
            d = self._drop(r1.name, r1.reason)
            d.trace = trace
            return d
        device_id, seq = fields["device_id"], fields["seq"]

        r2, record = gates.gate2_trust_ledger(device_id, self.trust)
        record_step(r2)
        if not r2.passed:
            d = self._drop(r2.name, r2.reason, device_id, seq)
            d.trace = trace
            return d

        r3, new_sid = gates.gate3_session_check(device_id, fields["session_id"], self.sessions)
        record_step(r3)
        if not r3.passed:
            d = self._drop(r3.name, r3.reason, device_id, seq, penalize=False, new_session_id=new_sid)
            d.trace = trace
            return d

        r4 = gates.gate4_hmac_verify(fields["header"], fields["mac"], record.secret_key)
        record_step(r4)
        if not r4.passed:
            d = self._drop(r4.name, r4.reason, device_id, seq)
            d.trace = trace
            return d

        r5 = gates.gate5_adaptive_rate_limit(device_id, record.trust_score, self._device_limiter)
        record_step(r5)
        if not r5.passed:
            d = self._drop(r5.name, r5.reason, device_id, seq)
            d.trace = trace
            return d

        r6 = gates.gate6_replay_window(device_id, seq, self.replay)
        record_step(r6)
        if not r6.passed:
            d = self._drop(r6.name, r6.reason, device_id, seq)
            d.trace = trace
            return d

        r7 = gates.gate7_freshness(device_id, fields["uptime_ms"], received_at_ms, self.freshness)
        record_step(r7)
        if not r7.passed:
            d = self._drop(r7.name, r7.reason, device_id, seq)
            d.trace = trace
            return d

        # Resolve any reading held by a *previous* message before judging this one.
        resolved_summary = None
        resolution = self.corroboration.resolve(device_id, fields["scaled_value"])
        if resolution is not None:
            held, confirmed = resolution
            if confirmed:
                record_id = self._commit(held.fields)
                self.trust.reward(device_id)
                resolved_summary = {"seq": held.seq, "outcome": "confirmed", "record_id": record_id}
            else:
                self.trust.penalize(device_id, amount=4)
                self.quarantine.append(Decision(
                    accepted=False, device_id=device_id, seq=held.seq,
                    failed_gate="plausibility_hold", reason="not corroborated by next reading — quarantined",
                    trust_score_after=self.trust.get(device_id).trust_score,
                ))
                resolved_summary = {"seq": held.seq, "outcome": "rejected"}

        r8 = gates.gate8_plausibility(fields["scaled_value"], self._last_value.get(device_id))
        record_step(r8)
        if r8.held:
            self.corroboration.hold(device_id, seq, fields["scaled_value"], fields)
            self._last_value[device_id] = fields["scaled_value"]
            decision = Decision(
                accepted=False, device_id=device_id, seq=seq, failed_gate=r8.name, reason=r8.reason,
                trust_score_after=self.trust.get(device_id).trust_score, resolved_hold=resolved_summary,
                trace=trace,
            )
            return decision
        if not r8.passed:
            decision = self._drop(r8.name, r8.reason, device_id, seq)
            decision.resolved_hold = resolved_summary
            decision.trace = trace
            return decision
        self._last_value[device_id] = fields["scaled_value"]

        record_id = self._commit(fields)
        self.trust.reward(device_id)
        trace.append({"gate": 9, "name": "commit", "passed": True, "reason": f"committed as record #{record_id}"})
        decision = Decision(
            accepted=True, device_id=device_id, seq=seq, failed_gate=None, reason="committed",
            trust_score_after=self.trust.get(device_id).trust_score,
            record_id=record_id, resolved_hold=resolved_summary, trace=trace,
        )
        self.accepted.append(decision)
        return decision

    def flush_ledger(self):
        return self.ledger.flush()
