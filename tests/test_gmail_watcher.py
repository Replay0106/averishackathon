"""
tests/test_gmail_watcher.py — Unit tests for the Gmail inbox listener.

Tests the pure-logic functions (no Gmail API calls required):
  - Message-to-pipeline JSON conversion
  - Attachment extraction and naming
  - Document type detection from content
  - State persistence
  - HTML-to-text conversion
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gmail_watcher import GmailWatcher, gmail_message_to_pipeline_json


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dataset(tmp_path):
    """Create a temporary dataset directory structure."""
    ds = tmp_path / "test_dataset"
    (ds / "inbox").mkdir(parents=True)
    (ds / "attachments").mkdir(parents=True)
    return ds


@pytest.fixture
def temp_state_file(tmp_path):
    """Create a temporary state file path."""
    return tmp_path / "gmail_state.json"


# ---------------------------------------------------------------------------
# Tests: gmail_message_to_pipeline_json
# ---------------------------------------------------------------------------

class TestMessageConversion:
    """Test the pure conversion function that writes emails to disk."""

    def test_basic_conversion(self, temp_dataset):
        """Convert a simple email with no attachments."""
        record = gmail_message_to_pipeline_json(
            email_id="gmail_001",
            sender="docs@example.com",
            subject="SI and BL for review",
            body="Please check the attached documents.",
            attachments=[],
            output_dir=temp_dataset,
            gmail_id="abc123",
            gmail_date="Mon, 21 Sep 2026 10:00:00 +0000",
        )

        assert record["email_id"] == "gmail_001"
        assert record["from"] == "docs@example.com"
        assert record["subject"] == "SI and BL for review"
        assert record["body"] == "Please check the attached documents."
        assert record["attachments"] == []
        assert record["gmail_id"] == "abc123"

        # Verify file was written
        json_path = temp_dataset / "inbox" / "gmail_001.json"
        assert json_path.is_file()
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        assert saved["email_id"] == "gmail_001"

    def test_conversion_with_attachments(self, temp_dataset):
        """Convert an email with SI and BL attachments."""
        si_content = b"SHIPPING INSTRUCTION\nShipper: Test Corp\nConsignee: Buyer Ltd"
        bl_content = b"BILL OF LADING\nDraft B/L No: MEDU1234567"

        record = gmail_message_to_pipeline_json(
            email_id="gmail_002",
            sender="shipping@cargo.com",
            subject="Draft BL comparison request",
            body="Attached are the SI and draft BL.",
            attachments=[
                ("SI_document.txt", si_content),
                ("Draft_BL.txt", bl_content),
            ],
            output_dir=temp_dataset,
        )

        assert len(record["attachments"]) == 2
        assert all(a.startswith("attachments/gmail_002_") for a in record["attachments"])

        # Verify attachment files exist
        for ref in record["attachments"]:
            path = temp_dataset / ref
            assert path.is_file()
            assert path.stat().st_size > 0

    def test_unsafe_filename_sanitization(self, temp_dataset):
        """Filenames with special characters should be sanitized."""
        record = gmail_message_to_pipeline_json(
            email_id="gmail_003",
            sender="test@test.com",
            subject="Test",
            body="Test email",
            attachments=[
                ("../../malicious file (1).pdf", b"fake pdf content"),
                ("normal_doc.txt", b"text content"),
            ],
            output_dir=temp_dataset,
        )

        assert len(record["attachments"]) == 2
        # No path traversal in refs
        for ref in record["attachments"]:
            assert ".." not in ref
            assert ref.startswith("attachments/gmail_003_")

    def test_creates_directories(self, tmp_path):
        """Should create inbox/ and attachments/ if they don't exist."""
        ds = tmp_path / "new_dataset"
        record = gmail_message_to_pipeline_json(
            email_id="gmail_004",
            sender="test@test.com",
            subject="Test",
            body="Body",
            attachments=[],
            output_dir=ds,
        )

        assert (ds / "inbox").is_dir()
        assert (ds / "attachments").is_dir()
        assert (ds / "inbox" / "gmail_004.json").is_file()


# ---------------------------------------------------------------------------
# Tests: Document type detection
# ---------------------------------------------------------------------------

class TestDocTypeDetection:
    """Test the text-based document type classifier."""

    def test_si_detection(self):
        text = "SHIPPING INSTRUCTION for OC 5ALT-01226, Shipper: Acme Corp"
        assert GmailWatcher._classify_text(text) == "SI"

    def test_bl_detection(self):
        text = "DRAFT BILL OF LADING No: MEDU1234567\nPort of Loading: SGSIN"
        assert GmailWatcher._classify_text(text) == "BL"

    def test_unknown_detection(self):
        text = "Dear team, please find the invoice attached."
        assert GmailWatcher._classify_text(text) == "UNKNOWN"

    def test_si_vs_bl_tie(self):
        # When both signal counts are equal, classifier returns UNKNOWN (ambiguous)
        text = "SHIPPING INSTRUCTION with reference to BILL OF LADING draft"
        result = GmailWatcher._classify_text(text)
        assert result == "UNKNOWN"


# ---------------------------------------------------------------------------
# Tests: HTML to text conversion
# ---------------------------------------------------------------------------

class TestHtmlToText:
    """Test HTML→plaintext conversion used for email bodies."""

    def test_basic_html(self):
        html = "<p>Hello <b>World</b></p><p>Second paragraph</p>"
        text = GmailWatcher._html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "Second paragraph" in text

    def test_br_tags(self):
        html = "Line 1<br>Line 2<br/>Line 3"
        text = GmailWatcher._html_to_text(html)
        assert "Line 1" in text
        assert "Line 2" in text

    def test_script_removal(self):
        html = "<p>Text</p><script>alert('xss')</script><p>More text</p>"
        text = GmailWatcher._html_to_text(html)
        assert "alert" not in text
        assert "Text" in text
        assert "More text" in text

    def test_html_entities(self):
        html = "<p>Price: &lt;$100 &amp; free shipping</p>"
        text = GmailWatcher._html_to_text(html)
        assert "<$100" in text
        assert "& free shipping" in text


# ---------------------------------------------------------------------------
# Tests: State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    """Test state save/load for surviving restarts."""

    def test_save_and_load_state(self, temp_dataset, tmp_path):
        state_path = tmp_path / "state.json"

        # Create watcher and set state
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "fake_creds.json",
            token_path=tmp_path / "fake_token.json",
            state_path=state_path,
        )
        watcher._state["history_id"] = "12345"
        watcher._state["processed_ids"] = ["msg_a", "msg_b"]
        watcher._state["email_counter"] = 42
        watcher._save_state()

        # Verify file exists
        assert state_path.is_file()

        # Create new watcher and verify state was loaded
        watcher2 = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "fake_creds.json",
            token_path=tmp_path / "fake_token.json",
            state_path=state_path,
        )
        assert watcher2._state["history_id"] == "12345"
        assert watcher2._state["processed_ids"] == ["msg_a", "msg_b"]
        assert watcher2._state["email_counter"] == 42

    def test_missing_state_file(self, temp_dataset, tmp_path):
        """Watcher should work fine with no prior state."""
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "fake_creds.json",
            token_path=tmp_path / "fake_token.json",
            state_path=tmp_path / "nonexistent_state.json",
        )
        assert watcher._state["history_id"] is None
        assert watcher._state["processed_ids"] == []
        assert watcher._state["email_counter"] == 0

    def test_corrupted_state_file(self, temp_dataset, tmp_path):
        """Corrupted state file should not crash the watcher."""
        state_path = tmp_path / "bad_state.json"
        state_path.write_text("NOT VALID JSON {{{{", encoding="utf-8")

        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "fake_creds.json",
            token_path=tmp_path / "fake_token.json",
            state_path=state_path,
        )
        # Should fall back to defaults
        assert watcher._state["history_id"] is None


# ---------------------------------------------------------------------------
# Tests: Filename sanitization
# ---------------------------------------------------------------------------

class TestFilenameSanitization:
    """Test the safe filename utility."""

    def test_normal_filename(self):
        assert GmailWatcher._safe_filename("document.pdf") == "document.pdf"

    def test_spaces_and_parens(self):
        result = GmailWatcher._safe_filename("My Document (1).pdf")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result
        assert result.endswith(".pdf")

    def test_path_traversal(self):
        result = GmailWatcher._safe_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result

    def test_very_long_filename(self):
        long_name = "a" * 200 + ".pdf"
        result = GmailWatcher._safe_filename(long_name)
        assert len(result) <= 120

    def test_empty_filename(self):
        result = GmailWatcher._safe_filename("")
        assert result == "attachment"


# ---------------------------------------------------------------------------
# Tests: Watcher initialization
# ---------------------------------------------------------------------------

class TestWatcherInit:
    """Test GmailWatcher initialization and configuration."""

    def test_default_config(self, temp_dataset, tmp_path):
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
        )
        assert watcher.label_filter == ["INBOX"]
        assert not watcher.is_authenticated()
        assert watcher.processed_count == 0

    def test_custom_labels(self, temp_dataset, tmp_path):
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
            label_filter=["INBOX", "Label_123"],
        )
        assert watcher.label_filter == ["INBOX", "Label_123"]

    def test_custom_query_filter(self, temp_dataset, tmp_path):
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
            query_filter="to:shipping-ops@averis.com",
        )
        assert watcher.query_filter == "to:shipping-ops@averis.com"
        assert watcher.status()["query_filter"] == "to:shipping-ops@averis.com"

    def test_status_report(self, temp_dataset, tmp_path):
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
        )
        status = watcher.status()
        assert status["authenticated"] is False
        assert status["pipeline_emails"] == 0
        assert status["label_filter"] == ["INBOX"]

    def test_directory_creation(self, tmp_path):
        """Watcher should create inbox/ and attachments/ directories."""
        ds = tmp_path / "brand_new_dataset"
        watcher = GmailWatcher(
            dataset_dir=ds,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
        )
        assert (ds / "inbox").is_dir()
        assert (ds / "attachments").is_dir()


# ---------------------------------------------------------------------------
# Tests: Base64 URL decoding
# ---------------------------------------------------------------------------

class TestBase64Decode:

    def test_decode_normal(self):
        import base64
        original = "Hello, World! This is a test email body."
        encoded = base64.urlsafe_b64encode(original.encode()).decode()
        result = GmailWatcher._decode_base64url(encoded)
        assert result == original

    def test_decode_empty(self):
        assert GmailWatcher._decode_base64url("") == ""

    def test_decode_unicode(self):
        import base64
        original = "こんにちは — Unicode test: €, ñ, ü"
        encoded = base64.urlsafe_b64encode(original.encode("utf-8")).decode()
        result = GmailWatcher._decode_base64url(encoded)
        assert result == original


# ---------------------------------------------------------------------------
# Tests: Simulation Mode & API Endpoint
# ---------------------------------------------------------------------------

class TestSimulation:

    def test_simulate_clean_email(self, temp_dataset, tmp_path):
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
        )
        eid = watcher.simulate_incoming_email(has_discrepancy=False)
        assert eid.startswith("gmail_")
        assert (temp_dataset / "inbox" / f"{eid}.json").is_file()
        email_data = json.loads((temp_dataset / "inbox" / f"{eid}.json").read_text(encoding="utf-8"))
        assert len(email_data["attachments"]) == 2

    def test_simulate_discrepancy_email(self, temp_dataset, tmp_path):
        watcher = GmailWatcher(
            dataset_dir=temp_dataset,
            credentials_path=tmp_path / "creds.json",
            token_path=tmp_path / "token.json",
            state_path=tmp_path / "state.json",
        )
        eid = watcher.simulate_incoming_email(has_discrepancy=True)
        assert (temp_dataset / "inbox" / f"{eid}.json").is_file()

    def test_api_simulate_endpoint(self):
        from starlette.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        res = client.post("/api/gmail/simulate", json={"has_discrepancy": False, "booking_ref": "MEDU-TEST1"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "gmail_" in data["email_id"]
        assert data["dataset_id"] == "gmail_live"

