"""
api/gmail_ingest.py — FastAPI router for Gmail inbox listener integration.

Provides endpoints to:
  - Connect to Gmail via OAuth2
  - Start/stop background polling
  - View watcher status
  - Trigger manual polls

The background watcher thread polls Gmail at a configurable interval and
ingests new messages into a "gmail" dataset that the existing pipeline
processes automatically.
"""

import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("navisai.gmail.api")

ROOT = Path(__file__).resolve().parent.parent
DATASETS_DIR = ROOT / "datasets"

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
gmail_router = APIRouter(prefix="/api/gmail", tags=["gmail"])


# ---------------------------------------------------------------------------
# Watcher state — singleton
# ---------------------------------------------------------------------------
class _WatcherState:
    """Global state for the Gmail watcher background thread."""

    def __init__(self):
        self.watcher = None          # GmailWatcher instance
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.stop_event = threading.Event()
        self.poll_interval: int = int(os.environ.get("GMAIL_POLL_INTERVAL_SECONDS", "30"))
        self.last_poll: Optional[str] = None
        self.last_poll_count: int = 0
        self.total_ingested: int = 0
        self.errors: list = []
        self.dataset_id: Optional[str] = None
        self.dataset_ref = None      # Reference to Dataset object in main.DATASETS

    def reset(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.thread = None
        self.running = False
        self.stop_event = threading.Event()


_state = _WatcherState()


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------
class ConnectRequest(BaseModel):
    credentials_path: str = "credentials.json"


class CallbackRequest(BaseModel):
    auth_code: str


class StartRequest(BaseModel):
    poll_interval_seconds: int = 30
    label_filter: str = "INBOX"


class SimulateRequest(BaseModel):
    has_discrepancy: bool = False
    booking_ref: str = "MEDU-98241"
    shipper: str = "APRIL FAR EAST (M) SDN BHD"
    consignee: str = "AL GURG STATIONERY LLC"
    kind: str = "bl_comparison"  # "bl_comparison", "invoice_query", "spam"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@gmail_router.get("/status")
def gmail_status():
    """Get the current status of the Gmail watcher."""
    watcher_status = _state.watcher.status() if _state.watcher else {}
    return {
        "connected": _state.watcher is not None and _state.watcher.is_authenticated(),
        "watching": _state.running,
        "poll_interval_seconds": _state.poll_interval,
        "last_poll": _state.last_poll,
        "last_poll_count": _state.last_poll_count,
        "total_ingested": _state.total_ingested,
        "dataset_id": _state.dataset_id,
        "errors": _state.errors[-10:],  # last 10 errors
        **watcher_status,
    }


@gmail_router.post("/simulate")
def gmail_simulate(req: SimulateRequest):
    """Simulate an incoming email without requiring Google Cloud credentials.

    Allows hackathon judges and evaluators to test autonomous inbox ingestion,
    MIME unpacking, zero-shot intent triage, and 7-field compliance checks in 1 click.
    """
    from gmail_watcher import GmailWatcher, gmail_message_to_pipeline_json

    ds_id = _state.dataset_id or "gmail_live"
    ds_dir = DATASETS_DIR / ds_id
    (ds_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (ds_dir / "attachments").mkdir(parents=True, exist_ok=True)

    if not _state.watcher:
        _state.watcher = GmailWatcher(
            dataset_dir=ds_dir,
            credentials_path=ROOT / "credentials.json",
            token_path=ROOT / "token.json",
            state_path=ROOT / "gmail_state.json",
        )
    _state.dataset_id = ds_id
    _ensure_dataset_registered(ds_id, ds_dir)

    counter = _state.watcher._state.get("email_counter", 0) + 1
    _state.watcher._state["email_counter"] = counter
    email_id = f"gmail_{counter:03d}"
    gmail_id = f"sim_{base64.b16encode(os.urandom(8)).decode().lower()}"
    gmail_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    if req.kind == "invoice_query":
        sender = "billing@transocean-logistics.com"
        subject = f"Invoice clarification request for Booking {req.booking_ref}"
        body = (
            f"Dear Averis Operations,\n\n"
            f"We have issued invoice #INV-88391 for booking {req.booking_ref}. "
            f"Please review the origin terminal handling charge (THC) breakdown.\n\n"
            f"Kind regards,\nFinance & Billing Team"
        )
        record = gmail_message_to_pipeline_json(
            email_id=email_id,
            sender=sender,
            subject=subject,
            body=body,
            attachments=[],
            output_dir=ds_dir,
            gmail_id=gmail_id,
            gmail_date=gmail_date,
        )
        _state.watcher._state.setdefault("processed_ids", []).append(gmail_id)
        _state.watcher._save_state()

    elif req.kind == "spam":
        sender = "newsletter@freight-specials-promo.net"
        subject = "Exclusive freight discounts on transpacific routes - 40% OFF"
        body = (
            "Limited time shipping container freight discount rates for Asia-Europe and Transpacific lanes! "
            "Book now to claim 40% container haulage credits."
        )
        record = gmail_message_to_pipeline_json(
            email_id=email_id,
            sender=sender,
            subject=subject,
            body=body,
            attachments=[],
            output_dir=ds_dir,
            gmail_id=gmail_id,
            gmail_date=gmail_date,
        )
        _state.watcher._state.setdefault("processed_ids", []).append(gmail_id)
        _state.watcher._save_state()

    else:
        # Standard BL Comparison (with or without discrepancy)
        email_id = _state.watcher.simulate_incoming_email(
            has_discrepancy=req.has_discrepancy,
            booking_ref=req.booking_ref,
            shipper=req.shipper,
            consignee=req.consignee,
        )

    _state.total_ingested += 1
    _state.last_poll = datetime.utcnow().isoformat() + "Z"
    _state.last_poll_count = 1

    # Run sdoc verification pipeline
    _process_new_emails([email_id])

    result_data = None
    if _state.dataset_ref and email_id in _state.dataset_ref.cache:
        result_data = _state.dataset_ref.cache[email_id]

    return {
        "status": "ok",
        "email_id": email_id,
        "dataset_id": ds_id,
        "has_discrepancy": req.has_discrepancy,
        "kind": req.kind,
        "result": result_data,
        "message": f"Simulated email {email_id} ingested into {ds_id} and verified.",
    }


@gmail_router.post("/connect")
def gmail_connect(req: ConnectRequest):
    """Initiate connection to Gmail.

    If token.json exists, authenticates silently using the refresh token.
    If not, returns an auth URL for the user to visit.
    """
    from gmail_watcher import GmailWatcher, GmailAuthError

    creds_path = ROOT / req.credentials_path
    if not creds_path.is_file():
        raise HTTPException(
            400,
            f"credentials.json not found at {creds_path}. "
            "Download it from the Google Cloud Console: "
            "APIs & Services > Credentials > OAuth 2.0 Client IDs > Download JSON"
        )

    # Ensure dataset directory exists
    ds_id = _state.dataset_id or "gmail_live"
    ds_dir = DATASETS_DIR / ds_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    watcher = GmailWatcher(
        dataset_dir=ds_dir,
        credentials_path=creds_path,
        token_path=ROOT / "token.json",
        state_path=ROOT / "gmail_state.json",
    )

    token_path = ROOT / "token.json"
    if token_path.is_file():
        # Try silent auth with existing token
        try:
            watcher.authenticate()
            _state.watcher = watcher
            _state.dataset_id = ds_id
            _ensure_dataset_registered(ds_id, ds_dir)
            return {
                "status": "connected",
                "message": "Authenticated with existing token",
                "dataset_id": ds_id,
            }
        except GmailAuthError:
            pass  # Fall through to auth URL

    # No token or expired — provide auth URL
    try:
        auth_url = watcher.get_auth_url()
        _state.watcher = watcher
        _state.dataset_id = ds_id
        return {
            "status": "auth_required",
            "auth_url": auth_url,
            "message": "Visit the auth URL in your browser, authorize the app, then POST the code to /api/gmail/callback",
        }
    except Exception as e:
        raise HTTPException(500, f"Failed to generate auth URL: {e}")


@gmail_router.post("/callback")
def gmail_callback(req: CallbackRequest):
    """Complete OAuth2 flow with the authorization code from the browser."""
    from gmail_watcher import GmailAuthError

    if not _state.watcher:
        raise HTTPException(400, "Call /api/gmail/connect first")

    try:
        _state.watcher.authenticate_with_code(req.auth_code)
        ds_id = _state.dataset_id or "gmail_live"
        ds_dir = DATASETS_DIR / ds_id
        _ensure_dataset_registered(ds_id, ds_dir)
        return {
            "status": "connected",
            "message": "OAuth2 authentication complete",
            "dataset_id": ds_id,
        }
    except GmailAuthError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Auth callback failed: {e}")


@gmail_router.post("/start")
def gmail_start(req: StartRequest):
    """Start the background polling thread."""
    if not _state.watcher or not _state.watcher.is_authenticated():
        raise HTTPException(400, "Not connected to Gmail. Call /api/gmail/connect first.")

    if _state.running:
        return {"status": "already_running", "poll_interval_seconds": _state.poll_interval}

    # Update label filter if changed
    _state.watcher.label_filter = [lbl.strip() for lbl in req.label_filter.split(",")]
    _state.poll_interval = req.poll_interval_seconds
    _state.stop_event.clear()
    _state.running = True

    def _poll_loop():
        logger.info("Gmail watcher started (interval=%ds, labels=%s)",
                     _state.poll_interval, _state.watcher.label_filter)
        while not _state.stop_event.is_set():
            try:
                new_ids = _state.watcher.poll()
                _state.last_poll = datetime.utcnow().isoformat() + "Z"
                _state.last_poll_count = len(new_ids)
                _state.total_ingested += len(new_ids)

                # Trigger pipeline processing for new emails
                if new_ids:
                    _process_new_emails(new_ids)

            except Exception as e:
                error_msg = f"{datetime.utcnow().isoformat()}Z: {type(e).__name__}: {e}"
                logger.error("Poll error: %s", error_msg)
                _state.errors.append(error_msg)
                if len(_state.errors) > 100:
                    _state.errors = _state.errors[-50:]

            _state.stop_event.wait(timeout=_state.poll_interval)

        _state.running = False
        logger.info("Gmail watcher stopped")

    _state.thread = threading.Thread(target=_poll_loop, daemon=True, name="gmail-watcher")
    _state.thread.start()

    return {
        "status": "started",
        "poll_interval_seconds": _state.poll_interval,
        "label_filter": _state.watcher.label_filter,
        "dataset_id": _state.dataset_id,
    }


@gmail_router.post("/stop")
def gmail_stop():
    """Stop the background polling thread."""
    if not _state.running:
        return {"status": "not_running"}

    _state.stop_event.set()
    if _state.thread:
        _state.thread.join(timeout=10)
    _state.running = False
    return {"status": "stopped"}


@gmail_router.post("/poll")
def gmail_poll_now():
    """Trigger an immediate poll (regardless of whether the watcher is running)."""
    if not _state.watcher or not _state.watcher.is_authenticated():
        raise HTTPException(400, "Not connected to Gmail. Call /api/gmail/connect first.")

    try:
        new_ids = _state.watcher.poll()
        _state.last_poll = datetime.utcnow().isoformat() + "Z"
        _state.last_poll_count = len(new_ids)
        _state.total_ingested += len(new_ids)

        if new_ids:
            _process_new_emails(new_ids)

        return {
            "status": "ok",
            "new_emails": new_ids,
            "count": len(new_ids),
            "total_ingested": _state.total_ingested,
        }
    except Exception as e:
        raise HTTPException(500, f"Poll failed: {e}")


@gmail_router.post("/disconnect")
def gmail_disconnect():
    """Disconnect from Gmail and stop the watcher."""
    if _state.running:
        _state.stop_event.set()
        if _state.thread:
            _state.thread.join(timeout=10)
        _state.running = False

    _state.watcher = None
    token_path = ROOT / "token.json"
    if token_path.is_file():
        token_path.unlink()

    return {"status": "disconnected"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_dataset_registered(ds_id: str, ds_dir: Path) -> None:
    """Register the gmail dataset in the main DATASETS dict if not already there."""
    try:
        # Import here to avoid circular imports
        from api.main import DATASETS, Dataset

        if ds_id not in DATASETS:
            ds = Dataset(ds_id, "Gmail Live Inbox", ds_dir, kind="gmail")
            meta_path = ds_dir / "meta.json"
            meta_path.write_text(json.dumps({
                "name": "Gmail Live Inbox",
                "created": ds.created,
                "kind": "gmail",
            }), encoding="utf-8")
            DATASETS[ds_id] = ds
            _state.dataset_ref = ds
            logger.info("Registered gmail dataset: %s", ds_id)
        else:
            _state.dataset_ref = DATASETS[ds_id]

    except ImportError:
        logger.warning("Could not import from api.main — dataset registration skipped")


def _process_new_emails(new_email_ids: list) -> None:
    """Run the pipeline on newly ingested emails (non-blocking)."""
    try:
        from api.main import run_email

        ds_ref = _state.dataset_ref
        if ds_ref is None:
            logger.warning("No dataset reference — skipping pipeline processing")
            return

        # Reload the dataset's loader to pick up new files
        from sdoc_loader import InboxLoader
        ds_ref.loader = InboxLoader(str(ds_ref.root))

        for eid in new_email_ids:
            try:
                result = run_email(ds_ref, eid)
                logger.info(
                    "Processed %s: category=%s status=%s",
                    eid, result.get("category"), result.get("status")
                )
            except Exception as e:
                logger.error("Failed to process %s: %s", eid, e)
                error_msg = f"Processing {eid}: {type(e).__name__}: {e}"
                _state.errors.append(error_msg)

    except ImportError as e:
        logger.warning("Pipeline import failed: %s", e)
