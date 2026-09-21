"""NavisAI API — thin FastAPI layer over the sdoc_* verification pipeline.

Run from the repo root:  uvicorn api.main:app --reload --port 8000
Text extraction is deterministic; scanned PDFs use Gemini vision when a key is configured.
Datasets: the bundled demo inbox ("demo") plus any folders imported through /api/datasets/import.
"""
import dataclasses
import json
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
from pydantic import BaseModel

from api import importer
from sdoc_classifier import EmailClassifier, is_draft_request
from sdoc_extractor import FieldExtractor
from sdoc_loader import InboxLoader
from sdoc_reconciler import DocumentReconciler
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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

classifier = EmailClassifier()
extractor = FieldExtractor()
reconciler = DocumentReconciler()
ledger = TamperEvidentAuditLedger(str(ROOT / "audit_ledger.json"))


class Dataset:
    def __init__(self, id: str, name: str, root: Path, kind: str = "import", created: Optional[str] = None, report: Optional[dict] = None):
        self.id, self.name, self.root, self.kind = id, name, root, kind
        self.created = created or datetime.utcnow().isoformat() + "Z"
        self.report = report or {}
        self.loader = InboxLoader(str(root))
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.overrides: Dict[str, Dict[str, Any]] = {}
        self.timing: Dict[str, float] = {}
        self.job: Dict[str, Any] = {"status": "ready", "done": 0, "total": 0, "failed": []}

    def info(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "kind": self.kind, "created": self.created,
                "emails": len(self.loader.get_email_ids()), "job": self.job, "report": self.report}


DATASETS: Dict[str, Dataset] = {"demo": Dataset("demo", "SDOC demo inbox", BUNDLE, kind="demo")}


def _load_saved() -> None:
    if not DATASETS_DIR.is_dir():
        return
    for d in sorted(DATASETS_DIR.iterdir()):
        meta = d / "meta.json"
        if meta.is_file():
            m = json.loads(meta.read_text(encoding="utf-8"))
            DATASETS[d.name] = Dataset(d.name, m.get("name", d.name), d, created=m.get("created"), report=m.get("report"))


_load_saved()


def ds_of(ds: str) -> Dataset:
    if ds not in DATASETS:
        raise HTTPException(404, f"unknown dataset {ds!r}")
    return DATASETS[ds]


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
    si = bl = None
    loaded = [d.loader.load_attachment(a) for a in email.get("attachments", [])]
    for att in loaded:
        fn = att.filename.upper()
        if "_SI" in fn or "SI_" in fn or att.detected_doc_type == "SI":
            si = si or att
        elif "_BL" in fn or "BL_" in fn or att.detected_doc_type == "BL":
            bl = bl or att
    if len(loaded) == 2 and (si is None or bl is None):
        si = si or next((x for x in loaded if x.detected_doc_type == "SI"), loaded[0])
        bl = bl or next((x for x in loaded if x.detected_doc_type == "BL"), loaded[1] if loaded[0] is si else loaded[0])
    return si, bl, loaded


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
            si, bl, _ = _pick_docs(d, email)
            si_f = extractor.extract(si) if si else None
            bl_f = extractor.extract(bl) if bl else None
            entry = reconciler.reconcile(email_id=eid, category=category, si_att=si, bl_att=bl, si_fields=si_f, bl_fields=bl_f, draft_request=is_draft_request(email))
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
    return {"status": "operational", "emails": len(d.loader.get_email_ids()), "datasets": len(DATASETS),
            "engine": "deterministic-text + gemini-vision", "ledger_ok": ledger.verify_integrity()[0]}


@app.get("/api/emails")
def emails(ds: str = "demo"):
    d = ds_of(ds)
    return [_summary_row(d, run_email(d, e)) for e in d.loader.get_email_ids()]


@app.get("/api/emails/{eid}")
def email_detail(eid: str, ds: str = "demo"):
    d = ds_of(ds)
    if eid not in d.loader.get_email_ids():
        raise HTTPException(404, "email not found")
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


@app.post("/api/emails/{eid}/action")
def record(eid: str, body: Action, ds: str = "demo"):
    d = ds_of(ds)
    if eid not in d.loader.get_email_ids():
        raise HTTPException(404, "email not found")
    ref = eid if d.id == "demo" else f"{d.id}:{eid}"
    block = ledger.record_action(body.actor, ref, body.action, body.details)
    if body.action in ("CONFIRM_AI_RESULT", "OVERRIDE_RESULT", "AMENDMENT_DISPATCHED", "AMENDMENT_CONFIRMED"):
        d.overrides[eid] = {"action": body.action, "at": datetime.utcnow().isoformat() + "Z", "block": block["index"], **body.details}
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
    return {"kind": "mismatch", "shipment": r["shipment"], "email": eid, "issues": bad, "evidence": r["attachments"], "recommendation": "Request corrected draft BL from carrier."}


# ---------------------------------------------------------------- dataset import
def _process(d: Dataset) -> None:
    ids = d.loader.get_email_ids()
    d.job.update(status="processing", done=0, total=len(ids), failed=[])
    for eid in ids:
        r = run_email(d, eid)
        if r.get("error"):
            d.job["failed"].append({"id": eid, "error": r["error"]})
        d.job["done"] += 1
    d.job["status"] = "ready"


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
    return {"deleted": ds}
