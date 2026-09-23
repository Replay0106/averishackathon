"""Every dataset's emails pass the Trust Gateway, and emails that arrive later still get their amendment."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from sdoc_loader import InboxLoader
from tests.api_stores import isolate_api_stores

DOC = """{title}
Shipper: APRIL FAR EAST (M) SDN BHD
Consignee: AL GURG STATIONERY LLC
Notify Party: SAME AS CONSIGNEE
Port of Loading: PORT KLANG
Port of Discharge: JEBEL ALI
No. of Containers: 2 x 40'HC
Gross Weight: {weight} KGS
"""


def add_email(root: Path, eid: str, sender: str, bl_weight: str = "2,000.00") -> None:
    (root / "inbox").mkdir(parents=True, exist_ok=True)
    (root / "attachments").mkdir(exist_ok=True)
    (root / "attachments" / f"{eid}_SI.txt").write_text(DOC.format(title="SHIPPING INSTRUCTION", weight="1,000.00"), encoding="utf-8")
    (root / "attachments" / f"{eid}_BL.txt").write_text(DOC.format(title="DRAFT BILL OF LADING", weight=bl_weight), encoding="utf-8")
    (root / "inbox" / f"{eid}.json").write_text(json.dumps({
        "email_id": eid, "from": sender, "subject": f"Please check draft BL {eid}",
        "body": "Please compare the attached draft BL against our SI.",
        "attachments": [f"attachments/{eid}_SI.txt", f"attachments/{eid}_BL.txt"],
    }), encoding="utf-8")


class GatewayWiring(unittest.TestCase):
    def setUp(self):
        import api.main as m
        from api import gateway_routes as gr

        self.m, self.gr = m, gr
        self.tmp = Path(tempfile.mkdtemp())
        isolate_api_stores(self, self.tmp)
        self.root = self.tmp / "folder"
        self.ds = m.Dataset(f"imp_{uuid.uuid4().hex[:8]}", "Test folder", self.root)

    def dataset(self):
        self.ds.loader = InboxLoader(str(self.root))
        return self.ds

    def test_imported_mismatch_is_gated_with_a_scoped_id_and_still_amended(self):
        add_email(self.root, "email_001", "ops@medlog-shipping.com")
        d = self.dataset()
        self.m.ensure_amendments(d)
        key = f"{d.id}:email_001"
        dec = self.gr.GATEWAY.decisions_by_email_id[key]
        self.assertEqual(dec.failed_gate, "discrepancy_plausibility")  # unknown sender + mismatch
        # The demo inbox's own email_001 is a different email and was not treated as a duplicate of this one.
        self.assertNotEqual(self.gr.GATEWAY.decisions_by_email_id.get("email_001"), dec)
        self.assertEqual(self.m._gateway_verdict(d, "email_001")["failed_gate"], "discrepancy_plausibility")
        row = self.m.outbox.get(d.id, "email_001")
        self.assertIsNotNone(row, "a mismatch-only rejection must not stop the amendment")
        self.assertEqual(row["recipient"], "ops@medlog-shipping.com")

    def test_email_added_later_gets_its_amendment_without_a_restart(self):
        add_email(self.root, "email_001", "ops@medlog-shipping.com")
        d = self.dataset()
        self.m.ensure_amendments(d)
        add_email(self.root, "email_002", "ops@medlog-shipping.com", bl_weight="3,000.00")
        d = self.dataset()
        self.m.ensure_amendments(d)
        self.assertIn(f"{d.id}:email_002", self.gr.GATEWAY.decisions_by_email_id)
        self.assertIsNotNone(self.m.outbox.get(d.id, "email_002"))

    def test_a_security_rejection_still_blocks_the_amendment(self):
        add_email(self.root, "email_001", "billing@webmail-verify.co")
        d = self.dataset()
        self.m.ensure_amendments(d)
        verdict = self.m._gateway_verdict(d, "email_001")
        self.assertIn(verdict["failed_gate"], ("sender_trust", "authentication"))  # a known phishing domain starts untrusted
        self.assertIsNone(self.m.outbox.get(d.id, "email_001"))

    def test_gmail_messages_share_one_gateway_id_across_datasets(self):
        gm = self.m.Dataset("gmail_test", "Gmail", self.root, kind="gmail")
        self.assertEqual(self.m.gateway_key(gm, "gmail_abc"), "gmail_abc")
        self.assertEqual(self.m.gateway_key(self.m.DATASETS["demo"], "gmail_abc"), "gmail_abc")
        self.assertEqual(self.m.gateway_key(self.ds, "email_001"), f"{self.ds.id}:email_001")


if __name__ == "__main__":
    unittest.main()
