"""
gmail_watcher.py — Gmail API Client for NavisAI Autonomous Inbox Listener.

Connects to a real Gmail inbox via OAuth2, polls for new messages using
the efficient history-based delta API, downloads bodies and attachments,
and converts everything into the pipeline's native inbox/*.json + attachments/
layout so the existing sdoc_* pipeline can process them unchanged.

Setup:
  1. Create a Google Cloud project and enable the Gmail API.
  2. Create OAuth 2.0 Desktop credentials → download as credentials.json
  3. Place credentials.json in the repo root.
  4. On first run, a browser window opens for consent → token.json is saved.

Usage:
  watcher = GmailWatcher(dataset_dir="datasets/gmail_live")
  watcher.authenticate()           # Opens browser on first run
  new_ids = watcher.poll()         # Returns list of new email IDs ingested
"""

import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logger = logging.getLogger("navisai.gmail")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DEFAULT_CREDENTIALS = ROOT / "credentials.json"
DEFAULT_TOKEN = ROOT / "token.json"
DEFAULT_STATE = ROOT / "gmail_state.json"

# Gmail API scopes — readonly by default, modify if labelling is desired
SCOPES_READONLY = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_MODIFY = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Attachment extensions we care about
ATTACHMENT_EXT = {".txt", ".pdf", ".docx", ".xlsx", ".xls", ".doc", ".csv"}

# Max attachments per message to download (safety valve)
MAX_ATTACHMENTS_PER_MSG = 20
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024  # 50 MB per file


# ---------------------------------------------------------------------------
# Gmail Watcher
# ---------------------------------------------------------------------------

class GmailAuthError(Exception):
    """Raised when OAuth2 credentials are missing or invalid."""
    pass


class GmailWatcher:
    """Polls a Gmail inbox and writes new messages into the sdoc_* pipeline format.

    Parameters
    ----------
    dataset_dir : str or Path
        Root directory for the dataset being built (contains inbox/ and attachments/).
    credentials_path : str or Path
        Path to the OAuth2 client secrets file (credentials.json).
    token_path : str or Path
        Path where the user's access/refresh token is persisted.
    state_path : str or Path
        Path for the watcher's persistent state (historyId, processed set).
    label_filter : list[str]
        Gmail label IDs to watch (default: ["INBOX"]).
    scopes : list[str]
        OAuth2 scopes. Use SCOPES_MODIFY if you want to label processed emails.
    """

    def __init__(
        self,
        dataset_dir: str | Path,
        credentials_path: str | Path = DEFAULT_CREDENTIALS,
        token_path: str | Path = DEFAULT_TOKEN,
        state_path: str | Path = DEFAULT_STATE,
        label_filter: Optional[List[str]] = None,
        query_filter: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ):
        self.dataset_dir = Path(dataset_dir)
        self.inbox_dir = self.dataset_dir / "inbox"
        self.attachments_dir = self.dataset_dir / "attachments"
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.state_path = Path(state_path)
        self.label_filter = label_filter or ["INBOX"]
        self.query_filter = query_filter
        self.scopes = scopes or SCOPES_READONLY

        self._service = None
        self._state: Dict[str, Any] = {"history_id": None, "processed_ids": [], "email_counter": 0}
        self._load_state()

        # Ensure directories exist
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _kv_key(self) -> str:
        return f"gmail_state:{self.dataset_dir.name}"

    def _load_state(self) -> None:
        """Load watcher state: from Supabase when it is configured, otherwise from disk."""
        try:
            import sdoc_store

            stored = sdoc_store.kv_get(self._kv_key()) if sdoc_store.get_store() is not None else None
        except Exception as e:
            logger.warning("Failed to load stored state: %s", e)
            stored = None
        if isinstance(stored, dict):
            self._state["history_id"] = stored.get("history_id")
            self._state["processed_ids"] = stored.get("processed_ids", [])
            self._state["email_counter"] = stored.get("email_counter", 0)
            return
        if self.state_path.is_file():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._state["history_id"] = data.get("history_id")
                self._state["processed_ids"] = data.get("processed_ids", [])
                self._state["email_counter"] = data.get("email_counter", 0)
                logger.info("Loaded state: historyId=%s, %d processed",
                            self._state["history_id"], len(self._state["processed_ids"]))
            except Exception as e:
                logger.warning("Failed to load state: %s", e)

    def _save_state(self) -> None:
        """Persist watcher state to Supabase (when configured) and to disk."""
        try:
            import sdoc_store

            if sdoc_store.get_store() is not None:
                sdoc_store.kv_set(self._kv_key(), self._state)
        except Exception as e:
            logger.error("Failed to store state: %s", e)
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save state: %s", e)

    @property
    def history_id(self) -> Optional[str]:
        return self._state.get("history_id")

    @property
    def processed_count(self) -> int:
        return len(self._state.get("processed_ids", []))

    # ------------------------------------------------------------------
    # OAuth2 Authentication
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Check if we have valid credentials."""
        return self._service is not None

    def authenticate(self) -> None:
        """Authenticate with Gmail API using OAuth2.

        On first run, opens a browser for user consent.
        On subsequent runs, uses the stored refresh token.
        """
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as e:
            raise GmailAuthError(
                f"Missing Google API libraries: {e}. "
                "Install with: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            ) from e

        creds = None

        # 1. Try loading existing token
        if self.token_path.is_file():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), self.scopes)
            except Exception:
                creds = None

        # 2. Refresh or re-authenticate
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Refreshed OAuth2 token")
            except Exception as e:
                logger.warning("Token refresh failed: %s — re-authenticating", e)
                creds = None

        if not creds or not creds.valid:
            if not self.credentials_path.is_file():
                raise GmailAuthError(
                    f"credentials.json not found at {self.credentials_path}. "
                    "Download it from the Google Cloud Console (APIs & Services > Credentials > OAuth 2.0 Client IDs)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), self.scopes)
            creds = flow.run_local_server(port=0)
            logger.info("Completed OAuth2 consent flow")

        # 3. Save token for next time
        self.token_path.write_text(creds.to_json(), encoding="utf-8")

        # 4. Build the Gmail service
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        logger.info("Gmail API service built successfully")

        # 5. Initialize history ID if needed
        if not self._state["history_id"]:
            profile = self._service.users().getProfile(userId="me").execute()
            self._state["history_id"] = profile["historyId"]
            self._save_state()
            logger.info("Initialized historyId to %s", self._state["history_id"])

    def authenticate_with_code(self, auth_code: str) -> None:
        """Complete OAuth2 flow using an authorization code (for headless/API use).

        Used when the frontend captures the auth code from the redirect.
        """
        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as e:
            raise GmailAuthError(f"Missing Google API libraries: {e}") from e

        if not self.credentials_path.is_file():
            raise GmailAuthError(f"credentials.json not found at {self.credentials_path}")

        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), self.scopes)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        flow.fetch_token(code=auth_code)
        creds = flow.credentials

        self.token_path.write_text(creds.to_json(), encoding="utf-8")
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        if not self._state["history_id"]:
            profile = self._service.users().getProfile(userId="me").execute()
            self._state["history_id"] = profile["historyId"]
            self._save_state()

    def get_auth_url(self) -> str:
        """Generate the OAuth2 authorization URL for manual/headless flow."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.credentials_path.is_file():
            raise GmailAuthError(f"credentials.json not found at {self.credentials_path}")

        flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), self.scopes)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = flow.authorization_url(prompt="consent")
        return auth_url

    # ------------------------------------------------------------------
    # Polling — fetch new messages since last historyId
    # ------------------------------------------------------------------

    def poll(self) -> List[str]:
        """Poll Gmail for new messages and ingest them into the dataset.

        Returns a list of pipeline email IDs (e.g. ["gmail_001", "gmail_002"]).
        """
        if not self._service:
            raise GmailAuthError("Not authenticated. Call authenticate() first.")

        new_msg_ids = self._get_new_message_ids()
        if not new_msg_ids:
            logger.debug("No new messages")
            return []

        ingested = []
        for gmail_id in new_msg_ids:
            if gmail_id in self._state["processed_ids"]:
                continue
            try:
                eid = self._ingest_message(gmail_id)
                if eid:
                    ingested.append(eid)
                    self._state["processed_ids"].append(gmail_id)
            except Exception as e:
                logger.error("Failed to ingest message %s: %s", gmail_id, e, exc_info=True)

        self._save_state()
        if ingested:
            logger.info("Ingested %d new email(s): %s", len(ingested), ingested)
        return ingested

    def _get_new_message_ids(self) -> List[str]:
        """Use the history API to find new message IDs since last poll."""
        history_id = self._state.get("history_id")

        if not history_id:
            # First poll — do a full listing of recent inbox messages
            return self._full_inbox_scan()

        try:
            new_ids = []
            page_token = None

            while True:
                kwargs: Dict[str, Any] = {
                    "userId": "me",
                    "startHistoryId": history_id,
                    "historyTypes": "messageAdded",
                    "labelId": self.label_filter[0] if self.label_filter else "INBOX",
                }
                if page_token:
                    kwargs["pageToken"] = page_token

                response = self._service.users().history().list(**kwargs).execute()

                for record in response.get("history", []):
                    for item in record.get("messagesAdded", []):
                        msg = item.get("message", {})
                        msg_id = msg.get("id")
                        # Only include if it has one of our target labels
                        msg_labels = msg.get("labelIds", [])
                        if msg_id and any(lbl in msg_labels for lbl in self.label_filter):
                            new_ids.append(msg_id)

                # Update history ID to the latest
                if "historyId" in response:
                    self._state["history_id"] = response["historyId"]

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

            return list(dict.fromkeys(new_ids))  # deduplicate preserving order

        except Exception as e:
            err_str = str(e)
            if "404" in err_str or "historyId" in err_str.lower():
                logger.warning("History ID expired, falling back to full scan")
                return self._full_inbox_scan()
            raise

    def _full_inbox_scan(self, max_results: int = 50) -> List[str]:
        """List recent inbox messages (fallback when historyId is stale)."""
        base_query = " ".join(f"label:{lbl}" for lbl in self.label_filter) if self.label_filter else "in:inbox"
        query = f"({base_query}) {self.query_filter}" if self.query_filter else base_query
        response = self._service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()

        messages = response.get("messages", [])

        # Update history ID from profile
        profile = self._service.users().getProfile(userId="me").execute()
        self._state["history_id"] = profile["historyId"]

        return [m["id"] for m in messages]

    # ------------------------------------------------------------------
    # Message ingestion — fetch a single message and write to disk
    # ------------------------------------------------------------------

    def _ingest_message(self, gmail_msg_id: str) -> Optional[str]:
        """Fetch a Gmail message and write it as pipeline-compatible JSON + attachments.

        Returns the pipeline email ID (e.g. "gmail_017") or None if skipped.
        """
        # Fetch full message including body and attachment metadata
        msg = self._service.users().messages().get(
            userId="me", id=gmail_msg_id, format="full"
        ).execute()

        # Extract headers
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        date_str = headers.get("date", "")

        # Extract body text
        body = self._extract_body(msg.get("payload", {}))

        # Download attachments
        attachment_refs = []
        self._state["email_counter"] += 1
        counter = self._state["email_counter"]
        eid = f"gmail_{counter:03d}"

        parts = self._collect_parts(msg.get("payload", {}))
        att_index = 0
        for part in parts:
            filename = part.get("filename", "")
            if not filename:
                continue
            ext = Path(filename).suffix.lower()
            if ext not in ATTACHMENT_EXT:
                continue
            if att_index >= MAX_ATTACHMENTS_PER_MSG:
                break

            att_data = self._download_attachment(gmail_msg_id, part)
            if not att_data:
                continue
            if len(att_data) > MAX_ATTACHMENT_BYTES:
                logger.warning("Attachment %s too large (%d bytes), skipping", filename, len(att_data))
                continue

            # Determine SI/BL naming by content detection
            safe_name = self._safe_filename(filename)
            doc_type = self._detect_doc_type_from_bytes(att_data, ext, safe_name)
            if doc_type in ("SI", "BL"):
                att_filename = f"{eid}_{doc_type}{ext}"
            else:
                att_filename = f"{eid}_{safe_name}"

            att_path = self.attachments_dir / att_filename
            att_path.write_bytes(att_data)
            attachment_refs.append(f"attachments/{att_filename}")
            att_index += 1

        # Write inbox JSON
        email_record = {
            "email_id": eid,
            "from": sender,
            "subject": subject,
            "body": body,
            "attachments": attachment_refs,
            "gmail_id": gmail_msg_id,
            "gmail_date": date_str,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

        email_path = self.inbox_dir / f"{eid}.json"
        email_path.write_text(
            json.dumps(email_record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info("Ingested %s: '%s' from %s (%d attachments)",
                     eid, subject[:60], sender[:40], len(attachment_refs))
        return eid

    # ------------------------------------------------------------------
    # Body extraction — handles multipart MIME
    # ------------------------------------------------------------------

    def _extract_body(self, payload: Dict[str, Any]) -> str:
        """Extract plain text body from a Gmail message payload.

        Handles multipart/alternative and multipart/mixed structures.
        Falls back to HTML→text conversion if no plain text part exists.
        """
        mime_type = payload.get("mimeType", "")

        # Direct text part
        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            return self._decode_base64url(data)

        # Multipart — recurse into parts
        parts = payload.get("parts", [])
        if parts:
            # Prefer text/plain
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    return self._decode_base64url(data)

            # Fall back to text/html, convert to text
            for part in parts:
                if part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    html = self._decode_base64url(data)
                    return self._html_to_text(html)

            # Recurse into nested multipart
            for part in parts:
                if part.get("mimeType", "").startswith("multipart/"):
                    result = self._extract_body(part)
                    if result:
                        return result

        # Direct HTML (non-multipart)
        if mime_type == "text/html":
            data = payload.get("body", {}).get("data", "")
            html = self._decode_base64url(data)
            return self._html_to_text(html)

        return ""

    # ------------------------------------------------------------------
    # Attachment handling
    # ------------------------------------------------------------------

    def _collect_parts(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Recursively collect all MIME parts that have a filename (attachments)."""
        results = []
        if payload.get("filename"):
            results.append(payload)
        for part in payload.get("parts", []):
            results.extend(self._collect_parts(part))
        return results

    def _download_attachment(self, msg_id: str, part: Dict[str, Any]) -> Optional[bytes]:
        """Download attachment data from Gmail API."""
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")

        if attachment_id:
            # Large attachment — separate download required
            att_resp = self._service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=attachment_id
            ).execute()
            data = att_resp.get("data", "")
            return base64.urlsafe_b64decode(data) if data else None
        else:
            # Inline attachment — data is in the body
            data = body.get("data", "")
            return base64.urlsafe_b64decode(data) if data else None

    def _detect_doc_type_from_bytes(self, data: bytes, ext: str, filename: str) -> str:
        """Detect whether an attachment is an SI or BL document by its content.

        Uses the same heuristics as sdoc_loader._detect_doc_type but operates
        on raw bytes since we haven't written the file yet.
        """
        # Check filename first
        fn_upper = filename.upper()
        if "_SI" in fn_upper or "SI_" in fn_upper or fn_upper.startswith("SI"):
            return "SI"
        if "_BL" in fn_upper or "BL_" in fn_upper or fn_upper.startswith("BL") or "DRAFT" in fn_upper:
            return "BL"

        # For text files, check content
        if ext in (".txt", ".csv"):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = data.decode("latin-1", errors="replace")
            return self._classify_text(text)

        return "UNKNOWN"

    @staticmethod
    def _classify_text(text: str) -> str:
        """Classify text content as SI, BL, or UNKNOWN."""
        upper = text.upper()
        si_signals = ["SHIPPING INSTRUCTION", "SHIPPER'S INSTRUCTION", "SI DETAILS"]
        bl_signals = ["BILL OF LADING", "DRAFT B/L", "DRAFT BL", "B/L NO", "BL NUMBER"]

        si_score = sum(1 for s in si_signals if s in upper)
        bl_score = sum(1 for s in bl_signals if s in upper)

        if si_score > bl_score:
            return "SI"
        if bl_score > si_score:
            return "BL"
        return "UNKNOWN"

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_base64url(data: str) -> str:
        """Decode base64url-encoded string (Gmail's encoding)."""
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Simple HTML to text conversion."""
        import re as _re
        # Remove script/style
        text = _re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
        # Convert line-break elements
        text = _re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
        # Strip remaining tags
        text = _re.sub(r"<[^>]+>", "", text)
        # Decode entities
        try:
            import html as html_mod
            text = html_mod.unescape(text)
        except ImportError:
            pass
        return text.strip()

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Sanitize a filename for safe filesystem use."""
        # Remove path components
        name = Path(name).name
        # Replace unsafe chars
        name = re.sub(r"[^\w.\-]", "_", name).strip("._") or "attachment"
        return name[:120]

    # ------------------------------------------------------------------
    # Status reporting
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return current watcher status for the API."""
        inbox_files = list(self.inbox_dir.glob("*.json")) if self.inbox_dir.is_dir() else []
        return {
            "authenticated": self._service is not None,
            "history_id": self._state.get("history_id"),
            "processed_gmail_ids": len(self._state.get("processed_ids", [])),
            "pipeline_emails": len(inbox_files),
            "email_counter": self._state.get("email_counter", 0),
            "dataset_dir": str(self.dataset_dir),
            "label_filter": self.label_filter,
            "query_filter": self.query_filter,
        }

    # ------------------------------------------------------------------
    # Simulation mode — for judges and offline evaluation
    # ------------------------------------------------------------------

    def simulate_incoming_email(
        self,
        has_discrepancy: bool = False,
        booking_ref: str = "MEDU-98241",
        shipper: str = "APRIL FAR EAST (M) SDN BHD",
        consignee: str = "AL GURG STATIONERY LLC",
    ) -> str:
        """Simulate an incoming email arriving in the Gmail inbox.

        Allows hackathon judges and evaluators to test the entire end-to-end flow
        (ingestion -> parsing -> SI/BL classification -> pipeline trigger)
        without requiring Google Cloud OAuth credentials or test user access.
        """
        self._state["email_counter"] = self._state.get("email_counter", 0) + 1
        counter = self._state["email_counter"]
        email_id = f"gmail_{counter:03d}"

        # SI document content
        si_text = f"""SHIPPING INSTRUCTION
Booking No: {booking_ref}
Shipper: {shipper}
Consignee: {consignee}
Notify Party: SAME AS CONSIGNEE
Port of Loading: PORT KLANG, MALAYSIA
Port of Discharge: JEBEL ALI, UAE
Container Count: 10 x 40'HC
Gross Weight: 245,000.00 KGS
Description: PRIME BLEACHED HARDWOOD KRAFT PULP
"""

        # BL document content (clean match or with deliberate discrepancy for demo)
        bl_wt = "231,000.00 KGS" if has_discrepancy else "245,000.00 KGS"
        bl_cnt = "8 x 40'HC" if has_discrepancy else "10 x 40'HC"
        bl_text = f"""BILL OF LADING - DRAFT
B/L No: MSCU{booking_ref.replace('-', '')}
Shipper: {shipper}
Consignee: {consignee}
Notify Party: {consignee}
Port of Loading: PORT KELANG
Port of Discharge: JEBEL ALI, UAE
Total Containers: {bl_cnt}
Total Gross Weight: {bl_wt}
Cargo: Hardwood Kraft Pulp in bales
"""

        attachments = [
            (f"{booking_ref}_SI.txt", si_text.encode("utf-8")),
            (f"{booking_ref}_Draft_BL.txt", bl_text.encode("utf-8")),
        ]

        sender = "operations@medlog-shipping.com"
        subject = f"Draft B/L for Booking {booking_ref} - Review Requested"
        body = f"Dear Averis Trade Team,\n\nPlease review attached SI and Draft B/L for Booking {booking_ref}.\nConfirm approval before vessel cut-off.\n\nBest regards,\nMedlog Documentation Desk"

        gmail_id = f"sim_{base64.b16encode(os.urandom(8)).decode().lower()}"
        gmail_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

        record = gmail_message_to_pipeline_json(
            email_id=email_id,
            sender=sender,
            subject=subject,
            body=body,
            attachments=attachments,
            output_dir=self.dataset_dir,
            gmail_id=gmail_id,
            gmail_date=gmail_date,
        )

        self._state["processed_ids"].append(gmail_id)
        self._save_state()
        logger.info("Simulated incoming email ingested as %s", email_id)
        return email_id


# ---------------------------------------------------------------------------
# Standalone conversion utility — for testing without Gmail API
# ---------------------------------------------------------------------------

def gmail_message_to_pipeline_json(
    email_id: str,
    sender: str,
    subject: str,
    body: str,
    attachments: List[Tuple[str, bytes]],
    output_dir: Path,
    gmail_id: str = "",
    gmail_date: str = "",
) -> Dict[str, Any]:
    """Convert a raw email into the pipeline's JSON format and write to disk.

    This is the pure-logic function used by both GmailWatcher and tests.

    Parameters
    ----------
    email_id : str   Pipeline email ID (e.g. "gmail_001")
    sender : str     From address
    subject : str    Email subject
    body : str       Plain text body
    attachments : list of (filename, bytes) tuples
    output_dir : Path  Dataset root (must contain inbox/ and attachments/)
    gmail_id : str   Original Gmail message ID
    gmail_date : str Original date header

    Returns
    -------
    dict  The email record that was written to inbox/{email_id}.json
    """
    inbox_dir = output_dir / "inbox"
    attach_dir = output_dir / "attachments"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    attach_dir.mkdir(parents=True, exist_ok=True)

    att_refs = []
    for filename, data in attachments:
        safe = re.sub(r"[^\w.\-]", "_", Path(filename).name).strip("._") or "attachment"
        safe = safe[:120]
        att_filename = f"{email_id}_{safe}"
        (attach_dir / att_filename).write_bytes(data)
        att_refs.append(f"attachments/{att_filename}")

    record = {
        "email_id": email_id,
        "from": sender,
        "subject": subject,
        "body": body,
        "attachments": att_refs,
        "gmail_id": gmail_id,
        "gmail_date": gmail_date,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }

    (inbox_dir / f"{email_id}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return record


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="NavisAI Autonomous Gmail Inbox Listener")
    parser.add_argument("--dataset", default="datasets/gmail_live", help="Dataset directory path")
    parser.add_argument("--credentials", default=str(DEFAULT_CREDENTIALS), help="Path to credentials.json")
    parser.add_argument("--token", default=str(DEFAULT_TOKEN), help="Path to token.json")
    parser.add_argument("--state", default=str(DEFAULT_STATE), help="Path to gmail_state.json")
    parser.add_argument("--label", action="append", help="Gmail label(s) to monitor (default: INBOX)")
    parser.add_argument("--query", help="Search query filter (e.g. 'to:ops@domain.com')")
    parser.add_argument("--poll-once", action="store_true", help="Perform a single poll pass and exit")
    parser.add_argument("--watch", action="store_true", help="Run in continuous background polling mode")
    parser.add_argument("--interval", type=int, default=30, help="Polling interval in seconds (default: 30)")
    parser.add_argument("--trigger-pipeline", action="store_true", help="Automatically trigger sdoc_pipeline.py when new emails arrive")
    parser.add_argument("--simulate", action="store_true", help="Simulate an incoming email without requiring Google Cloud credentials")
    parser.add_argument("--discrepancy", action="store_true", help="When simulating, inject a deliberate discrepancy for verification testing")
    parser.add_argument("--status", action="store_true", help="Show current watcher status and exit")

    args = parser.parse_args()

    watcher = GmailWatcher(
        dataset_dir=args.dataset,
        credentials_path=args.credentials,
        token_path=args.token,
        state_path=args.state,
        label_filter=args.label,
        query_filter=args.query,
    )

    if args.status:
        print(json.dumps(watcher.status(), indent=2))
        return

    def _handle_new_emails(email_ids: List[str]):
        if not email_ids:
            return
        print(f"📥 Ingested {len(email_ids)} new email(s): {', '.join(email_ids)}")
        if args.trigger_pipeline:
            print("🚀 Auto-triggering NavisAI compliance verification pipeline...")
            try:
                from sdoc_pipeline import run_pipeline
                output_json = Path(args.dataset) / "submission.json"
                run_pipeline(args.dataset, str(output_json))
                print(f"✨ Pipeline completed. Results saved to {output_json}")
            except Exception as e:
                print(f"❌ Pipeline run failed: {e}")

    if args.simulate:
        print("📨 [Simulation Mode] Simulating incoming operational email from ocean carrier...")
        email_id = watcher.simulate_incoming_email(has_discrepancy=args.discrepancy)
        _handle_new_emails([email_id])
        return

    if not args.poll_once and not args.watch:
        parser.print_help()
        return

    print("🔑 Authenticating with Gmail API...")
    watcher.authenticate()
    print("✅ Authenticated successfully!")

    if args.poll_once:
        print("🔍 Polling Gmail for new messages...")
        new_ids = watcher.poll()
        if new_ids:
            _handle_new_emails(new_ids)
        else:
            print("✨ No new messages found.")
        return

    if args.watch:
        print(f"👀 Watching Gmail inbox every {args.interval}s (Press Ctrl+C to stop)...")
        try:
            while True:
                new_ids = watcher.poll()
                if new_ids:
                    _handle_new_emails(new_ids)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n🛑 Watcher stopped by user.")


if __name__ == "__main__":
    main()
