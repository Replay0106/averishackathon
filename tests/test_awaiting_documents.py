"""A request to send the draft BL with nothing attached is shown as awaiting documents, not as verified; fields of a
review case are reported as not compared rather than as matching. The scored outcome is unchanged."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from sdoc_loader import InboxLoader
from tests.api_stores import isolate_api_stores


def write(root: Path, eid: str, body: str, attachments=(), docs=None):
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "attachments").mkdir(exist_ok=True)
    for name, text in (docs or {}).items():
        (root / "attachments" / name).write_text(text, encoding="utf-8")
    (root / "inbox" / f"{eid}.json").write_text(json.dumps({
        "email_id": eid, "from": "ops@medlog-shipping.com", "subject": f"Draft BL {eid}", "body": body,
        "attachments": [f"attachments/{a}" for a in attachments]}), encoding="utf-8")


class AwaitingDocuments(unittest.TestCase):
    def setUp(self):
        import api.main as m

        self.m = m
        tmp = Path(tempfile.mkdtemp())
        isolate_api_stores(self, tmp)
        root = tmp / "folder"
        write(root, "email_001", "Please send the draft BL for checking asap.")
        # Only the SI is attached, so the case goes to review before the fields are compared.
        write(root, "email_002", "Please compare the SI and draft BL.", ["email_002_SI.txt"],
              {"email_002_SI.txt": "SHIPPING INSTRUCTION\nShipper: A\nConsignee: B\nPort of Loading: PORT KLANG\n"})
        self.d = m.Dataset(f"imp_{uuid.uuid4().hex[:8]}", "Test", root)
        self.d.loader = InboxLoader(str(root))

    def test_draft_request_is_awaiting_documents_and_scores_unchanged(self):
        r = self.m.run_email(self.d, "email_001")
        self.assertEqual((r["category"], r["status"]), ("BL_COMPARISON", "OK"))  # what the scorer expects
        self.assertTrue(r["awaiting_documents"])
        self.assertEqual(r["comparison"], [])

    def test_summary_counts_them_separately(self):
        self.m.DATASETS[self.d.id] = self.d
        self.addCleanup(self.m.DATASETS.pop, self.d.id, None)
        from fastapi.testclient import TestClient

        s = TestClient(self.m.app).get("/api/summary", params={"ds": self.d.id}).json()
        self.assertEqual((s["awaiting_documents"], s["ok"], s["comparisons"]), (1, 0, 1))

    def test_review_case_fields_are_not_reported_as_matching(self):
        r = self.m.run_email(self.d, "email_002")
        self.assertEqual(r["status"], "NEEDS_REVIEW")
        self.assertFalse(r["awaiting_documents"])
        self.assertTrue(r["comparison"])
        self.assertTrue(all(c["compared"] is False and c["match"] is False for c in r["comparison"]))


if __name__ == "__main__":
    unittest.main()
