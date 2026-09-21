"""
FastAPI routes for NavisAI's real email-intake cybersecurity layer.

This is not a simulated device and not a fabricated sensor — GATEWAY is a
single `sdoc_gateway.EmailGateway` instance that has actually processed
every real email in the demo inbox through the real 10-gate pipeline:
real sender domains, NavisAI's own spoofing heuristics, real
classification/extraction results, and a real Merkle-proof ledger.
`bootstrap_from_dataset` is called once from api/main.py, after its own
pipeline objects exist, to run that real intake.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sdoc_gateway import EmailGateway, KNOWN_GOOD_DOMAINS, SUSPICIOUS_DOMAINS

router = APIRouter(prefix="/api/gateway", tags=["gateway"])

GATEWAY = EmailGateway()
EVENT_LOG: List[Dict[str, Any]] = []
_LOG_SEQ = 0
_BOOTSTRAPPED = False
_DATASET_REF = None
_RUN_EMAIL_REF = None


def _push_log(domain: Optional[str], kind: str, text: str) -> None:
    global _LOG_SEQ
    _LOG_SEQ += 1
    EVENT_LOG.append({"id": _LOG_SEQ, "domain": domain, "kind": kind, "text": text})
    del EVENT_LOG[:-100]


def bootstrap_from_dataset(dataset, run_email_fn) -> None:
    """Run every real email in the demo inbox through the real gate pipeline.
    skip_rate_limit=True because this replays weeks of real historical
    correspondence in milliseconds — that's an artifact of batch-loading an
    archive, not an actual flood, so the rate-limit gates would otherwise
    fire on every high-volume sender for no real reason."""
    global _BOOTSTRAPPED, _DATASET_REF, _RUN_EMAIL_REF
    _DATASET_REF, _RUN_EMAIL_REF = dataset, run_email_fn
    if _BOOTSTRAPPED:
        return
    for eid in dataset.loader.get_email_ids():
        email = dataset.loader.get_email(eid)
        result = run_email_fn(dataset, eid)
        GATEWAY.ingest(eid, email.get("from", ""), email.get("subject", ""), result, skip_rate_limit=True)
    GATEWAY.flush_ledger()
    _push_log(None, "pass", f"bootstrap: {len(GATEWAY.accepted)} emails committed, {len(GATEWAY.quarantine)} quarantined")
    _BOOTSTRAPPED = True


def _tag(domain: str) -> str:
    if domain in KNOWN_GOOD_DOMAINS:
        return "known"
    if domain in SUSPICIOUS_DOMAINS:
        return "suspicious"
    return "unclassified"


def _outcome_kind(d) -> str:
    if d.accepted:
        return "accepted"
    if d.held:
        return "held"
    return "rejected"


def _decision_dict(d) -> Dict[str, Any]:
    return {
        "accepted": d.accepted,
        "email_id": d.email_id,
        "domain": d.domain,
        "failed_gate": d.failed_gate,
        "reason": d.reason,
        "trust_score_after": d.trust_score_after,
        "record_id": d.record_id,
        "resolved_hold": d.resolved_hold,
        "trace": d.trace,
        "outcome": _outcome_kind(d),
    }


def _domain_state(domain: str) -> Dict[str, Any]:
    rec = GATEWAY.trust.get(domain)
    last = GATEWAY.last_email_by_domain.get(domain)
    accepted_here = sum(1 for d in GATEWAY.accepted if d.domain == domain)
    quarantined_here = sum(1 for d in GATEWAY.quarantine if d.domain == domain)
    return {
        "domain": domain,
        "tag": _tag(domain),
        "trust": rec.trust_score if rec else None,
        "revoked": rec.revoked if rec else False,
        "accepted_count": accepted_here,
        "quarantine_count": quarantined_here,
        "last_email_id": last["email_id"] if last else None,
        "last_subject": last["subject"][:70] if last else None,
        "has_last": last is not None,
    }


def _ledger_summary() -> Dict[str, Any]:
    ledger = GATEWAY.ledger
    last_batch = ledger._batches[-1] if ledger._batches else None
    return {
        "total_committed": len(ledger._records),
        "pending_flush": len(ledger._pending),
        "batches": len(ledger._batches),
        "last_root": last_batch["root"] if last_batch else None,
    }


def _state_snapshot() -> Dict[str, Any]:
    domains = sorted(GATEWAY.trust._devices.keys(), key=lambda dom: -(GATEWAY.trust.get(dom).trust_score))
    return {
        "domains": [_domain_state(dom) for dom in domains],
        "ledger": _ledger_summary(),
        "log": EVENT_LOG[-40:],
        "accepted_count": len(GATEWAY.accepted),
        "quarantine_count": len(GATEWAY.quarantine),
        "bootstrapped": _BOOTSTRAPPED,
    }


@router.get("/state")
def get_state():
    return _state_snapshot()


class ResubmitBody(BaseModel):
    domain: str
    mode: str = "replay"  # replay | flood | send_test


@router.post("/resubmit")
def resubmit(body: ResubmitBody):
    if body.mode == "send_test":
        if GATEWAY.trust.get(body.domain) is None:
            raise HTTPException(404, f"unknown domain {body.domain!r}")
        decision = GATEWAY.send_test_email(body.domain)
        _push_log(body.domain, "pass" if decision.accepted else "fail",
                   f"send_test: {decision.email_id} — {decision.reason}")
        return {"decision": _decision_dict(decision), "state": _state_snapshot()}

    last = GATEWAY.last_email_by_domain.get(body.domain)
    if last is None:
        raise HTTPException(400, f"no processed email on file yet for {body.domain!r}")

    if body.mode == "flood":
        # Each iteration gets its own synthetic id (real sender + real
        # content, a "-flood-N" suffix) so gate 6 (duplicate) doesn't mask
        # what this test is actually isolating: the rate limiter. Marked as
        # a drill — it proves the limiter works without leaving the domain
        # looking like it just got attacked 25 times.
        blocked = 0
        decision = None
        for i in range(25):
            synthetic_id = f"{last['email_id']}-flood-{i}"
            decision = GATEWAY.ingest(synthetic_id, last["sender"], last["subject"], last["result"], is_test=True)
            if not decision.accepted:
                blocked += 1
        _push_log(body.domain, "fail", f"flood drill: {blocked}/25 blocked ({decision.failed_gate})")
        return {"decision": _decision_dict(decision), "state": _state_snapshot(), "flood_blocked": blocked}

    # replay: a drill proving gate 6 catches a resend of something already
    # processed — never counted against the domain, since it isn't new abuse.
    decision = GATEWAY.ingest(last["email_id"], last["sender"], last["subject"], last["result"], is_test=True)
    kind = "pass" if decision.accepted else ("hold" if decision.held else "fail")
    _push_log(body.domain, kind, f"replay drill: {decision.failed_gate or 'commit'} — {decision.reason}")
    return {"decision": _decision_dict(decision), "state": _state_snapshot()}


@router.post("/flush")
def flush():
    batch = GATEWAY.flush_ledger()
    if batch:
        _push_log(None, "pass", f"ledger flush: batch #{batch['index']} anchored, {batch['size']} records")
    return {"batch": batch, "state": _state_snapshot()}


@router.get("/ledger")
def ledger(limit: int = 60):
    records = [{"record_id": r.record_id, "payload": r.payload, "batch_index": r.batch_index} for r in GATEWAY.ledger._records]
    return {"records": list(reversed(records))[:limit], "summary": _ledger_summary()}


@router.get("/ledger/verify/{record_id}")
def verify(record_id: int):
    receipt = GATEWAY.ledger.get_receipt(record_id)
    if receipt is None:
        return {"record_id": record_id, "verified": False, "reason": "not flushed into a batch yet"}
    return {
        "record_id": record_id,
        "verified": GATEWAY.ledger.verify_receipt(receipt),
        "root": receipt["root"],
        "leaf_hash": receipt["leaf_hash"],
        "proof": [{"side": side, "hash": h} for side, h in receipt["proof"]],
    }


@router.get("/ledger/verify-tampered/{record_id}")
def verify_tampered(record_id: int):
    receipt = GATEWAY.ledger.get_receipt(record_id)
    if receipt is None:
        return {"record_id": record_id, "verified": False, "reason": "not flushed into a batch yet"}
    forged = dict(receipt)
    forged["leaf_hash"] = "0" * 64
    return {
        "record_id": record_id,
        "verified": GATEWAY.ledger.verify_receipt(forged),
        "root": forged["root"],
        "leaf_hash": forged["leaf_hash"],
        "proof": [{"side": side, "hash": h} for side, h in forged["proof"]],
    }


@router.get("/email-check/{email_id}")
def email_check(email_id: str):
    """Consumed by the Compliance Gate page: what did the real security
    pipeline decide about the email this shipment's documents arrived in?"""
    d = GATEWAY.decisions_by_email_id.get(email_id)
    if d is None:
        return {"checked": False}
    return {"checked": True, **_decision_dict(d)}


@router.post("/reset")
def reset():
    global GATEWAY, EVENT_LOG, _LOG_SEQ, _BOOTSTRAPPED
    GATEWAY = EmailGateway()
    EVENT_LOG = []
    _LOG_SEQ = 0
    _BOOTSTRAPPED = False
    if _DATASET_REF is not None and _RUN_EMAIL_REF is not None:
        bootstrap_from_dataset(_DATASET_REF, _RUN_EMAIL_REF)
    return _state_snapshot()
