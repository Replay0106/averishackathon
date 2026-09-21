"""Secure Google OAuth and Gmail ingestion for NavisAI.

The module intentionally uses Google's HTTPS REST endpoints directly.  This keeps the
runtime small while still using OAuth 2.0 authorization-code flow with PKCE.  Refresh
tokens and cached Gmail payloads are encrypted with AES-256-GCM before SQLite sees
them; decrypted documents are materialised only in an OS temporary directory so the
existing document pipeline can process them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from dotenv import load_dotenv

from api import importer
from sdoc_loader import InboxLoader

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - surfaced as a configuration error
    AESGCM = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = ("openid", "email", "https://www.googleapis.com/auth/gmail.readonly")
SESSION_COOKIE = "navis_session"
ALLOWED_GMAIL_ATTACHMENTS = {".pdf", ".docx", ".xlsx", ".xls", ".txt"}


def _utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


class GmailServiceError(RuntimeError):
    """A user-safe integration error with a suitable HTTP status."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OAuthResult:
    session_token: str
    user_id: str
    email: str


class GmailService:
    def __init__(self, private_dir: Optional[Path] = None):
        self.private_dir = private_dir or ROOT / "private"
        self.db_path = self.private_dir / "gmail.db"
        self.client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/api/gmail/callback").strip()
        self.frontend_url = os.getenv("GMAIL_FRONTEND_URL", "http://localhost:5173/#/settings").strip()
        self.label = os.getenv("GMAIL_LABEL", "NavisAI").strip() or "NavisAI"
        self.cookie_secure = _truthy(os.getenv("COOKIE_SECURE", "false"))
        self.max_messages = min(max(int(os.getenv("GMAIL_SYNC_MAX_MESSAGES", "100")), 1), 500)
        self.max_attachment_bytes = min(max(int(os.getenv("GMAIL_MAX_ATTACHMENT_MB", "15")), 1), 25) * 1024 * 1024
        self.max_sync_bytes = min(max(int(os.getenv("GMAIL_MAX_SYNC_MB", "100")), 1), 250) * 1024 * 1024
        self._key: Optional[bytes] = None
        self._configuration_error = self._load_key()
        if not self.client_id or not self.client_secret:
            self._configuration_error = "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are required"
        if AESGCM is None:
            self._configuration_error = "cryptography is required; install the project requirements"
        if not self._configuration_error:
            self.private_dir.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _load_key(self) -> Optional[str]:
        raw = os.getenv("APP_ENCRYPTION_KEY", "").strip()
        if not raw:
            return "APP_ENCRYPTION_KEY is required (URL-safe base64 for exactly 32 random bytes)"
        try:
            key = _b64url_decode(raw)
        except Exception:
            return "APP_ENCRYPTION_KEY is not valid URL-safe base64"
        if len(key) != 32:
            return "APP_ENCRYPTION_KEY must decode to exactly 32 bytes"
        self._key = key
        return None

    @property
    def configured(self) -> bool:
        return self._configuration_error is None

    @property
    def configuration_error(self) -> Optional[str]:
        return self._configuration_error

    def require_configured(self) -> None:
        if not self.configured:
            raise GmailServiceError(self._configuration_error or "Gmail integration is not configured", 503)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    verifier_enc BLOB NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS gmail_connections (
                    user_id TEXT PRIMARY KEY,
                    email_enc BLOB NOT NULL,
                    refresh_token_enc BLOB NOT NULL,
                    granted_scopes TEXT NOT NULL,
                    label_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_sync_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    csrf_enc BLOB NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES gmail_connections(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS gmail_messages (
                    user_id TEXT NOT NULL,
                    gmail_id TEXT NOT NULL,
                    payload_enc BLOB NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, gmail_id),
                    FOREIGN KEY(user_id) REFERENCES gmail_connections(user_id) ON DELETE CASCADE
                );
                """
            )

    def _encrypt(self, plaintext: bytes, aad: str) -> bytes:
        self.require_configured()
        nonce = secrets.token_bytes(12)
        return nonce + AESGCM(self._key).encrypt(nonce, plaintext, aad.encode("utf-8"))

    def _decrypt(self, ciphertext: bytes, aad: str) -> bytes:
        self.require_configured()
        if len(ciphertext) < 29:
            raise GmailServiceError("Encrypted Gmail data is invalid", 500)
        try:
            return AESGCM(self._key).decrypt(ciphertext[:12], ciphertext[12:], aad.encode("utf-8"))
        except Exception as exc:
            raise GmailServiceError("Unable to decrypt Gmail data; check APP_ENCRYPTION_KEY", 500) from exc

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def begin_oauth(self) -> str:
        self.require_configured()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
        state_hash = self._digest(state)
        now = _utc_ts()
        with self._connect() as con:
            con.execute("DELETE FROM oauth_states WHERE expires_at < ?", (now,))
            con.execute(
                "INSERT INTO oauth_states(state_hash, verifier_enc, expires_at) VALUES (?, ?, ?)",
                (state_hash, self._encrypt(verifier.encode(), state_hash), now + 600),
            )
        query = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTH_URL}?{query}"

    def complete_oauth(self, code: str, state: str) -> OAuthResult:
        self.require_configured()
        if not code or not state:
            raise GmailServiceError("Google did not return an authorization code and state")
        state_hash = self._digest(state)
        with self._connect() as con:
            row = con.execute(
                "SELECT verifier_enc, expires_at FROM oauth_states WHERE state_hash=?", (state_hash,)
            ).fetchone()
            con.execute("DELETE FROM oauth_states WHERE state_hash=?", (state_hash,))
        if not row or int(row["expires_at"]) < _utc_ts():
            raise GmailServiceError("OAuth state is invalid or expired; start the Gmail connection again")
        verifier = self._decrypt(row["verifier_enc"], state_hash).decode()
        token = self._post_form(
            TOKEN_URL,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
            },
        )
        access_token = str(token.get("access_token", ""))
        if not access_token:
            raise GmailServiceError("Google token exchange did not return an access token", 502)
        profile = self._get_json(USERINFO_URL, access_token)
        user_id, email = str(profile.get("sub", "")), str(profile.get("email", "")).strip()
        if not user_id or not email or profile.get("email_verified") is False:
            raise GmailServiceError("Google account email could not be verified", 502)
        refresh_token = str(token.get("refresh_token", ""))
        with self._connect() as con:
            existing = con.execute(
                "SELECT refresh_token_enc FROM gmail_connections WHERE user_id=?", (user_id,)
            ).fetchone()
            if not refresh_token and existing:
                refresh_enc = existing["refresh_token_enc"]
            elif refresh_token:
                refresh_enc = self._encrypt(refresh_token.encode(), f"refresh:{user_id}")
            else:
                raise GmailServiceError("Google did not issue a refresh token; revoke NavisAI access and connect again")
            now = _utc_iso()
            con.execute(
                """INSERT INTO gmail_connections
                   (user_id, email_enc, refresh_token_enc, granted_scopes, label_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     email_enc=excluded.email_enc,
                     refresh_token_enc=excluded.refresh_token_enc,
                     granted_scopes=excluded.granted_scopes,
                     label_name=excluded.label_name""",
                (user_id, self._encrypt(email.encode(), f"email:{user_id}"), refresh_enc,
                 str(token.get("scope", " ".join(SCOPES))), self.label, now),
            )
        return OAuthResult(self._create_session(user_id), user_id, email)

    def _create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(48)
        session_hash = self._digest(token)
        csrf = secrets.token_urlsafe(32)
        with self._connect() as con:
            con.execute("DELETE FROM sessions WHERE expires_at < ?", (_utc_ts(),))
            con.execute(
                "INSERT INTO sessions(session_hash, user_id, csrf_enc, expires_at) VALUES (?, ?, ?, ?)",
                (session_hash, user_id, self._encrypt(csrf.encode(), f"csrf:{session_hash}"), _utc_ts() + 7 * 86400),
            )
        return token

    def session(self, token: Optional[str]) -> Optional[Dict[str, str]]:
        if not self.configured or not token:
            return None
        session_hash = self._digest(token)
        with self._connect() as con:
            row = con.execute(
                """SELECT s.user_id, s.csrf_enc, s.expires_at, c.email_enc, c.last_sync_at
                   FROM sessions s JOIN gmail_connections c ON c.user_id=s.user_id
                   WHERE s.session_hash=?""",
                (session_hash,),
            ).fetchone()
            if row and int(row["expires_at"]) < _utc_ts():
                con.execute("DELETE FROM sessions WHERE session_hash=?", (session_hash,))
                row = None
        if not row:
            return None
        csrf = self._decrypt(row["csrf_enc"], f"csrf:{session_hash}").decode()
        email = self._decrypt(row["email_enc"], f"email:{row['user_id']}").decode()
        return {
            "user_id": row["user_id"],
            "email": email,
            "csrf_token": csrf,
            "last_sync_at": row["last_sync_at"] or "",
        }

    def require_session(self, token: Optional[str]) -> Dict[str, str]:
        session = self.session(token)
        if not session:
            raise GmailServiceError("Sign in with Google first", 401)
        return session

    def require_csrf(self, token: Optional[str], supplied: Optional[str]) -> Dict[str, str]:
        session = self.require_session(token)
        if not supplied or not hmac.compare_digest(session["csrf_token"], supplied):
            raise GmailServiceError("Invalid CSRF token", 403)
        return session

    def status(self, token: Optional[str]) -> Dict[str, Any]:
        session = self.session(token)
        return {
            "configured": self.configured,
            "configuration_error": self.configuration_error,
            "connected": session is not None,
            "email": session["email"] if session else None,
            "label": self.label,
            "last_sync_at": session["last_sync_at"] if session else None,
            "csrf_token": session["csrf_token"] if session else None,
        }

    def disconnect(self, token: Optional[str], csrf: Optional[str]) -> Optional[str]:
        session = self.require_csrf(token, csrf)
        user_id = session["user_id"]
        refresh_token = self._refresh_token(user_id)
        # Revocation is best effort; local deletion must still succeed if Google is unavailable.
        try:
            self._post_form("https://oauth2.googleapis.com/revoke", {"token": refresh_token}, expect_json=False)
        except GmailServiceError:
            pass
        with self._connect() as con:
            con.execute("DELETE FROM gmail_connections WHERE user_id=?", (user_id,))
        return user_id

    def _refresh_token(self, user_id: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT refresh_token_enc FROM gmail_connections WHERE user_id=?", (user_id,)
            ).fetchone()
        if not row:
            raise GmailServiceError("Gmail connection no longer exists", 401)
        return self._decrypt(row["refresh_token_enc"], f"refresh:{user_id}").decode()

    def _access_token(self, user_id: str) -> str:
        token = self._post_form(
            TOKEN_URL,
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self._refresh_token(user_id),
                "grant_type": "refresh_token",
            },
        )
        access_token = str(token.get("access_token", ""))
        if not access_token:
            raise GmailServiceError("Google did not return an access token; reconnect Gmail", 502)
        return access_token

    def sync(self, user_id: str) -> tuple[Path, Dict[str, Any]]:
        """Fetch the configured Gmail label, encrypt the cache, and materialise it."""
        self.require_configured()
        access_token = self._access_token(user_id)
        labels = self._get_json(f"{GMAIL_API}/labels", access_token).get("labels", [])
        label = next((x for x in labels if str(x.get("name", "")).casefold() == self.label.casefold()), None)
        if not label:
            raise GmailServiceError(f'Gmail label "{self.label}" was not found. Create it and apply it to the emails to import.', 404)

        ids: list[str] = []
        page_token = ""
        while len(ids) < self.max_messages:
            query: Dict[str, Any] = {
                "labelIds": label["id"],
                "maxResults": min(100, self.max_messages - len(ids)),
            }
            if page_token:
                query["pageToken"] = page_token
            page = self._get_json(f"{GMAIL_API}/messages?{urllib.parse.urlencode(query)}", access_token)
            ids.extend(str(x["id"]) for x in page.get("messages", []) if x.get("id"))
            page_token = str(page.get("nextPageToken", ""))
            if not page_token:
                break

        records: list[tuple[str, bytes]] = []
        skipped: list[str] = []
        total_bytes = 0
        attachment_count = 0
        for gmail_id in ids:
            raw = self._get_json(f"{GMAIL_API}/messages/{urllib.parse.quote(gmail_id)}?format=full", access_token)
            record, used, skipped_names = self._normalise_message(raw, access_token, total_bytes)
            total_bytes += used
            attachment_count += len(record["attachments"])
            skipped.extend(skipped_names)
            records.append((gmail_id, self._encrypt(json.dumps(record).encode("utf-8"), f"message:{user_id}:{gmail_id}")))

        now = _utc_iso()
        with self._connect() as con:
            con.execute("DELETE FROM gmail_messages WHERE user_id=?", (user_id,))
            con.executemany(
                "INSERT INTO gmail_messages(user_id, gmail_id, payload_enc, updated_at) VALUES (?, ?, ?, ?)",
                [(user_id, gmail_id, payload, now) for gmail_id, payload in records],
            )
            con.execute("UPDATE gmail_connections SET last_sync_at=? WHERE user_id=?", (now, user_id))

        root = self.materialise(user_id)
        report = {
            "emails": len(records),
            "attachments": attachment_count,
            "label": self.label,
            "skipped": skipped[:50],
            "last_sync_at": now,
            "encrypted_cache": True,
        }
        return root, report

    def _normalise_message(
        self, message: Dict[str, Any], access_token: str, bytes_already_used: int
    ) -> tuple[Dict[str, Any], int, list[str]]:
        gmail_id = str(message.get("id", ""))
        payload = message.get("payload") or {}
        headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in payload.get("headers", [])}
        bodies: Dict[str, list[str]] = {"text/plain": [], "text/html": []}
        attachments: list[Dict[str, str]] = []
        skipped: list[str] = []
        bytes_used = 0

        def walk(part: Dict[str, Any]) -> None:
            nonlocal bytes_used
            mime = str(part.get("mimeType", "")).lower()
            filename = str(part.get("filename", "")).strip()
            body = part.get("body") or {}
            encoded = str(body.get("data", ""))
            if filename:
                clean = importer._clean_name(filename)
                ext = Path(clean).suffix.lower()
                if ext not in ALLOWED_GMAIL_ATTACHMENTS:
                    skipped.append(f"{clean}: unsupported type")
                else:
                    data = b""
                    if encoded:
                        data = _b64url_decode(encoded)
                    elif body.get("attachmentId"):
                        att_id = urllib.parse.quote(str(body["attachmentId"]), safe="")
                        response = self._get_json(f"{GMAIL_API}/messages/{urllib.parse.quote(gmail_id)}/attachments/{att_id}", access_token)
                        data = _b64url_decode(str(response.get("data", "")))
                    if len(data) > self.max_attachment_bytes:
                        skipped.append(f"{clean}: exceeds attachment size limit")
                    elif bytes_already_used + bytes_used + len(data) > self.max_sync_bytes:
                        skipped.append(f"{clean}: sync size limit reached")
                    elif data:
                        attachments.append({"name": clean, "data": base64.b64encode(data).decode("ascii")})
                        bytes_used += len(data)
            elif encoded and mime in bodies:
                text = _b64url_decode(encoded).decode("utf-8", errors="replace")
                bodies[mime].append(text)
            for child in part.get("parts", []) or []:
                walk(child)

        walk(payload)
        body = "\n".join(bodies["text/plain"]).strip()
        if not body:
            body = importer._html_to_text("\n".join(bodies["text/html"]))
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", gmail_id)[:70]
        return (
            {
                "email_id": f"gmail_{safe_id}",
                "gmail_id": gmail_id,
                "from": headers.get("from", ""),
                "subject": headers.get("subject", ""),
                "body": body,
                "attachments": attachments,
            },
            bytes_used,
            skipped,
        )

    def materialise(self, user_id: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix="navis_gmail_"))
        inbox, attachments_dir = root / "inbox", root / "attachments"
        inbox.mkdir()
        attachments_dir.mkdir()
        try:
            with self._connect() as con:
                rows = con.execute(
                    "SELECT gmail_id, payload_enc FROM gmail_messages WHERE user_id=? ORDER BY gmail_id", (user_id,)
                ).fetchall()
            for row in rows:
                record = json.loads(
                    self._decrypt(row["payload_enc"], f"message:{user_id}:{row['gmail_id']}").decode("utf-8")
                )
                email_id = record["email_id"]
                refs: list[str] = []
                for index, att in enumerate(record.get("attachments", []), 1):
                    clean = importer._clean_name(att["name"])
                    target = attachments_dir / f"{email_id}_{index}_{clean}"
                    target.write_bytes(base64.b64decode(att["data"], validate=True))
                    refs.append(f"attachments/{target.name}")
                email = {
                    "email_id": email_id,
                    "from": record.get("from", ""),
                    "subject": record.get("subject", ""),
                    "body": record.get("body", ""),
                    "attachments": refs,
                    "source": "gmail",
                }
                (inbox / f"{email_id}.json").write_text(json.dumps(email, ensure_ascii=False, indent=1), encoding="utf-8")
            self._rename_detected_documents(root)
            return root
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @staticmethod
    def _rename_detected_documents(root: Path) -> None:
        loader = InboxLoader(str(root))
        for path in sorted((root / "inbox").glob("*.json")):
            email = json.loads(path.read_text(encoding="utf-8"))
            changed, seen, new_refs = False, set(), []
            for ref in email.get("attachments", []):
                attachment = root / ref
                kind = loader.load_attachment(ref).detected_doc_type
                if kind in {"SI", "BL"} and kind not in seen:
                    seen.add(kind)
                    renamed = attachment.with_name(f"{email['email_id']}_{kind}{attachment.suffix.lower()}")
                    if not renamed.exists():
                        attachment.rename(renamed)
                        ref, changed = f"attachments/{renamed.name}", True
                new_refs.append(ref)
            if changed:
                email["attachments"] = new_refs
                path.write_text(json.dumps(email, ensure_ascii=False, indent=1), encoding="utf-8")

    def _post_form(self, url: str, values: Dict[str, str], expect_json: bool = True) -> Dict[str, Any]:
        data = urllib.parse.urlencode(values).encode("ascii")
        request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return self._request_json(request, expect_json)

    def _get_json(self, url: str, access_token: str) -> Dict[str, Any]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
        return self._request_json(request)

    @staticmethod
    def _request_json(request: urllib.request.Request, expect_json: bool = True) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                return json.loads(payload) if expect_json and payload else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("error", {})
                if isinstance(detail, dict):
                    detail = detail.get("message") or detail.get("error_description")
            except Exception:
                detail = None
            raise GmailServiceError(str(detail or "Google API request failed"), 502) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GmailServiceError("Could not reach Google; try again shortly", 503) from exc
