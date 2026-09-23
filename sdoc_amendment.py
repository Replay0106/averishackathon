"""
sdoc_amendment.py — automatic amendment requests for SI/BL discrepancies.

Three parts, none of which talk to a mail server (sending is simulated: a sent amendment is a
durable outbox record plus an audit-ledger entry):

  decide()          policy: should this case be sent automatically, held for a person, or never sent?
  build_message()   the amendment email addressed to the original sender
  AmendmentOutbox   SQLite store, so a restart can never send the same amendment twice
"""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SEND = "SEND_AUTOMATICALLY"
HOLD = "HOLD_FOR_HUMAN"
NEVER = "NEVER_SEND"
NONE = "NO_AMENDMENT"

FIELD_LABELS = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "container_count": "Container Count",
    "gross_weight_kg": "Gross Weight",
}

# A mismatch on any of the seven compared fields is sent without a person looking first.
AUTO_SEND_FIELDS = tuple(FIELD_LABELS)


def sender_address(sender: Optional[str]) -> Optional[str]:
    """The bare address from a From header, or None when there is no usable one."""
    addr = parseaddr(sender or "")[1].strip()
    return addr if "@" in addr and not addr.startswith("@") and not addr.endswith("@") else None


# Gate 8 ("discrepancy_plausibility") judges only the document mismatch: it holds a mismatch from a trusted sender and
# rejects one from a sender with no trust history. Neither is a finding about the sender's security.
MISMATCH_GATE = "discrepancy_plausibility"


def security_rejection(gateway: Optional[Dict[str, Any]]) -> bool:
    """True when the Trust Gateway rejected the email at a security gate (not merely because of the mismatch)."""
    return bool(gateway) and not gateway.get("accepted") and not gateway.get("held") and gateway.get("failed_gate") != MISMATCH_GATE


def decide(result: Dict[str, Any], gateway: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """Policy for one processed email. `gateway` is the Trust Gateway verdict when one exists
    ({"accepted", "held", "failed_gate"}); a sender the gateway rejected at a security gate is never
    contacted. A hold or rejection caused only by the document mismatch itself (gate 8) does not block,
    because asking the sender to correct the document is the right response to any mismatch."""
    if result.get("category") != "BL_COMPARISON":
        return NONE, "not an SI/BL check"
    status = result.get("status")
    if status == "OK":
        return NONE, "documents match"
    if status == "NEEDS_REVIEW":
        return HOLD, f"needs a person: {(result.get('review_reason') or 'review').replace('_', ' ')}"
    if security_rejection(gateway):
        return NEVER, f"sender rejected by the Trust Gateway at gate '{gateway.get('failed_gate')}'"
    if not sender_address(result.get("sender")):
        return NEVER, "no usable sender address"
    fields = list(result.get("defect_fields") or [])
    if not fields:
        return HOLD, "mismatch without a named field"
    unknown = [f for f in fields if f not in AUTO_SEND_FIELDS]
    if unknown:
        return HOLD, "needs a person: unrecognised field " + ", ".join(unknown)
    return SEND, "mismatch on: " + ", ".join(FIELD_LABELS[f] for f in fields)


def send_block(result: Dict[str, Any], gateway: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Why an amendment must not be emailed for this case, or None when it may be. Applies to automatic
    and to reviewer-triggered sends alike."""
    if security_rejection(gateway):
        return f"sender rejected by the Trust Gateway at gate '{gateway.get('failed_gate')}'"
    if not sender_address(result.get("sender")):
        return "no usable sender address"
    return None


def _fmt(key: str, value: Any) -> str:
    if value is None:
        return "not stated"
    if key == "gross_weight_kg":
        try:
            return f"{float(value):,.0f} kg" if float(value).is_integer() else f"{float(value):,.1f} kg"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def differing_fields(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = {c["key"]: c for c in result.get("comparison", [])}
    return [
        {"key": k, "label": FIELD_LABELS.get(k, k), "si": rows[k]["si"], "bl": rows[k]["bl"]}
        for k in result.get("defect_fields", []) if k in rows
    ]


def build_message(result: Dict[str, Any]) -> Dict[str, Any]:
    fields = differing_fields(result)
    booking = (result.get("meta") or {}).get("booking") or ""
    name = parseaddr(result.get("sender") or "")[0].strip()
    lines = [f"  - {f['label']}: Shipping Instruction {_fmt(f['key'], f['si'])}, draft BL {_fmt(f['key'], f['bl'])}" for f in fields]
    subject = f"Amendment request - draft BL {booking}".strip() + f" ({result['shipment']})"
    body = (
        f"Dear {name or 'Sir/Madam'},\n\n"
        f"Re: {result.get('subject', '')}\n\n"
        "We compared the Shipping Instruction with the draft Bill of Lading and found the following differences:\n\n"
        + "\n".join(lines)
        + "\n\nPlease send a corrected draft BL that matches the Shipping Instruction, or confirm which document is correct.\n\n"
        "Regards,\nNavisAI Document Verification\n"
    )
    return {"recipient": sender_address(result.get("sender")), "subject": subject, "body": body, "fields": fields}


def _review_text(result: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    reason = result.get("review_reason") or "missing_value"
    attachments = result.get("attachments", [])
    if reason == "missing_attachment":
        missing = [n for n, k in (("Shipping Instruction", "si"), ("draft BL", "bl")) if not result.get(k)]
        what = " and the ".join(missing) if missing else "Shipping Instruction / draft BL"
        verb, pronoun = ("was", "it") if len(missing) < 2 else ("were", "them")
        return f"The {what} {verb} not attached to your email. Please resend {pronoun}.", []
    if reason == "wrong_doc_type":
        files = ", ".join(attachments) if attachments else "none"
        return (f"The attached files could not be identified as a Shipping Instruction and a draft BL (files: {files}). "
                "Please resend the correct documents."), []
    if reason == "unreadable":
        bad = [a for a in attachments if any((result.get(k) or {}).get("is_legible") is False and ("_" + k) in a.lower() for k in ("si", "bl"))]
        files = ", ".join(bad or attachments) or "the attached document"
        return f"We could not read {files}. Please resend a legible copy, ideally a text PDF rather than a scan.", []
    blank = [c for c in result.get("comparison", []) if c.get("missing")]
    labels = ", ".join(FIELD_LABELS.get(c["key"], c["key"]) for c in blank) or "one or more fields"
    fields = [{"key": c["key"], "label": FIELD_LABELS.get(c["key"], c["key"]), "si": c.get("si"), "bl": c.get("bl")} for c in blank]
    return f"The following fields are blank or unclear: {labels}. Please confirm the values or send corrected documents.", fields


def build_review_message(result: Dict[str, Any]) -> Dict[str, Any]:
    """Email for a NEEDS_REVIEW case, asking the sender for what is missing or unreadable."""
    text, fields = _review_text(result)
    booking = (result.get("meta") or {}).get("booking") or ""
    name = parseaddr(result.get("sender") or "")[0].strip()
    subject = f"Documents needed - draft BL {booking}".strip() + f" ({result['shipment']})"
    body = (
        f"Dear {name or 'Sir/Madam'},\n\n"
        f"Re: {result.get('subject', '')}\n\n"
        f"{text}\n\n"
        "Regards,\nNavisAI Document Verification\n"
    )
    if not fields:
        fields = [{"key": "review_reason", "label": "Review reason", "si": result.get("review_reason"), "bl": None}]
    return {"recipient": sender_address(result.get("sender")), "subject": subject, "body": body, "fields": fields}


def build_manual_message(result: Dict[str, Any]) -> Dict[str, Any]:
    """Message for a reviewer-triggered send: a difference list for mismatches, a request for what is missing otherwise."""
    return build_message(result) if result.get("status") == "MISMATCH" else build_review_message(result)


def fingerprint(dataset: str, email_id: str, fields: List[Dict[str, Any]]) -> str:
    """One amendment per shipment per document version: the same differences are never sent twice."""
    payload = json.dumps([dataset, email_id, sorted((f["key"], f["si"], f["bl"]) for f in fields)], default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AmendmentOutbox:
    def __init__(self, path: str):
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            test = p.parent / ".write_test"
            test.touch()
            test.unlink()
            self.path = p
        except Exception:
            import tempfile
            self.path = Path(tempfile.gettempdir()) / "navis_cache" / p.name
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS amendments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    dataset TEXT NOT NULL,
                    email_id TEXT NOT NULL,
                    shipment TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    block_index INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_amend_ds ON amendments(dataset, email_id)")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row(r: sqlite3.Row) -> Dict[str, Any]:
        d = dict(r)
        d["fields"] = json.loads(d.pop("fields_json"))
        return d

    def record(self, dataset: str, email_id: str, shipment: str, message: Dict[str, Any], decision: str, reason: str) -> Optional[Dict[str, Any]]:
        """Insert a sent amendment; returns None when this exact amendment was already recorded."""
        now = datetime.utcnow().isoformat() + "Z"
        fp = fingerprint(dataset, email_id, message["fields"])
        with self._lock, self._connect() as con:
            try:
                cur = con.execute(
                    """INSERT INTO amendments(fingerprint,dataset,email_id,shipment,recipient,subject,body,fields_json,decision,reason,status,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (fp, dataset, email_id, shipment, message["recipient"], message["subject"], message["body"],
                     json.dumps(message["fields"], default=str), decision, reason, "sent", now, now),
                )
            except sqlite3.IntegrityError:
                return None
            return self._row(con.execute("SELECT * FROM amendments WHERE id=?", (cur.lastrowid,)).fetchone())

    def attach_block(self, amendment_id: int, block_index: int) -> None:
        with self._lock, self._connect() as con:
            con.execute("UPDATE amendments SET block_index=?, updated_at=? WHERE id=?", (block_index, datetime.utcnow().isoformat() + "Z", amendment_id))

    def discard(self, amendment_id: int) -> None:
        """Remove a row whose audit-ledger entry could not be written, so it is retried later."""
        with self._lock, self._connect() as con:
            con.execute("DELETE FROM amendments WHERE id=?", (amendment_id,))

    def get(self, dataset: str, email_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as con:
            r = con.execute("SELECT * FROM amendments WHERE dataset=? AND email_id=? ORDER BY id DESC LIMIT 1", (dataset, email_id)).fetchone()
        return self._row(r) if r else None

    def all(self, dataset: str) -> Dict[str, Dict[str, Any]]:
        """Latest amendment per email for a dataset."""
        with self._lock, self._connect() as con:
            rows = con.execute("SELECT * FROM amendments WHERE dataset=? ORDER BY id", (dataset,)).fetchall()
        return {r["email_id"]: self._row(r) for r in rows}

    def set_status(self, dataset: str, email_id: str, status: str) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE amendments SET status=?, updated_at=? WHERE id=(SELECT MAX(id) FROM amendments WHERE dataset=? AND email_id=?)",
                (status, datetime.utcnow().isoformat() + "Z", dataset, email_id),
            )
            return cur.rowcount > 0
