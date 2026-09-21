"""Folder import: normalises an uploaded folder (bundle layout and/or .eml files) into the
`inbox/*.json` + `attachments/` layout that the sdoc_* pipeline reads."""
import html
import json
import re
from email import policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Tuple

from sdoc_loader import InboxLoader

MAX_FILES = 20000
MAX_TOTAL_BYTES = 250 * 1024 * 1024
ALLOWED_EXT = {".json", ".eml", ".txt", ".pdf", ".docx", ".xlsx", ".xls"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


class ImportError_(Exception):
    pass


def safe_relpath(raw: str) -> PurePosixPath:
    """Sanitised relative path; rejects traversal, absolute paths and drive letters."""
    p = PurePosixPath(raw.replace("\\", "/"))
    parts = [s for s in p.parts if s not in ("", ".")]
    if not parts or any(s == ".." or ":" in s for s in parts) or p.is_absolute():
        raise ImportError_(f"Unsafe path in upload: {raw!r}")
    return PurePosixPath(*parts)


def _lower_layout_dirs(p: PurePosixPath) -> PurePosixPath:
    """`Inbox/` and `Attachments/` (any case) become the canonical lowercase names."""
    return PurePosixPath(*[s.lower() if i < len(p.parts) - 1 and s.lower() in ("inbox", "attachments") else s for i, s in enumerate(p.parts)])


def strip_common_root(paths: List[PurePosixPath]) -> List[PurePosixPath]:
    """Drops the picked folder's own name; if an `inbox/` folder (any case) sits deeper, roots the dataset there."""
    paths = [_lower_layout_dirs(p) for p in paths]
    for p in paths:
        if "inbox" in p.parts[:-1]:
            depth = p.parts.index("inbox")
            return [PurePosixPath(*q.parts[depth:]) if len(q.parts) > depth else q for q in paths]
    if paths and all(len(p.parts) > 1 for p in paths) and len({p.parts[0] for p in paths}) == 1:
        return [PurePosixPath(*p.parts[1:]) for p in paths]
    return paths


def _clean_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "file"
    return stem[:120]


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_eml(data: bytes) -> Tuple[Dict[str, str], List[Tuple[str, bytes]]]:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if body_part is not None:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = _html_to_text(body)
    atts = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        atts.append((part.get_filename() or "attachment", payload))
    meta = {"from": str(msg.get("From", "")), "subject": str(msg.get("Subject", "")), "body": body}
    return meta, atts


def normalise(staging: Path, dest: Path) -> Dict[str, Any]:
    """Reads `staging` (bundle and/or .eml) and writes the normalised layout into `dest`."""
    inbox, attach = dest / "inbox", dest / "attachments"
    inbox.mkdir(parents=True, exist_ok=True)
    attach.mkdir(parents=True, exist_ok=True)
    used: set = set()
    n = 0
    report = {"bundle_emails": 0, "eml_emails": 0, "attachments": 0, "skipped": []}

    def new_id(preferred: str = "") -> str:
        nonlocal n
        if preferred and ID_RE.match(preferred) and preferred not in used:
            used.add(preferred)
            return preferred
        while True:
            n += 1
            eid = f"email_{n:03d}"
            if eid not in used:
                used.add(eid)
                return eid

    def store_attachment(eid: str, name: str, data: bytes) -> str:
        safe = _clean_name(name)
        target = attach / (safe if safe.startswith(eid) else f"{eid}_{safe}")
        target.write_bytes(data)
        report["attachments"] += 1
        return f"attachments/{target.name}"

    # 1. Bundle layout: inbox/*.json referencing attachments/...
    for jf in sorted((staging / "inbox").glob("*.json")) if (staging / "inbox").is_dir() else []:
        try:
            rec = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            assert isinstance(rec, dict)
        except Exception:
            report["skipped"].append(f"{jf.name}: not a valid email record")
            continue
        eid = new_id(str(rec.get("email_id", "")) or jf.stem)
        rels = []
        for ref in rec.get("attachments", []) or []:
            try:
                src = staging / _lower_layout_dirs(safe_relpath(str(ref)))
            except ImportError_:
                report["skipped"].append(f"{jf.name}: unsafe attachment path {ref!r}")
                continue
            if src.is_file():
                rels.append(store_attachment(eid, src.name if src.name.startswith(eid) else src.name, src.read_bytes()))
            else:
                report["skipped"].append(f"{jf.name}: attachment {ref} not in upload")
        out = {"email_id": eid, "from": rec.get("from", ""), "subject": rec.get("subject", ""), "body": rec.get("body", ""), "attachments": rels}
        (inbox / f"{eid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        report["bundle_emails"] += 1

    # 2. .eml files anywhere in the upload
    for ef in sorted(staging.rglob("*.eml")):
        try:
            meta, atts = parse_eml(ef.read_bytes())
        except Exception as e:
            report["skipped"].append(f"{ef.name}: cannot parse ({e})")
            continue
        eid = new_id()
        rels = [store_attachment(eid, name, data) for name, data in atts]
        out = {"email_id": eid, **meta, "attachments": rels, "source_file": ef.name}
        (inbox / f"{eid}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        report["eml_emails"] += 1

    # 3. Name SI / BL attachments by content so the pipeline recognises them (…_SI.pdf, …_BL.pdf)
    loader = InboxLoader(str(dest))
    for jf in sorted(inbox.glob("*.json")):
        rec = json.loads(jf.read_text(encoding="utf-8"))
        changed, seen = False, set()
        new_atts = []
        for rel in rec.get("attachments", []):
            path = dest / rel
            if re.search(r"_(SI|BL)\.", path.name, re.I):
                new_atts.append(rel)
                continue
            kind = loader.load_attachment(rel).detected_doc_type
            if kind in ("SI", "BL") and kind not in seen:
                seen.add(kind)
                renamed = path.with_name(f"{rec['email_id']}_{kind}{path.suffix.lower()}")
                if not renamed.exists():
                    path.rename(renamed)
                    rel, changed = f"attachments/{renamed.name}", True
            new_atts.append(rel)
        if changed:
            rec["attachments"] = new_atts
            jf.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    report["emails"] = report["bundle_emails"] + report["eml_emails"]
    if report["emails"] == 0:
        raise ImportError_("No emails found. Expected an inbox/*.json + attachments/ bundle, or .eml files.")
    return report


# Fallback export so Vercel function discovery finds an ASGI app if scanned
try:
    from api.main import app
except Exception:
    pass

