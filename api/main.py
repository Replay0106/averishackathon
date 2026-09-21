"""NavisAI API — thin FastAPI layer over the sdoc_* verification pipeline.

Run from the repo root:  uvicorn api.main:app --reload --port 8000
Text extraction is deterministic; scanned PDFs use Gemini vision when a key is configured.
Datasets: the bundled demo inbox ("demo") plus any folders imported through /api/datasets/import.
"""
import dataclasses
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import importer
from api.gateway_routes import bootstrap_from_dataset
from api.gateway_routes import router as gateway_router
from api.gmail_ingest import gmail_router
import sdoc_store
from sdoc_amendment import SEND, build_manual_message, build_message, decide as decide_amendment, send_block
from sdoc_classifier import EmailClassifier, is_draft_request
from sdoc_extractor import FieldExtractor
from sdoc_loader import InboxLoader
from sdoc_reconciler import DocumentReconciler, select_documents
from security_layer.gates import RateLimiter

BUNDLE = ROOT / "sdoc-hackathon-bundle"


def _get_datasets_dir() -> Path:
    d = ROOT / "datasets"
    try:
        d.mkdir(parents=True, exist_ok=True)
        test = d / ".write_test"
        test.touch()
        test.unlink()
        return d
    except Exception:
        tmp_d = Path(tempfile.gettempdir()) / "navis_datasets"
        tmp_d.mkdir(parents=True, exist_ok=True)
        return tmp_d


DATASETS_DIR = _get_datasets_dir()
FIELDS = [
    ("shipper", "Shipper"),
    ("consignee", "Consignee"),
    ("notify_party", "Notify Party"),
    ("port_of_loading", "Port of Loading"),
    ("port_of_discharge", "Port of Discharge"),
    ("container_count", "Container Count"),
    ("gross_weight_kg", "Gross Weight"),
]
HERO_EMAIL, HERO_SHIPMENT = "email_043", "SHP-2048"

app = FastAPI(title="NavisAI API", version="1.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(gateway_router)
app.include_router(gmail_router)

# Whole-system protection: the same rate-limiting primitive the email
# gateway uses per sender domain, applied per client IP in front of every
# API route — the front door gets the same treatment as the front desk.
_api_limiter = RateLimiter(window_s=1.0)
_API_RATE_CAPACITY = 100


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if not _api_limiter.allow(f"api:{client_ip}", _API_RATE_CAPACITY):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
    return await call_next(request)

classifier = EmailClassifier()
extractor = FieldExtractor()
reconciler = DocumentReconciler()
try:
    # Supabase when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set, local files otherwise.
    ledger = sdoc_store.make_ledger(str(ROOT / "audit_ledger.json"))
    outbox = sdoc_store.make_outbox(str(ROOT / ".cache" / "amendments.db"))
except sdoc_store.StoreError as e:
    raise RuntimeError(f"Supabase is configured but its tables are not usable ({e}). Apply supabase/migrations/*.sql to the project.") from e
decisions = sdoc_store.make_decisions()


class Dataset:
    def __init__(self, id: str, name: str, root: Path, kind: str = "import", created: Optional[str] = None, report: Optional[dict] = None, remote: bool = False):
        self.id, self.name, self.root, self.kind = id, name, root, kind
        # remote: the files live in Supabase Storage and are copied into `root` (a local cache) on first use
        self.remote = remote
        self.hydrated = not remote
        self._hydrate_lock = threading.Lock()
        self.created = created or datetime.utcnow().isoformat() + "Z"
        self.report = report or {}
        self.loader = InboxLoader(str(root))
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.overrides: Dict[str, Dict[str, Any]] = {}
        self.timing: Dict[str, float] = {}
        self.job: Dict[str, Any] = {"status": "ready", "done": 0, "total": 0, "failed": []}
        self.amend_lock = threading.Lock()
        self.amend_done = False
        if self.id == "demo":
            self._seed_cache_from_snapshot()

    def _seed_cache_from_snapshot(self) -> None:
        snap_path = ROOT / "web" / "src" / "data" / "snapshot.json"
        if snap_path.is_file():
            try:
                snap = json.loads(snap_path.read_text(encoding="utf-8"))
                details = snap.get("details", {})
                if isinstance(details, dict):
                    self.cache.update(details)
            except Exception:
                pass

    def ensure_local(self) -> None:
        """Copy a stored dataset from Supabase Storage into the local cache once per process."""
        if self.hydrated:
            return
        with self._hydrate_lock:
            if self.hydrated:
                return
            backend = sdoc_store.get_store()
            if backend is not None:
                sdoc_store.hydrate(backend, self.id, self.root)
            self.hydrated = True

    def refresh_decisions(self) -> None:
        self.overrides = decisions.all(self.id)

    def info(self) -> Dict[str, Any]:
        count = len(self.loader.get_email_ids()) if self.hydrated else int(self.report.get("emails", 0))
        return {"id": self.id, "name": self.name, "kind": self.kind, "created": self.created,
                "emails": count, "job": self.job, "report": self.report}


DATASETS: Dict[str, Dataset] = {"demo": Dataset("demo", "SDOC demo inbox", BUNDLE, kind="demo")}


def _load_saved() -> None:
    if DATASETS_DIR.is_dir():
        for d in sorted(DATASETS_DIR.iterdir()):
            meta = d / "meta.json"
            if meta.is_file():
                m = json.loads(meta.read_text(encoding="utf-8"))
                DATASETS[d.name] = Dataset(d.name, m.get("name", d.name), d, created=m.get("created"), report=m.get("report"))
    backend = sdoc_store.get_store()
    if backend is None:
        return
    try:
        rows = sdoc_store.list_dataset_rows(backend)
    except sdoc_store.StoreError as e:
        raise RuntimeError(f"Supabase is configured but the datasets table is not usable ({e}). Apply supabase/migrations/*.sql.") from e
    for r in rows:
        if r["id"] not in DATASETS:
            DATASETS[r["id"]] = Dataset(r["id"], r["name"], DATASETS_DIR / r["id"], kind=r.get("kind", "import"),
                                        created=r.get("created"), report=r.get("report"), remote=True)


_load_saved()


_gmail_reinjected = False


def _reinject_gmail_into_demo() -> None:
    """Simulated and live Gmail emails are shown in the demo inbox as well; that view is rebuilt in memory, so
    after a restart the stored Gmail emails are put back."""
    global _gmail_reinjected
    if _gmail_reinjected or sdoc_store.get_store() is None:
        return
    _gmail_reinjected = True
    demo = DATASETS.get("demo")
    if demo is None:
        return
    for g in [d for d in DATASETS.values() if d.kind == "gmail"]:
        try:
            g.ensure_local()
            g.loader = InboxLoader(str(g.root))
            for eid in reversed(g.loader.get_email_ids()):
                demo.loader.inject_email(eid, g.loader.get_email(eid))
        except Exception:
            continue


def ds_of(ds: str) -> Dataset:
    if ds not in DATASETS:
        raise HTTPException(404, f"unknown dataset {ds!r}")
    d = DATASETS[ds]
    if d.remote:
        d.ensure_local()
    if d.id == "demo":
        _reinject_gmail_into_demo()
    return d


def shipment_id(eid: str, ds: str = "demo") -> str:
    if ds == "demo" and eid == HERO_EMAIL:
        return HERO_SHIPMENT
    digits = re.findall(r"\d+", eid)
    return f"SHP-{2100 + int(digits[-1])}" if digits and len(digits[-1]) <= 6 else f"SHP-{zlib.crc32(eid.encode()) % 9000 + 1000}"


def email_for_shipment(d: Dataset, sid: str) -> Optional[str]:
    sid = sid.upper()
    for eid in d.loader.get_email_ids():
        if shipment_id(eid, d.id) == sid:
            return eid
    return None


def _pick_docs(d: Dataset, email: Dict[str, Any]):
    loaded = [d.loader.load_attachment(a) for a in email.get("attachments", [])]
    si, bl, ambiguous = select_documents(loaded)
    return si, bl, ambiguous


def _fields_dict(f) -> Optional[Dict[str, Any]]:
    if f is None:
        return None
    d = dataclasses.asdict(f)
    return {k: d[k] for k in ("doc_type", "is_scanned", "is_legible", "evidence_spans", "raw_text", *[k for k, _ in FIELDS])}


CARRIERS = {"MEDU": "MSC", "ONEY": "ONE", "HLCU": "Hapag-Lloyd", "EGLV": "Evergreen", "OOLU": "OOCL", "YMJA": "Yang Ming",
            "MAEU": "Maersk", "CMDU": "CMA CGM", "COSU": "COSCO", "ZIMU": "ZIM", "MSDU": "MSC"}


def _meta(text: str) -> Dict[str, Optional[str]]:
    def grab(pat):
        m = re.search(pat, text, re.I)
        return m.group(1).strip() if m else None
    ids = re.findall(r"(?:Booking\s*(?:Ref|No)|B/L\s*No|Bill of Lading No)[^:\n]*:\s*([A-Z0-9]{6,})", text, re.I)
    booking = next((i for i in ids if i[:4].upper() in CARRIERS), ids[0] if ids else None)
    return {
        "booking": booking,
        "vessel": grab(r"(?:Ocean Vessel|Vessel Name|Vessel)[^:\n]*:\s*([^\n]+)"),
        "oc_no": grab(r"OC No\.?[^:\n]*:\s*([A-Z0-9-]+)"),
        "carrier": CARRIERS.get((booking or "")[:4].upper(), "Other / NVOCC") if booking else "Unassigned",
    }


def _confidence(status: str, reason: Optional[str], si, bl) -> float:
    spans = [f.evidence_spans for f in (si, bl) if f is not None]
    vals = [s[k]["confidence"] for s in spans for k, _ in FIELDS if k in s and "confidence" in s[k]]
    base = sum(vals) / len(vals) if vals else 0.0
    if status == "NEEDS_REVIEW":
        cap = {"missing_attachment": 0.0, "wrong_doc_type": 0.35, "unreadable": 0.22, "missing_value": 0.58}.get(reason or "", 0.5)
        return round(min(base, cap), 2)
    return round(base, 2)


def _blank(d: Dataset, eid: str, email: Dict[str, Any], category: str) -> Dict[str, Any]:
    return {
        "id": eid,
        "shipment": shipment_id(eid, d.id),
        "sender": email.get("from", ""),
        "subject": email.get("subject", ""),
        "body": email.get("body", ""),
        "attachments": [Path(a).name for a in email.get("attachments", [])],
        "category": category,
        "status": "OK",
        "review_reason": None,
        "defect_fields": [],
        "confidence": 0.99,
        "si": None,
        "bl": None,
        "comparison": [],
        "meta": {"booking": None, "vessel": None, "oc_no": None, "carrier": "n/a"},
    }


def run_email(d: Dataset, eid: str) -> Dict[str, Any]:
    if eid in d.cache:
        return d.cache[eid]
    t0 = time.perf_counter()
    email = d.loader.get_email(eid)
    category = classifier.classify_email(email)
    result = _blank(d, eid, email, category)
    if category == "BL_COMPARISON":
        try:
            si, bl, ambiguous = _pick_docs(d, email)
            si_f = extractor.extract(si) if si else None
            bl_f = extractor.extract(bl) if bl else None
            entry = reconciler.reconcile(email_id=eid, category=category, si_att=si, bl_att=bl, si_fields=si_f, bl_fields=bl_f,
                                         draft_request=is_draft_request(email), ambiguous_documents=ambiguous)
            result.update(status=entry["status"], review_reason=entry["review_reason"], defect_fields=entry["defect_fields"])
            result["si"], result["bl"] = _fields_dict(si_f), _fields_dict(bl_f)
            result["confidence"] = _confidence(entry["status"], entry["review_reason"], si_f, bl_f)
            result["meta"] = _meta((bl.text if bl else "") + "\n" + (si.text if si else ""))
            for key, label in FIELDS:
                sv = getattr(si_f, key, None) if si_f else None
                bv = getattr(bl_f, key, None) if bl_f else None
                result["comparison"].append({
                    "key": key, "label": label, "si": sv, "bl": bv,
                    "match": key not in entry["defect_fields"] and sv is not None and bv is not None,
                    "missing": sv is None or bv is None,
                    "si_evidence": (si_f.evidence_spans.get(key) if si_f else None),
                    "bl_evidence": (bl_f.evidence_spans.get(key) if bl_f else None),
                })
        except Exception as e:  # visible failure, never silent
            result.update(status="NEEDS_REVIEW", review_reason="unreadable", confidence=0.0, error=f"{type(e).__name__}: {e}")
        d.timing[eid] = (time.perf_counter() - t0) * 1000
    d.cache[eid] = result
    return result


def _ledger_ref(d: Dataset, eid: str) -> str:
    return eid if d.id == "demo" else f"{d.id}:{eid}"


def _gateway_verdict(d: Dataset, eid: str) -> Optional[Dict[str, Any]]:
    """The Trust Gateway's decision for an email; only the demo inbox is run through the gateway."""
    if d.kind != "demo":
        return None
    try:
        from api import gateway_routes as gr
        dec = gr.GATEWAY.decisions_by_email_id.get(eid)
    except Exception:
        return None
    return None if dec is None else {"accepted": dec.accepted, "held": dec.held, "failed_gate": dec.failed_gate}


def ensure_amendments(d: Dataset) -> None:
    """Send (record) the automatic amendments for a dataset once. The outbox is durable and keyed by
    the exact differences, so a restart or a repeat call never sends the same amendment twice."""
    if d.amend_done:
        return
    with d.amend_lock:
        if d.amend_done:
            return
        for eid in d.loader.get_email_ids():
            r = run_email(d, eid)
            if r["category"] != "BL_COMPARISON" or r["status"] != "MISMATCH":
                continue
            verdict = _gateway_verdict(d, eid)
            decision, reason = decide_amendment(r, verdict)
            if decision != SEND:
                continue
            msg = build_message(r)
            row = outbox.record(d.id, eid, r["shipment"], msg, decision, reason)
            if row is None:
                continue
            try:
                block = ledger.record_action(
                    "NavisAI Auto-Amend", _ledger_ref(d, eid), "AMENDMENT_DISPATCHED",
                    {"auto": True, "recipient": msg["recipient"], "subject": msg["subject"],
                     "fields": [{"key": f["key"], "si": f["si"], "bl": f["bl"]} for f in msg["fields"]],
                     "policy": decision, "reason": reason, "gateway": verdict, "shipment": r["shipment"]},
                )
            except Exception:
                outbox.discard(row["id"])
                raise
            outbox.attach_block(row["id"], block["index"])
        d.amend_done = True


def _amendment_view(a: Optional[Dict[str, Any]], full: bool = False) -> Optional[Dict[str, Any]]:
    if not a:
        return None
    v = {"status": a["status"], "at": a["created_at"], "recipient": a["recipient"], "subject": a["subject"], "block": a["block_index"], "auto": a["decision"] == SEND}
    if full:
        v |= {"body": a["body"], "fields": a["fields"], "reason": a["reason"]}
    return v


def _resolution(d: Dataset, eid: str, amends: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ov = d.overrides.get(eid)
    if ov:
        return ov
    a = amends.get(eid)
    if not a:
        return None
    action = "AMENDMENT_CONFIRMED" if a["status"] == "confirmed" else "AMENDMENT_DISPATCHED"
    return {"action": action, "at": a["updated_at"], "block": a["block_index"], "auto": a["decision"] == SEND, "recipient": a["recipient"]}


def _case_info(d: Dataset, r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if r["category"] != "BL_COMPARISON" or r["status"] == "OK":
        return None
    verdict = _gateway_verdict(d, r["id"])
    decision, reason = decide_amendment(r, verdict)
    blocked = send_block(r, verdict)
    return {"decision": decision, "reason": reason, "can_send": blocked is None, "block_reason": blocked}


def _apply_override(d: Dataset, r: Dict[str, Any]) -> Dict[str, Any]:
    amends = {r["id"]: a} if (a := outbox.get(d.id, r["id"])) else {}
    res = _resolution(d, r["id"], amends)
    out = {**r, "amendment": _amendment_view(amends.get(r["id"]), full=True), "case": _case_info(d, r)}
    return {**out, "resolution": res} if res else out


def _summary_row(d: Dataset, r: Dict[str, Any], amends: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    amends = amends if amends is not None else {}
    keys = ("id", "shipment", "sender", "subject", "category", "status", "review_reason", "defect_fields", "confidence", "attachments", "meta")
    return {k: r[k] for k in keys} | {
        "resolution": _resolution(d, r["id"], amends),
        "amendment": _amendment_view(amends.get(r["id"])),
        "case": _case_info(d, r),
    }


@app.get("/api")
def root():
    import os
    d = DATASETS["demo"]
    return {
        "status": "operational",
        "service": "NavisAI Verification Engine API",
        "version": "1.1",
        "environment": "serverless" if "VERCEL" in os.environ else "standard",
        "emails": len(d.loader.get_email_ids()),
        "endpoints": {
            "health": "/api/health",
            "emails": "/api/emails",
            "summary": "/api/summary",
            "audit": "/api/audit",
            "copilot": "/api/copilot",
            "gateway_state": "/api/gateway/state",
            "gateway_ledger": "/api/gateway/ledger",
            "datasets": "/api/datasets",
        },
    }


def _storage_health() -> Dict[str, Any]:
    backend = sdoc_store.get_store()
    if backend is None:
        return {"backend": "local", "persistent": False}
    try:
        backend.select(sdoc_store.T_DATASETS, limit=1)
        return {"backend": "supabase", "persistent": True, "ok": True}
    except Exception as e:  # noqa: BLE001
        return {"backend": "supabase", "persistent": True, "ok": False, "error": str(e)[:200]}


@app.get("/api/storage/status")
def storage_status():
    """Tells the web app how uploads should work: straight to Supabase Storage, or through this API."""
    backend = sdoc_store.get_store()
    return {"backend": "supabase" if backend is not None else "local", "direct_upload": backend is not None,
            "max_part_bytes": sdoc_store.PACK_LIMIT_BYTES, "max_total_bytes": importer.MAX_TOTAL_BYTES}


@app.get("/api/health")
def health():
    d = DATASETS["demo"]
    return {"status": "operational", "emails": len(d.loader.get_email_ids()), "datasets": len(DATASETS),
            "engine": "deterministic-text + gemini-vision", "ledger_ok": ledger.verify_integrity()[0],
            "storage": _storage_health()}


@app.get("/api/emails")
def emails(ds: str = "demo"):
    d = ds_of(ds)
    d.refresh_decisions()
    ensure_amendments(d)
    amends = outbox.all(d.id)
    return [_summary_row(d, run_email(d, e), amends) for e in d.loader.get_email_ids()]


@app.get("/api/emails/{eid}")
def email_detail(eid: str, ds: str = "demo"):
    d = ds_of(ds)
    if eid not in d.loader.get_email_ids():
        raise HTTPException(404, "email not found")
    d.refresh_decisions()
    ensure_amendments(d)
    return _apply_override(d, run_email(d, eid))


@app.get("/api/summary")
def summary(ds: str = "demo"):
    d = ds_of(ds)
    rows = [run_email(d, e) for e in d.loader.get_email_ids()]
    cats: Dict[str, int] = {}
    fields: Dict[str, int] = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
        for f in r["defect_fields"]:
            fields[f] = fields.get(f, 0) + 1
    comps = [r for r in rows if r["category"] == "BL_COMPARISON"]
    return {
        "total": len(rows),
        "categories": cats,
        "comparisons": len(comps),
        "ok": sum(r["status"] == "OK" for r in comps),
        "mismatch": sum(r["status"] == "MISMATCH" for r in comps),
        "needs_review": sum(r["status"] == "NEEDS_REVIEW" for r in comps),
        "defect_fields": fields,
        "avg_verify_ms": round(sum(d.timing.values()) / max(len(d.timing), 1), 1),
    }


class Action(BaseModel):
    actor: str = "Operations Desk"
    action: str
    details: Dict[str, Any] = {}


@app.get("/api/audit")
def audit():
    ok, msg = ledger.verify_integrity()
    return {"integrity": ok, "message": msg, "blocks": list(reversed(ledger.blocks))[:200]}


class SendAmendment(BaseModel):
    actor: str = "Operations Desk"


@app.post("/api/emails/{eid}/send-amendment")
def send_amendment(eid: str, body: SendAmendment, ds: str = "demo"):
    """A reviewer sends the amendment (or request for missing documents) for a case that was held for a person.
    Recorded like an automatic send, but the ledger entry names the reviewer as the approver."""
    d = ds_of(ds)
    if eid not in d.loader.get_email_ids():
        raise HTTPException(404, "email not found")
    ensure_amendments(d)
    r = run_email(d, eid)
    if r["category"] != "BL_COMPARISON" or r["status"] == "OK":
        raise HTTPException(400, "nothing to amend: the documents match or this is not an SI/BL check")
    if outbox.get(d.id, eid):
        raise HTTPException(409, "an amendment was already sent for this case")
    verdict = _gateway_verdict(d, eid)
    blocked = send_block(r, verdict)
    if blocked:
        raise HTTPException(403, f"cannot send: {blocked}")
    msg = build_manual_message(r)
    reason = f"sent by {body.actor}"
    row = outbox.record(d.id, eid, r["shipment"], msg, "MANUAL_SEND", reason)
    if row is None:
        raise HTTPException(409, "an amendment was already sent for this case")
    try:
        block = ledger.record_action(
            body.actor, _ledger_ref(d, eid), "AMENDMENT_DISPATCHED",
            {"auto": False, "approved_by": body.actor, "recipient": msg["recipient"], "subject": msg["subject"],
             "fields": [{"key": f["key"], "si": f["si"], "bl": f["bl"]} for f in msg["fields"]],
             "policy": "MANUAL_SEND", "reason": reason, "case_status": r["status"], "review_reason": r["review_reason"],
             "gateway": verdict, "shipment": r["shipment"]},
        )
    except Exception:
        outbox.discard(row["id"])
        raise
    outbox.attach_block(row["id"], block["index"])
    row = outbox.get(d.id, eid)
    return {"block": block, "amendment": _amendment_view(row, full=True), "resolution": _resolution(d, eid, {eid: row})}


@app.post("/api/emails/{eid}/action")
def record(eid: str, body: Action, ds: str = "demo"):
    d = ds_of(ds)
    if eid not in d.loader.get_email_ids():
        raise HTTPException(404, "email not found")
    ref = _ledger_ref(d, eid)
    block = ledger.record_action(body.actor, ref, body.action, body.details)
    if body.action == "AMENDMENT_CONFIRMED":
        outbox.set_status(d.id, eid, "confirmed")
    if body.action in ("CONFIRM_AI_RESULT", "OVERRIDE_RESULT", "AMENDMENT_DISPATCHED", "AMENDMENT_CONFIRMED"):
        value = {"action": body.action, "at": datetime.utcnow().isoformat() + "Z", "block": block["index"], **body.details}
        d.overrides[eid] = value
        decisions.set(d.id, eid, value)
    return {"block": block}


def _find_target(d: Dataset, q: str) -> Optional[str]:
    m = re.search(r"SHP-\d{4}", q, re.I)
    if m:
        return email_for_shipment(d, m.group(0))
    m = re.search(r"email[_ ]?(\d{3})", q, re.I)
    if m and f"email_{m.group(1)}" in d.loader.get_email_ids():
        return f"email_{m.group(1)}"
    return None


@app.get("/api/copilot")
def copilot(q: str, ds: str = "demo"):
    d = ds_of(ds)
    eid = _find_target(d, q)
    if not eid:
        return {"kind": "help", "message": "Ask about a shipment, e.g. 'Why is SHP-2048 flagged?'"}
    r = run_email(d, eid)
    if r["category"] != "BL_COMPARISON":
        return {"kind": "not_comparison", "shipment": r["shipment"], "category": r["category"], "message": f"{r['shipment']} is classified {r['category']}; no SI/BL comparison applies."}
    bad = [c for c in r["comparison"] if not c["match"] and not c["missing"]]
    if r["status"] == "NEEDS_REVIEW":
        rec = {
            "missing_attachment": "Request the missing document from the sender.",
            "wrong_doc_type": "Ask the sender to resend the correct SI and draft BL.",
            "unreadable": "Request a legible re-upload or clean PDF.",
            "missing_value": "Confirm the missing field with the shipper before verification.",
        }.get(r["review_reason"], "Escalate to a human reviewer.")
        return {"kind": "review", "shipment": r["shipment"], "email": eid, "reason": r["review_reason"], "confidence": r["confidence"], "recommendation": rec}
    if not bad:
        return {"kind": "clear", "shipment": r["shipment"], "email": eid, "message": "No mismatch detected. All seven fields match between SI and draft BL."}
    return {"kind": "mismatch", "shipment": r["shipment"], "email": eid, "issues": bad, "evidence": r["attachments"], "recommendation": "Request a corrected draft BL from the sender."}


# ---------------------------------------------------------------- dataset import
def _process(d: Dataset) -> None:
    ids = d.loader.get_email_ids()
    d.job.update(status="processing", done=0, total=len(ids), failed=[])
    for eid in ids:
        r = run_email(d, eid)
        if r.get("error"):
            d.job["failed"].append({"id": eid, "error": r["error"]})
        d.job["done"] += 1
    ensure_amendments(d)
    d.job["status"] = "ready"


def _persist_dataset(d: Dataset) -> None:
    """Keep a newly imported dataset in Supabase (documents as zip packs in the bucket, metadata in a table)."""
    backend = sdoc_store.get_store()
    if backend is None:
        return
    packs = sdoc_store.save_pack(backend, d.id, d.root)
    d.report = {**d.report, "packs": packs}
    sdoc_store.save_dataset_row(backend, d.id, d.name, d.kind, d.created, d.report)


class UploadPlan(BaseModel):
    name: str = ""
    parts: int = 1


@app.post("/api/datasets/uploads")
def plan_uploads(plan: UploadPlan):
    """Step 1 of a browser-direct import: hand out signed URLs so the browser can upload zip parts of the picked
    folder straight to Supabase Storage. (Vercel functions reject request bodies above about 4.5 MB.)"""
    backend = sdoc_store.get_store()
    if backend is None:
        raise HTTPException(400, "Direct upload needs Supabase; use POST /api/datasets/import instead")
    max_parts = importer.MAX_TOTAL_BYTES // sdoc_store.PACK_LIMIT_BYTES + 2
    if not 1 <= plan.parts <= max_parts:
        raise HTTPException(400, f"parts must be between 1 and {max_parts}")
    ds_id = f"imp_{uuid.uuid4().hex[:8]}"
    uploads = []
    for i in range(1, plan.parts + 1):
        path = f"{ds_id}/raw/part-{i:04d}.zip"
        uploads.append({"part": i, **backend.signed_upload(path)})
    return {"dataset_id": ds_id, "uploads": uploads}


class FinalizeUpload(BaseModel):
    name: str = ""
    parts: int = 1


def _extract_raw_parts(parts: List[bytes], staging: Path) -> None:
    """Unzip uploaded parts into staging with the same path rules as the multipart import."""
    import io
    import zipfile

    archives = [zipfile.ZipFile(io.BytesIO(b)) for b in parts]
    entries = [(a, i) for a in archives for i in a.infolist() if not i.is_dir()]
    if len(entries) > importer.MAX_FILES:
        raise HTTPException(413, f"Too many files (max {importer.MAX_FILES})")
    if sum(i.file_size for _, i in entries) > importer.MAX_TOTAL_BYTES:
        raise HTTPException(413, "Upload exceeds 250 MB")
    try:
        safe = importer.strip_common_root([importer.safe_relpath(i.filename) for _, i in entries])
    except importer.ImportError_ as e:
        raise HTTPException(400, str(e))
    for (arc, info), p in zip(entries, safe):
        if p.suffix.lower() not in importer.ALLOWED_EXT:
            continue
        target = staging / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(arc.read(info))


@app.post("/api/datasets/{ds_id}/finalize")
def finalize_upload(ds_id: str, body: FinalizeUpload):
    """Step 2 of a browser-direct import: read the uploaded parts from Supabase Storage, normalise them exactly like
    the multipart import, and store the result as the dataset."""
    backend = sdoc_store.get_store()
    if backend is None:
        raise HTTPException(400, "Direct upload needs Supabase")
    if not re.fullmatch(r"imp_[0-9a-f]{8}", ds_id) or ds_id in DATASETS:
        raise HTTPException(400, "invalid or already used dataset id")
    raw_paths = [f"{ds_id}/raw/part-{i:04d}.zip" for i in range(1, body.parts + 1)]
    parts = []
    for path in raw_paths:
        data = backend.get_object(path)
        if data is None:
            raise HTTPException(400, f"upload part missing: {path}")
        parts.append(data)
    staging = Path(tempfile.mkdtemp(prefix="navis_import_"))
    dest = DATASETS_DIR / ds_id
    try:
        _extract_raw_parts(parts, staging)
        try:
            report = importer.normalise(staging, dest)
        except importer.ImportError_ as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(422, str(e))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    label = body.name.strip() or f"Import {datetime.utcnow():%Y-%m-%d %H:%M}"
    d = Dataset(ds_id, label, dest, report=report)
    try:
        (dest / "meta.json").write_text(json.dumps({"name": label, "created": d.created, "report": report}), encoding="utf-8")
    except OSError:
        pass
    _persist_dataset(d)
    backend.delete_objects(raw_paths)
    DATASETS[ds_id] = d
    if "VERCEL" in os.environ:
        d.job.update(status="ready", done=0, total=report["emails"])  # a serverless instance is frozen after it responds
    else:
        d.job.update(status="processing", done=0, total=report["emails"])
        threading.Thread(target=_process, args=(d,), daemon=True).start()
    return d.info()


@app.get("/api/datasets")
def list_datasets():
    return [d.info() for d in DATASETS.values()]


@app.get("/api/datasets/{ds}")
def get_dataset(ds: str):
    return ds_of(ds).info()


@app.post("/api/datasets/import")
async def import_dataset(request: Request):
    form = await request.form(max_files=importer.MAX_FILES + 10, max_fields=importer.MAX_FILES + 10)
    name = str(form.get("name") or "")
    files = form.getlist("files")
    try:
        rel = json.loads(str(form.get("paths") or "[]"))
    except Exception:
        raise HTTPException(400, "paths must be a JSON list")
    if len(rel) != len(files):
        raise HTTPException(400, "paths and files must be the same length")
    if len(files) > importer.MAX_FILES:
        raise HTTPException(413, f"Too many files (max {importer.MAX_FILES})")
    try:
        safe = importer.strip_common_root([importer.safe_relpath(p) for p in rel])
    except importer.ImportError_ as e:
        raise HTTPException(400, str(e))

    ds_id = f"imp_{uuid.uuid4().hex[:8]}"
    staging = Path(tempfile.mkdtemp(prefix="navis_import_"))
    total = 0
    try:
        for up, p in zip(files, safe):
            if p.suffix.lower() not in importer.ALLOWED_EXT:
                continue
            data = await up.read()
            total += len(data)
            if total > importer.MAX_TOTAL_BYTES:
                raise HTTPException(413, "Upload exceeds 250 MB")
            target = staging / p
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        dest = DATASETS_DIR / ds_id
        try:
            report = importer.normalise(staging, dest)
        except importer.ImportError_ as e:
            shutil.rmtree(dest, ignore_errors=True)
            raise HTTPException(422, str(e))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    label = name.strip() or f"Import {datetime.utcnow():%Y-%m-%d %H:%M}"
    d = Dataset(ds_id, label, dest, report=report)
    (dest / "meta.json").write_text(json.dumps({"name": label, "created": d.created, "report": report}), encoding="utf-8")
    _persist_dataset(d)
    DATASETS[ds_id] = d
    d.job.update(status="processing", done=0, total=report["emails"])
    threading.Thread(target=_process, args=(d,), daemon=True).start()
    return d.info()


@app.delete("/api/datasets/{ds}")
def delete_dataset(ds: str):
    d = ds_of(ds)
    if d.kind == "demo":
        raise HTTPException(400, "The demo dataset cannot be deleted")
    DATASETS.pop(ds, None)
    shutil.rmtree(d.root, ignore_errors=True)
    backend = sdoc_store.get_store()
    if backend is not None:
        sdoc_store.delete_dataset_everywhere(backend, ds)
    else:
        decisions.clear(ds)
    return {"deleted": ds}


# Run the real demo inbox through the real email-intake gateway once, at
# import time — by the time the app serves its first request, the trust
# scores and audit ledger already reflect the actual 520-email inbox.
bootstrap_from_dataset(DATASETS["demo"], run_email)

# ---------------------------------------------------------------------------
# Frontend SPA & Static Files Serving
# Enables serving the compiled React frontend directly from FastAPI, both
# locally (via uvicorn) and when deployed serverless on Vercel.
# ---------------------------------------------------------------------------
DIST_DIR = ROOT / "web" / "dist"
ASSETS_DIR = DIST_DIR / "assets"


@app.get("/assets/{asset_name:path}")
async def serve_asset(asset_name: str):
    target = ASSETS_DIR / asset_name
    if target.is_file():
        return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    # Graceful fallback for browsers with stale tabs requesting older bundle chunks
    if asset_name.startswith("index-") and asset_name.endswith(".js"):
        for js_file in sorted(ASSETS_DIR.glob("index-*.js")):
            return FileResponse(js_file, media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    if asset_name.startswith("index-") and asset_name.endswith(".css"):
        for css_file in sorted(ASSETS_DIR.glob("index-*.css")):
            return FileResponse(css_file, media_type="text/css", headers={"Cache-Control": "no-cache"})

    raise HTTPException(404, "Asset not found")


@app.get("/{full_path:path}")
async def serve_spa_app(full_path: str):
    if full_path.startswith("api") or full_path in ("docs", "openapi.json", "redoc"):
        raise HTTPException(404, "Not Found")

    target = DIST_DIR / full_path
    if full_path and target.is_file():
        return FileResponse(target)

    index_html = DIST_DIR / "index.html"
    if index_html.is_file():
        return FileResponse(
            index_html,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    return {
        "status": "operational",
        "service": "NavisAI Verification Engine",
        "error": "Frontend build not found. Run 'npm run build' in web/.",
    }

