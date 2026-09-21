"""NavisAI API — thin FastAPI layer over the sdoc_* verification pipeline.

Run from the repo root:  uvicorn api.main:app --reload --port 8000
Text extraction is deterministic; scanned PDFs use Gemini vision when a key is configured.
Datasets: the bundled demo inbox ("demo") plus any folders imported through /api/datasets/import.
"""
import dataclasses
import atexit
import hashlib
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
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api import importer
from api.gmail_service import GmailService, GmailServiceError, SESSION_COOKIE
from sdoc_classifier import EmailClassifier, is_draft_request
from sdoc_extractor import FieldExtractor
from sdoc_loader import InboxLoader
from sdoc_reconciler import DocumentReconciler, select_documents
from sdoc_security import TamperEvidentAuditLedger

BUNDLE = ROOT / "sdoc-hackathon-bundle"
DATASETS_DIR = ROOT / "datasets"
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
cors_origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

classifier = EmailClassifier()
extractor = FieldExtractor()
reconciler = DocumentReconciler()
ledger = TamperEvidentAuditLedger(str(ROOT / "audit_ledger.json"))
gmail = GmailService()


class Dataset:
    def __init__(self, id: str, name: str, root: Path, kind: str = "import", created: Optional[str] = None,
                 report: Optional[dict] = None, owner_id: Optional[str] = None):
        self.id, self.name, self.root, self.kind = id, name, root, kind
        self.owner_id = owner_id
        self.created = created or datetime.utcnow().isoformat() + "Z"
        self.report = report or {}
        self.loader = InboxLoader(str(root))
        self.email_ids = self.loader.get_email_ids()
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.overrides: Dict[str, Dict[str, Any]] = {}
        self.timing: Dict[str, float] = {}
        self.job: Dict[str, Any] = {"status": "ready", "done": 0, "total": 0, "failed": []}

    def info(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "created": self.created,
                "emails": len(self.email_ids), "job": self.job, "report": self.report}


DATASETS: Dict[str, Dataset] = {"demo": Dataset("demo", "SDOC demo inbox", BUNDLE, kind="demo")}
GMAIL_RUNTIME: Dict[str, str] = {}


def _drop_gmail_runtime(user_id: str) -> None:
    dataset_id = GMAIL_RUNTIME.pop(user_id, None)
    dataset = DATASETS.pop(dataset_id, None) if dataset_id else None
    if dataset:
        shutil.rmtree(dataset.root, ignore_errors=True)


@atexit.register
def _cleanup_gmail_runtime() -> None:
    for user_id in list(GMAIL_RUNTIME):
        _drop_gmail_runtime(user_id)


def _load_saved() -> None:
    if not DATASETS_DIR.is_dir():
        return
    for d in sorted(DATASETS_DIR.iterdir()):
        meta = d / "meta.json"
        if meta.is_file():
            m = json.loads(meta.read_text(encoding="utf-8"))
            DATASETS[d.name] = Dataset(d.name, m.get("name", d.name), d, kind=m.get("kind", "import"),
                                       created=m.get("created"), report=m.get("report"))


_load_saved()


def _session_user(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    session = gmail.session(request.cookies.get(SESSION_COOKIE))
    return session["user_id"] if session else None


def ds_of(ds: str, request: Optional[Request] = None) -> Dataset:
    if ds not in DATASETS:
        raise HTTPException(404, f"unknown dataset {ds!r}")
    dataset = DATASETS[ds]
    if dataset.owner_id and dataset.owner_id != _session_user(request):
        # Do not disclose whether another user's private dataset exists.
        raise HTTPException(404, f"unknown dataset {ds!r}")
    return dataset


def shipment_id(eid: str, ds: str = "demo") -> str:
    if ds == "demo" and eid == HERO_EMAIL:
        return HERO_SHIPMENT
    digits = re.findall(r"\d+", eid)
    return f"SHP-{2100 + int(digits[-1])}" if digits and len(digits[-1]) <= 6 else f"SHP-{zlib.crc32(eid.encode()) % 9000 + 1000}"


def email_for_shipment(d: Dataset, sid: str) -> Optional[str]:
    sid = sid.upper()
    for eid in d.email_ids:
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


def _apply_override(d: Dataset, r: Dict[str, Any]) -> Dict[str, Any]:
    ov = d.overrides.get(r["id"])
    return {**r, "resolution": ov} if ov else r


def _summary_row(d: Dataset, r: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("id", "shipment", "sender", "subject", "category", "status", "review_reason", "defect_fields", "confidence", "attachments", "meta")
    return {k: r[k] for k in keys} | {"resolution": d.overrides.get(r["id"])}


@app.get("/api/health")
def health():
    d = DATASETS["demo"]
    return {"status": "operational", "emails": len(d.email_ids), "datasets": len(DATASETS),
            "engine": "deterministic-text + gemini-vision", "ledger_ok": ledger.verify_integrity()[0]}


@app.get("/api/emails")
def emails(request: Request, ds: str = "demo"):
    d = ds_of(ds, request)
    return [_summary_row(d, run_email(d, e)) for e in d.email_ids]


@app.get("/api/emails/{eid}")
def email_detail(eid: str, request: Request, ds: str = "demo"):
    d = ds_of(ds, request)
    if eid not in d.email_ids:
        raise HTTPException(404, "email not found")
    return _apply_override(d, run_email(d, eid))


@app.get("/api/summary")
def summary(request: Request, ds: str = "demo"):
    d = ds_of(ds, request)
    rows = [run_email(d, e) for e in d.email_ids]
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
def audit(request: Request):
    ok, msg = ledger.verify_integrity()
    user_id = _session_user(request)
    allowed_private = {d.id for d in DATASETS.values() if d.owner_id == user_id}
    visible = [
        block for block in ledger.blocks
        if not str(block.get("email_id", "")).startswith("gmail_")
        or str(block.get("email_id", "")).split(":", 1)[0] in allowed_private
    ]
    return {"integrity": ok, "message": msg, "blocks": list(reversed(visible))[:200]}


@app.post("/api/emails/{eid}/action")
def record(eid: str, body: Action, request: Request, ds: str = "demo"):
    d = ds_of(ds, request)
    if eid not in d.email_ids:
        raise HTTPException(404, "email not found")
    ref = eid if d.id == "demo" else f"{d.id}:{eid}"
    try:
        session = gmail.require_csrf(
            request.cookies.get(SESSION_COOKIE), request.headers.get("X-CSRF-Token")
        ) if d.owner_id else None
    except GmailServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    actor = session["email"] if session else body.actor
    block = ledger.record_action(actor, ref, body.action, body.details)
    if body.action in ("CONFIRM_AI_RESULT", "OVERRIDE_RESULT", "AMENDMENT_DISPATCHED", "AMENDMENT_CONFIRMED"):
        d.overrides[eid] = {"action": body.action, "at": datetime.utcnow().isoformat() + "Z", "block": block["index"], **body.details}
    return {"block": block}


def _find_target(d: Dataset, q: str) -> Optional[str]:
    m = re.search(r"SHP-\d{4}", q, re.I)
    if m:
        return email_for_shipment(d, m.group(0))
    m = re.search(r"email[_ ]?(\d{3})", q, re.I)
    if m and f"email_{m.group(1)}" in d.email_ids:
        return f"email_{m.group(1)}"
    return None


@app.get("/api/copilot")
def copilot(request: Request, q: str, ds: str = "demo"):
    d = ds_of(ds, request)
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
    return {"kind": "mismatch", "shipment": r["shipment"], "email": eid, "issues": bad, "evidence": r["attachments"], "recommendation": "Request corrected draft BL from carrier."}


# ---------------------------------------------------------------- dataset import
def _process(d: Dataset) -> None:
    ids = d.email_ids
    d.job.update(status="processing", done=0, total=len(ids), failed=[])
    for eid in ids:
        try:
            r = run_email(d, eid)
            if r.get("error"):
                d.job["failed"].append({"id": eid, "error": r["error"]})
        except Exception as exc:
            d.job["failed"].append({"id": eid, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            d.job["done"] += 1
    d.job["status"] = "ready"
    if d.kind == "gmail":
        # The encrypted SQLite cache is the persistent copy. Remove decrypted
        # working files as soon as the existing pipeline has populated memory.
        shutil.rmtree(d.root, ignore_errors=True)


# ---------------------------------------------------------------- Gmail OAuth + sync
def _gmail_error(exc: GmailServiceError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


@app.get("/api/gmail/status")
def gmail_status(request: Request):
    status = gmail.status(request.cookies.get(SESSION_COOKIE))
    session = gmail.session(request.cookies.get(SESSION_COOKIE))
    status["dataset_id"] = GMAIL_RUNTIME.get(session["user_id"]) if session else None
    return status


@app.get("/api/gmail/connect")
def gmail_connect():
    try:
        return RedirectResponse(gmail.begin_oauth(), status_code=302)
    except GmailServiceError as exc:
        raise _gmail_error(exc)


@app.get("/api/gmail/callback")
def gmail_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(400, f"Google authorization was not completed: {error}")
    try:
        result = gmail.complete_oauth(code, state)
    except GmailServiceError as exc:
        raise _gmail_error(exc)
    response = RedirectResponse(gmail.frontend_url, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        result.session_token,
        max_age=7 * 86400,
        httponly=True,
        secure=gmail.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/gmail/sync")
def gmail_sync(request: Request):
    try:
        session = gmail.require_csrf(
            request.cookies.get(SESSION_COOKIE), request.headers.get("X-CSRF-Token")
        )
        active_id = GMAIL_RUNTIME.get(session["user_id"])
        if active_id and DATASETS.get(active_id) and DATASETS[active_id].job["status"] == "processing":
            raise GmailServiceError("A Gmail sync is already being processed", 409)
        root, report = gmail.sync(session["user_id"])
    except GmailServiceError as exc:
        raise _gmail_error(exc)

    _drop_gmail_runtime(session["user_id"])
    dataset_id = f"gmail_{hashlib.sha256(session['user_id'].encode()).hexdigest()[:10]}"
    dataset = Dataset(
        dataset_id,
        f"Gmail · {session['email']}",
        root,
        kind="gmail",
        report=report,
        owner_id=session["user_id"],
    )
    DATASETS[dataset_id] = dataset
    GMAIL_RUNTIME[session["user_id"]] = dataset_id
    dataset.job.update(status="processing", done=0, total=report["emails"])
    threading.Thread(target=_process, args=(dataset,), daemon=True).start()
    return dataset.info()


@app.post("/api/gmail/disconnect")
def gmail_disconnect(request: Request):
    try:
        user_id = gmail.disconnect(
            request.cookies.get(SESSION_COOKIE), request.headers.get("X-CSRF-Token")
        )
    except GmailServiceError as exc:
        raise _gmail_error(exc)
    if user_id:
        _drop_gmail_runtime(user_id)
    response = RedirectResponse(url="/api/gmail/status", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/datasets")
def list_datasets(request: Request):
    user_id = _session_user(request)
    return [d.info() for d in DATASETS.values() if not d.owner_id or d.owner_id == user_id]


@app.get("/api/datasets/{ds}")
def get_dataset(ds: str, request: Request):
    return ds_of(ds, request).info()


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
    DATASETS[ds_id] = d
    d.job.update(status="processing", done=0, total=report["emails"])
    threading.Thread(target=_process, args=(d,), daemon=True).start()
    return d.info()


@app.delete("/api/datasets/{ds}")
def delete_dataset(ds: str, request: Request):
    d = ds_of(ds, request)
    if d.kind == "demo":
        raise HTTPException(400, "The demo dataset cannot be deleted")
    if d.kind == "gmail":
        raise HTTPException(400, "Disconnect Gmail from Settings to remove this private dataset")
    DATASETS.pop(ds, None)
    shutil.rmtree(d.root, ignore_errors=True)
    return {"deleted": ds}
