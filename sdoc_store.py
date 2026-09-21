"""
sdoc_store.py — persistent storage for NavisAI on Supabase (Postgres tables + Storage bucket).

Why: on a serverless host such as Vercel the file system is ephemeral, so anything written to disk is lost
between requests and cold starts. When SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set, the app keeps
its durable state here; when they are not set every caller falls back to the local files it used before.

Layers, from the bottom:
  Backend           small interface: table rows (select/insert/update/delete) and bucket objects
  MemoryBackend     in-memory implementation, used by the tests
  SupabaseBackend   PostgREST + Storage REST over `requests` (the service-role key stays server-side)
  SupabaseLedger    the hash-chained audit ledger in a table, one chain shared by every server instance
  SupabaseOutbox    the amendment outbox in a table
  DecisionStore     reviewer decisions per dataset
  helpers           vision cache and call log, key/value state, dataset packs (documents in the bucket)

Run `python -m sdoc_store --selftest` with the two variables set to check tables and bucket end to end.
"""
import hashlib
import io
import json
import os
import re
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from sdoc_amendment import AmendmentOutbox, fingerprint
from sdoc_security import TamperEvidentAuditLedger

BUCKET_DEFAULT = "navis-documents"
PACK_LIMIT_BYTES = 45 * 1024 * 1024  # under the 50 MB default per-object limit of a Supabase project
T_DATASETS, T_LEDGER, T_AMEND = "navis_datasets", "navis_ledger", "navis_amendments"
T_DECISIONS, T_VCACHE, T_VCALLS, T_KV = "navis_decisions", "navis_vision_cache", "navis_vision_calls", "navis_kv"


class StoreError(RuntimeError):
    pass


class Conflict(StoreError):
    """A unique constraint was violated (the row already exists)."""


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _norm_where(where: Optional[Dict[str, Any]]) -> Dict[str, Tuple[str, Any]]:
    out = {}
    for col, cond in (where or {}).items():
        out[col] = cond if isinstance(cond, tuple) else ("eq", cond)
    return out


# --------------------------------------------------------------------------------------------- backends
class Backend:
    """Rows: select / insert / update / delete.  Objects: put / get / list / delete / signed upload."""

    def select(self, table: str, where=None, order: Optional[str] = None, desc: bool = False,
               limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def select_all(self, table: str, where=None, order: Optional[str] = None, desc: bool = False, page: int = 1000) -> List[Dict[str, Any]]:
        rows, offset = [], 0
        while True:
            chunk = self.select(table, where, order, desc, page, offset)
            rows += chunk
            if len(chunk) < page:
                return rows
            offset += page

    def insert(self, table: str, rows, on_conflict: Optional[str] = None, ignore_duplicates: bool = False) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def update(self, table: str, where, values: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def delete(self, table: str, where) -> int:
        raise NotImplementedError

    def put_object(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        raise NotImplementedError

    def get_object(self, path: str) -> Optional[bytes]:
        raise NotImplementedError

    def list_objects(self, prefix: str) -> List[str]:
        raise NotImplementedError

    def delete_objects(self, paths: List[str]) -> None:
        raise NotImplementedError

    def signed_upload(self, path: str) -> Dict[str, str]:
        raise NotImplementedError


class MemoryBackend(Backend):
    """Test double with the same semantics the code relies on: unique keys and auto ids."""

    UNIQUE = {T_DATASETS: ("id",), T_LEDGER: ("idx",), T_AMEND: ("fingerprint",), T_DECISIONS: ("dataset", "email_id"),
              T_VCACHE: ("key",), T_KV: ("key",)}
    AUTO_ID = (T_AMEND, T_VCALLS)

    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.objects: Dict[str, bytes] = {}
        self._lock = threading.RLock()
        self._seq: Dict[str, int] = {}

    @staticmethod
    def _match(row, where) -> bool:
        for col, (op, val) in where.items():
            v = row.get(col)
            if op == "eq" and v != val:
                return False
            if op == "gt" and not (v is not None and v > val):
                return False
            if op == "gte" and not (v is not None and v >= val):
                return False
            if op == "in" and v not in val:
                return False
        return True

    def select(self, table, where=None, order=None, desc=False, limit=None, offset=0):
        with self._lock:
            rows = [dict(r) for r in self.tables.get(table, []) if self._match(r, _norm_where(where))]
        if order:
            rows.sort(key=lambda r: r.get(order), reverse=desc)
        rows = rows[offset:]
        return rows[:limit] if limit is not None else rows

    def insert(self, table, rows, on_conflict=None, ignore_duplicates=False):
        rows = rows if isinstance(rows, list) else [rows]
        keys = self.UNIQUE.get(table, ())
        out = []
        with self._lock:
            store = self.tables.setdefault(table, [])
            for r in rows:
                r = dict(r)
                dup = next((x for x in store if keys and all(x.get(k) == r.get(k) for k in keys)), None)
                if dup is not None:
                    if ignore_duplicates:
                        continue
                    if on_conflict:
                        dup.update(r)
                        out.append(dict(dup))
                        continue
                    raise Conflict(f"duplicate key in {table}")
                if table in self.AUTO_ID and "id" not in r:
                    self._seq[table] = self._seq.get(table, 0) + 1
                    r["id"] = self._seq[table]
                store.append(r)
                out.append(dict(r))
        return out

    def update(self, table, where, values):
        with self._lock:
            hit = [r for r in self.tables.get(table, []) if self._match(r, _norm_where(where))]
            for r in hit:
                r.update(values)
            return [dict(r) for r in hit]

    def delete(self, table, where):
        with self._lock:
            store = self.tables.get(table, [])
            keep = [r for r in store if not self._match(r, _norm_where(where))]
            self.tables[table] = keep
            return len(store) - len(keep)

    def put_object(self, path, data, content_type="application/octet-stream"):
        self.objects[path] = bytes(data)

    def get_object(self, path):
        return self.objects.get(path)

    def list_objects(self, prefix):
        return sorted(p for p in self.objects if p.startswith(prefix))

    def delete_objects(self, paths):
        for p in paths:
            self.objects.pop(p, None)

    def signed_upload(self, path):
        return {"url": f"memory://upload/{path}", "token": "memory", "path": path}


class SupabaseBackend(Backend):
    def __init__(self, url: str, service_key: str, bucket: str = BUCKET_DEFAULT, timeout: float = 30.0, session=None):
        import requests  # imported here so the rest of the app works without it when Supabase is unused

        self.url = url.rstrip("/")
        self.bucket = bucket
        self.timeout = timeout
        self.http = session or requests.Session()
        self.http.headers.update({"apikey": service_key, "Authorization": f"Bearer {service_key}"})

    # -- helpers
    def _check(self, resp, what: str):
        if resp.status_code < 400:
            return resp
        try:
            body = resp.json()
        except Exception:
            body = {"message": resp.text[:300]}
        code = str(body.get("code", "")) if isinstance(body, dict) else ""
        if resp.status_code == 409 or code == "23505":
            raise Conflict(f"{what}: duplicate key")
        msg = (body.get("message") or body.get("error") or str(body)) if isinstance(body, dict) else str(body)
        raise StoreError(f"{what} failed ({resp.status_code}): {msg}")

    @staticmethod
    def _filters(where) -> List[Tuple[str, str]]:
        params = []
        for col, (op, val) in _norm_where(where).items():
            if op == "in":
                params.append((col, "in.(" + ",".join(str(v) for v in val) + ")"))
            else:
                params.append((col, f"{op}.{val}"))
        return params

    # -- rows
    def select(self, table, where=None, order=None, desc=False, limit=None, offset=0):
        params = [("select", "*")] + self._filters(where)
        if order:
            params.append(("order", f"{order}.{'desc' if desc else 'asc'}"))
        if limit is not None:
            params.append(("limit", str(limit)))
        if offset:
            params.append(("offset", str(offset)))
        r = self.http.get(f"{self.url}/rest/v1/{table}", params=params, timeout=self.timeout)
        return self._check(r, f"select {table}").json()

    def insert(self, table, rows, on_conflict=None, ignore_duplicates=False):
        rows = rows if isinstance(rows, list) else [rows]
        prefer = ["return=representation"]
        params = []
        if on_conflict:
            params.append(("on_conflict", on_conflict))
            prefer.append("resolution=merge-duplicates")
        elif ignore_duplicates:
            prefer.append("resolution=ignore-duplicates")
        out: List[Dict[str, Any]] = []
        for i in range(0, len(rows), 500):
            r = self.http.post(f"{self.url}/rest/v1/{table}", params=params, json=rows[i:i + 500],
                               headers={"Prefer": ",".join(prefer)}, timeout=self.timeout)
            out += self._check(r, f"insert {table}").json()
        return out

    def update(self, table, where, values):
        r = self.http.patch(f"{self.url}/rest/v1/{table}", params=self._filters(where), json=values,
                            headers={"Prefer": "return=representation"}, timeout=self.timeout)
        return self._check(r, f"update {table}").json()

    def delete(self, table, where):
        r = self.http.delete(f"{self.url}/rest/v1/{table}", params=self._filters(where),
                             headers={"Prefer": "return=representation"}, timeout=self.timeout)
        return len(self._check(r, f"delete {table}").json())

    # -- objects
    def _obj(self, path: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{quote(path, safe='/')}"

    def put_object(self, path, data, content_type="application/octet-stream"):
        r = self.http.post(self._obj(path), data=data, headers={"Content-Type": content_type, "x-upsert": "true"}, timeout=max(self.timeout, 120))
        self._check(r, f"upload {path}")

    def get_object(self, path):
        r = self.http.get(self._obj(path), timeout=max(self.timeout, 120))
        if r.status_code in (400, 404):
            return None
        return self._check(r, f"download {path}").content

    def list_objects(self, prefix):
        prefix = prefix.strip("/")
        out: List[str] = []
        stack = [prefix]
        while stack:
            folder = stack.pop()
            offset = 0
            while True:
                r = self.http.post(f"{self.url}/storage/v1/object/list/{self.bucket}",
                                   json={"prefix": folder, "limit": 1000, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
                                   timeout=self.timeout)
                items = self._check(r, f"list {folder}").json()
                for it in items:
                    full = f"{folder}/{it['name']}" if folder else it["name"]
                    (stack if it.get("id") is None else out).append(full)
                if len(items) < 1000:
                    break
                offset += 1000
        return sorted(out)

    def delete_objects(self, paths):
        for i in range(0, len(paths), 100):
            r = self.http.delete(f"{self.url}/storage/v1/object/{self.bucket}", json={"prefixes": paths[i:i + 100]}, timeout=self.timeout)
            self._check(r, "delete objects")

    def signed_upload(self, path):
        r = self.http.post(f"{self.url}/storage/v1/object/upload/sign/{self.bucket}/{quote(path, safe='/')}", json={}, timeout=self.timeout)
        body = self._check(r, f"sign upload {path}").json()
        rel = body.get("url", "")
        token = body.get("token") or (re.search(r"token=([^&]+)", rel).group(1) if "token=" in rel else "")
        full = rel if rel.startswith("http") else f"{self.url}/storage/v1{rel}"
        return {"url": full, "token": token, "path": path}


# --------------------------------------------------------------------------------------------- selection
_lock = threading.Lock()
_backend: Optional[Backend] = None
_resolved = False


def _under_test_runner() -> bool:
    argv0 = (sys.argv[0] if sys.argv else "").lower()
    return "pytest" in argv0 or "unittest" in argv0 or "py.test" in argv0


def enabled() -> bool:
    """True when both variables are set. A developer's real keys in .env must never make the test suite write to
    the real project, so under pytest/unittest Supabase is off unless NAVIS_ALLOW_LIVE_STORE=1."""
    if os.environ.get("NAVIS_STORAGE", "").lower() == "local":
        return False
    if _under_test_runner() and os.environ.get("NAVIS_ALLOW_LIVE_STORE") != "1":
        return False
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))


def get_store() -> Optional[Backend]:
    """The Supabase backend when it is configured, otherwise None (callers keep using local files)."""
    global _backend, _resolved
    with _lock:
        if not _resolved:
            if enabled():
                _backend = SupabaseBackend(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                                           os.environ.get("SUPABASE_BUCKET", BUCKET_DEFAULT))
            _resolved = True
        return _backend


def set_store(backend: Optional[Backend]) -> None:
    """Install a backend explicitly (tests) or None to force local mode."""
    global _backend, _resolved
    with _lock:
        _backend, _resolved = backend, True


# --------------------------------------------------------------------------------------------- audit ledger
class SupabaseLedger(TamperEvidentAuditLedger):
    """The same hash chain as TamperEvidentAuditLedger, stored in a table so every server instance appends to
    one chain. The primary key on `idx` makes two instances that race for the same slot collide, and the loser
    re-reads the tail and retries. `details` is stored as the exact JSON text that was hashed."""

    RETRIES = 8

    def __init__(self, backend: Backend):
        self.backend = backend
        self._lock = threading.RLock()
        self._blocks: List[Dict[str, Any]] = []
        self._sync()
        if not self._blocks:
            genesis = {
                "index": 0, "timestamp": _utc(), "actor": "GENESIS_SYSTEM", "email_id": "SYS_000", "action": "LEDGER_INITIALIZED",
                "details": "NavisAI Immutable Compliance Ledger Initialized", "previous_hash": "0" * 64,
                "block_hash": hashlib.sha256(b"GENESIS").hexdigest(),
            }
            try:
                self.backend.insert(T_LEDGER, self._to_row(genesis))
            except Conflict:
                pass
            self._sync()

    @staticmethod
    def _to_row(b: Dict[str, Any]) -> Dict[str, Any]:
        return {"idx": b["index"], "ts": b["timestamp"], "actor": b["actor"], "email_id": b["email_id"], "action": b["action"],
                "details_json": json.dumps(b["details"], sort_keys=True), "previous_hash": b["previous_hash"], "block_hash": b["block_hash"]}

    @staticmethod
    def _from_row(r: Dict[str, Any]) -> Dict[str, Any]:
        return {"index": r["idx"], "timestamp": r["ts"], "actor": r["actor"], "email_id": r["email_id"], "action": r["action"],
                "details": json.loads(r["details_json"]), "previous_hash": r["previous_hash"], "block_hash": r["block_hash"]}

    def _sync(self) -> None:
        with self._lock:
            last = self._blocks[-1]["index"] if self._blocks else -1
            rows = self.backend.select_all(T_LEDGER, {"idx": ("gt", last)}, order="idx")
            self._blocks += [self._from_row(r) for r in rows]

    @property
    def blocks(self) -> List[Dict[str, Any]]:
        self._sync()
        return list(self._blocks)

    def record_action(self, actor: str, email_id: str, action: str, details: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            for _ in range(self.RETRIES):
                self._sync()
                prev = self._blocks[-1]
                index, timestamp = prev["index"] + 1, _utc()
                payload = f"{index}:{timestamp}:{actor}:{email_id}:{action}:{json.dumps(details, sort_keys=True)}:{prev['block_hash']}"
                block = {"index": index, "timestamp": timestamp, "actor": actor, "email_id": email_id, "action": action,
                         "details": details, "previous_hash": prev["block_hash"], "block_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
                try:
                    self.backend.insert(T_LEDGER, self._to_row(block))
                except Conflict:
                    continue  # another instance took this slot; re-read the tail and try the next one
                self._blocks.append(block)
                return block
        raise StoreError("could not append to the audit ledger after repeated conflicts")

    def verify_integrity(self):
        self._sync()
        return super().verify_integrity()


# --------------------------------------------------------------------------------------------- amendment outbox
class SupabaseOutbox:
    """Same interface as sdoc_amendment.AmendmentOutbox, backed by the navis_amendments table."""

    def __init__(self, backend: Backend):
        self.backend = backend

    def record(self, dataset, email_id, shipment, message, decision, reason):
        now = _utc()
        row = {"fingerprint": fingerprint(dataset, email_id, message["fields"]), "dataset": dataset, "email_id": email_id,
               "shipment": shipment, "recipient": message["recipient"], "subject": message["subject"], "body": message["body"],
               "fields": json.loads(json.dumps(message["fields"], default=str)), "decision": decision, "reason": reason,
               "status": "sent", "block_index": None, "created_at": now, "updated_at": now}
        try:
            return self.backend.insert(T_AMEND, row)[0]
        except Conflict:
            return None

    def attach_block(self, amendment_id, block_index):
        self.backend.update(T_AMEND, {"id": amendment_id}, {"block_index": block_index, "updated_at": _utc()})

    def discard(self, amendment_id):
        self.backend.delete(T_AMEND, {"id": amendment_id})

    def get(self, dataset, email_id):
        rows = self.backend.select(T_AMEND, {"dataset": dataset, "email_id": email_id}, order="id", desc=True, limit=1)
        return rows[0] if rows else None

    def all(self, dataset):
        rows = self.backend.select_all(T_AMEND, {"dataset": dataset}, order="id")
        return {r["email_id"]: r for r in rows}

    def set_status(self, dataset, email_id, status):
        latest = self.get(dataset, email_id)
        if not latest:
            return False
        self.backend.update(T_AMEND, {"id": latest["id"]}, {"status": status, "updated_at": _utc()})
        return True


def make_outbox(local_path: str):
    b = get_store()
    return SupabaseOutbox(b) if b is not None else AmendmentOutbox(local_path)


def make_ledger(local_path: str):
    b = get_store()
    return SupabaseLedger(b) if b is not None else TamperEvidentAuditLedger(local_path)


# --------------------------------------------------------------------------------------------- decisions
class DecisionStore:
    """Reviewer decisions (Override, Corrected document received, ...) per dataset and email.
    Kept in memory only when Supabase is not configured, as before."""

    def __init__(self, backend: Optional[Backend] = None):
        self.backend = backend
        self._mem: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def all(self, dataset: str) -> Dict[str, Dict[str, Any]]:
        if self.backend is None:
            return dict(self._mem.get(dataset, {}))
        rows = self.backend.select_all(T_DECISIONS, {"dataset": dataset})
        return {r["email_id"]: r["value"] for r in rows}

    def set(self, dataset: str, email_id: str, value: Dict[str, Any]) -> None:
        if self.backend is None:
            self._mem.setdefault(dataset, {})[email_id] = value
            return
        self.backend.insert(T_DECISIONS, {"dataset": dataset, "email_id": email_id, "value": json.loads(json.dumps(value, default=str)), "updated_at": _utc()},
                            on_conflict="dataset,email_id")

    def clear(self, dataset: str) -> None:
        if self.backend is None:
            self._mem.pop(dataset, None)
        else:
            self.backend.delete(T_DECISIONS, {"dataset": dataset})


def make_decisions() -> DecisionStore:
    return DecisionStore(get_store())


# --------------------------------------------------------------------------------------------- vision cache, kv
def vision_get(key: str) -> Optional[Dict[str, Any]]:
    b = get_store()
    if b is None:
        return None
    rows = b.select(T_VCACHE, {"key": key}, limit=1)
    return rows[0]["data"] if rows else None


def vision_put(key: str, data: Dict[str, Any]) -> None:
    b = get_store()
    if b is not None:
        b.insert(T_VCACHE, {"key": key, "data": data}, on_conflict="key")


def vision_log(entry: Dict[str, Any]) -> None:
    b = get_store()
    if b is not None:
        b.insert(T_VCALLS, {k: entry.get(k) for k in ("at", "doc", "model", "image_bytes", "latency_ms", "outcome", "prompt_tokens", "output_tokens", "total_tokens")})


def kv_get(key: str) -> Optional[Any]:
    b = get_store()
    if b is None:
        return None
    rows = b.select(T_KV, {"key": key}, limit=1)
    return rows[0]["value"] if rows else None


def kv_set(key: str, value: Any) -> None:
    b = get_store()
    if b is not None:
        b.insert(T_KV, {"key": key, "value": value, "updated_at": _utc()}, on_conflict="key")


# --------------------------------------------------------------------------------------------- datasets and documents
def _safe_member(name: str) -> Optional[PurePosixPath]:
    p = PurePosixPath(name.replace("\\", "/"))
    parts = [s for s in p.parts if s not in ("", ".")]
    if not parts or p.is_absolute() or any(s == ".." or ":" in s for s in parts):
        return None
    return PurePosixPath(*parts)


def unzip_safely(data: bytes, dest: Path, max_bytes: int = 300 * 1024 * 1024, max_files: int = 20000) -> int:
    """Extract a zip into dest, refusing traversal paths and oversized archives. Returns the file count."""
    n = total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > max_files or sum(i.file_size for i in infos) > max_bytes:
            raise StoreError("archive is too large")
        for info in infos:
            member = _safe_member(info.filename)
            if member is None:
                continue
            target = dest / Path(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
            n += 1
            total += info.file_size
    return n


def zip_parts(files: Iterable[Tuple[str, Path]], limit: int = PACK_LIMIT_BYTES) -> List[bytes]:
    """Group files (archive name, path) into zips that each stay under `limit` bytes."""
    parts: List[bytes] = []
    buf, size, z = None, 0, None

    def close():
        nonlocal buf, z, size
        if z is not None:
            z.close()
            parts.append(buf.getvalue())
        buf, z, size = None, None, 0

    for arc, path in files:
        data = path.read_bytes()
        if z is not None and size + len(data) > limit:
            close()
        if z is None:
            buf = io.BytesIO()
            z = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
        z.writestr(arc, data)
        size += len(data)
    close()
    return parts


def save_dataset_row(b: Backend, ds_id: str, name: str, kind: str, created: str, report: Dict[str, Any]) -> None:
    b.insert(T_DATASETS, {"id": ds_id, "name": name, "kind": kind, "created": created, "report": report}, on_conflict="id")


def list_dataset_rows(b: Backend) -> List[Dict[str, Any]]:
    return b.select_all(T_DATASETS, order="created")


def save_pack(b: Backend, ds_id: str, root: Path) -> int:
    """Store a whole normalised dataset (inbox + attachments) as a few zip objects."""
    files = [(str(p.relative_to(root)).replace("\\", "/"), p) for sub in ("inbox", "attachments") for p in sorted((root / sub).rglob("*")) if p.is_file()]
    parts = zip_parts(files)
    for i, data in enumerate(parts, 1):
        b.put_object(f"{ds_id}/packs/pack-{i:04d}.zip", data, "application/zip")
    return len(parts)


def save_extra_email(b: Backend, ds_id: str, root: Path, email_id: str) -> None:
    """Store one email added after the dataset was created (for example from Gmail) as individual objects."""
    rec_path = root / "inbox" / f"{email_id}.json"
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    b.put_object(f"{ds_id}/extra/{email_id}/record.json", rec_path.read_bytes(), "application/json")
    for rel in rec.get("attachments", []):
        f = root / rel
        if f.is_file():
            b.put_object(f"{ds_id}/extra/{email_id}/{rel}", f.read_bytes())


def hydrate(b: Backend, ds_id: str, root: Path) -> int:
    """Rebuild the local cache of a dataset from the bucket. Returns the number of files written."""
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for path in b.list_objects(f"{ds_id}/packs"):
        data = b.get_object(path)
        if data:
            n += unzip_safely(data, root)
    prefix = f"{ds_id}/extra/"
    for path in b.list_objects(f"{ds_id}/extra"):
        rest = path[len(prefix):]
        _, _, tail = rest.partition("/")
        if tail == "record.json":
            eid = rest.split("/")[0]
            dest = root / "inbox" / f"{eid}.json"
        else:
            member = _safe_member(tail)
            if member is None:
                continue
            dest = root / Path(*member.parts)
        data = b.get_object(path)
        if data is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            n += 1
    return n


def delete_dataset_everywhere(b: Backend, ds_id: str) -> None:
    b.delete_objects(b.list_objects(f"{ds_id}"))
    b.delete(T_DATASETS, {"id": ds_id})
    b.delete(T_DECISIONS, {"dataset": ds_id})
    b.delete(T_AMEND, {"dataset": ds_id})


# --------------------------------------------------------------------------------------------- self test
def selftest() -> int:
    b = get_store()
    if b is None:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are not set.")
        return 2
    probe = f"selftest-{int(datetime.utcnow().timestamp())}"
    results: List[Tuple[str, str]] = []

    def step(name: str, fn) -> None:
        try:
            fn()
            results.append((name, "ok"))
        except Exception as e:  # noqa: BLE001
            results.append((name, f"FAILED: {e}"))

    def expect(cond: bool, what: str) -> None:
        if not cond:
            raise StoreError(what)

    def kv_roundtrip():
        kv_set(probe, {"x": 1})
        expect(kv_get(probe) == {"x": 1}, "value did not round-trip")
        b.delete(T_KV, {"key": probe})

    def bucket_roundtrip():
        b.put_object(f"{probe}/a.txt", b"hello")
        expect(b.get_object(f"{probe}/a.txt") == b"hello", "object did not round-trip")
        expect(f"{probe}/a.txt" in b.list_objects(probe), "object missing from listing")
        b.delete_objects([f"{probe}/a.txt"])
        expect(b.get_object(f"{probe}/a.txt") is None, "object still present after delete")

    step("key/value table", kv_roundtrip)
    step("bucket upload/list/delete", bucket_roundtrip)
    step("signed upload url", lambda: expect(b.signed_upload(f"{probe}/signed.bin").get("url", "").startswith("http"), "no url returned"))
    step("audit ledger table", lambda: expect(SupabaseLedger(b).verify_integrity()[0], "ledger does not verify"))
    step("amendment table", lambda: SupabaseOutbox(b).all(probe))
    step("datasets table", lambda: b.select(T_DATASETS, limit=1))
    step("decisions table", lambda: b.select(T_DECISIONS, limit=1))
    step("vision tables", lambda: (b.select(T_VCACHE, limit=1), b.select(T_VCALLS, limit=1)))
    for name, res in results:
        print(f"{name:28} {res}")
    return 0 if all(r == "ok" for _, r in results) else 1


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv

    load_dotenv()
    sys.exit(selftest() if "--selftest" in sys.argv else print(__doc__) or 0)
