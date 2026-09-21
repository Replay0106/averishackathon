import base64
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.gmail_service import GmailService, GmailServiceError


class TestGmailService(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
        env = {
            "GOOGLE_OAUTH_CLIENT_ID": "test.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
            "APP_ENCRYPTION_KEY": key,
            "GMAIL_LABEL": "NavisAI",
        }
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.service = GmailService(self.tmp / "private")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _connection(self, user_id="google-user", email="ops@example.com"):
        with self.service._connect() as con:
            con.execute(
                """INSERT INTO gmail_connections
                   (user_id, email_enc, refresh_token_enc, granted_scopes, label_name, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    self.service._encrypt(email.encode(), f"email:{user_id}"),
                    self.service._encrypt(b"refresh-token", f"refresh:{user_id}"),
                    "openid email gmail.readonly",
                    "NavisAI",
                    "2026-01-01T00:00:00Z",
                ),
            )

    def test_aes_gcm_round_trip_and_tamper_detection(self):
        encrypted = self.service._encrypt(b"shipping document", "test-aad")
        self.assertNotIn(b"shipping document", encrypted)
        self.assertEqual(self.service._decrypt(encrypted, "test-aad"), b"shipping document")
        damaged = encrypted[:-1] + bytes([encrypted[-1] ^ 1])
        with self.assertRaises(GmailServiceError):
            self.service._decrypt(damaged, "test-aad")

    def test_oauth_url_uses_state_pkce_and_read_only_scope(self):
        url = self.service.begin_oauth()
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("gmail.readonly", url)
        self.assertIn("state=", url)
        with self.service._connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM oauth_states").fetchone()[0], 1)

    def test_session_is_opaque_and_csrf_protected(self):
        self._connection()
        token = self.service._create_session("google-user")
        self.assertNotIn("google-user", token)
        status = self.service.status(token)
        self.assertTrue(status["connected"])
        self.assertEqual(status["email"], "ops@example.com")
        with self.assertRaises(GmailServiceError):
            self.service.require_csrf(token, "wrong")
        self.assertEqual(self.service.require_csrf(token, status["csrf_token"])["user_id"], "google-user")

    def test_gmail_message_is_normalised_and_materialised(self):
        self._connection()
        message = {
            "id": "abc123",
            "payload": {
                "headers": [
                    {"name": "From", "value": "shipper@example.com"},
                    {"name": "Subject", "value": "Please compare SI and BL"},
                ],
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "body": {"data": base64.urlsafe_b64encode(b"Attached documents").decode().rstrip("=")},
                    },
                    {
                        "mimeType": "text/plain",
                        "filename": "shipping_instruction.txt",
                        "body": {"data": base64.urlsafe_b64encode(b"SHIPPING INSTRUCTION\nShipper: ACME").decode().rstrip("=")},
                    },
                ],
            },
        }
        record, used, skipped = self.service._normalise_message(message, "unused", 0)
        self.assertEqual(record["email_id"], "gmail_abc123")
        self.assertEqual(record["body"], "Attached documents")
        self.assertEqual(len(record["attachments"]), 1)
        self.assertGreater(used, 0)
        self.assertEqual(skipped, [])

        encrypted = self.service._encrypt(json.dumps(record).encode(), "message:google-user:abc123")
        with self.service._connect() as con:
            con.execute(
                "INSERT INTO gmail_messages(user_id, gmail_id, payload_enc, updated_at) VALUES (?, ?, ?, ?)",
                ("google-user", "abc123", encrypted, "2026-01-01T00:00:00Z"),
            )
        root = self.service.materialise("google-user")
        try:
            email = json.loads((root / "inbox" / "gmail_abc123.json").read_text(encoding="utf-8"))
            self.assertEqual(email["from"], "shipper@example.com")
            self.assertTrue(email["attachments"][0].endswith("_SI.txt"))
            self.assertTrue((root / email["attachments"][0]).is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_private_gmail_dataset_requires_owning_session(self):
        from api import main

        self._connection()
        token = self.service._create_session("google-user")
        dataset_root = self.tmp / "dataset"
        (dataset_root / "inbox").mkdir(parents=True)
        (dataset_root / "attachments").mkdir()
        (dataset_root / "inbox" / "gmail_one.json").write_text(
            json.dumps({"email_id": "gmail_one", "from": "a@example.com", "subject": "hello", "body": "", "attachments": []}),
            encoding="utf-8",
        )
        dataset = main.Dataset("gmail_private_test", "Private Gmail", dataset_root, kind="gmail", owner_id="google-user")
        original_service = main.gmail
        main.gmail = self.service
        main.DATASETS[dataset.id] = dataset
        try:
            anonymous = TestClient(main.app)
            self.assertEqual(anonymous.get(f"/api/datasets/{dataset.id}").status_code, 404)
            owner = TestClient(main.app)
            owner.cookies.set("navis_session", token)
            self.assertEqual(owner.get(f"/api/datasets/{dataset.id}").status_code, 200)
            self.assertIn(dataset.id, {item["id"] for item in owner.get("/api/datasets").json()})
        finally:
            main.DATASETS.pop(dataset.id, None)
            main.gmail = original_service


if __name__ == "__main__":
    unittest.main()
