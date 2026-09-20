"""
Unit tests for sdoc_monitor.py (Continuous Inbox Monitor & Client Rectification)
"""
import unittest
import tempfile
import os
import json
from sdoc_monitor import generate_client_rectification_notice, dispatch_client_rectification_email, AutonomousInboxMonitor
from sdoc_security import TamperEvidentAuditLedger

class TestMonitorAndClientRectification(unittest.TestCase):
    def test_generate_client_rectification_notice(self):
        email_data = {"subject": "Draft BL verification", "body": "Vessel CMA CGM MONTMARTRE voy 026E", "from": "ops@forwarder.com"}
        recon = {"status": "MISMATCH", "defect_fields": ["gross_weight_kg", "container_count"]}
        si_data = {"gross_weight_kg": 24000.0, "container_count": 5}
        bl_data = {"gross_weight_kg": 22500.0, "container_count": 4}
        
        notice = generate_client_rectification_notice("email_099", email_data, recon, si_data, bl_data)
        self.assertEqual(notice["recipient"], "ops@forwarder.com")
        self.assertIn("CMA CGM MONTMARTRE", notice["subject"])
        self.assertIn("Gross Weight", notice["body"])
        self.assertIn("24000.0", notice["body"])
        self.assertIn("22500.0", notice["body"])
        self.assertIn("UCP 600", notice["body"])

    def test_dispatch_client_rectification_email(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_file = os.path.join(tmpdir, "submission.json")
            ledger_file = os.path.join(tmpdir, "ledger.json")
            with open(sub_file, "w") as f:
                json.dump({"email_099": {"status": "MISMATCH"}}, f)
                
            ledger = TamperEvidentAuditLedger(ledger_file)
            res = dispatch_client_rectification_email(
                email_id="email_099",
                recipient="ops@forwarder.com",
                subject="Test Subject",
                body="Test Body",
                actor="CLERK_TEST",
                submission_path=sub_file,
                audit_ledger=ledger
            )
            self.assertEqual(res["status"], "SUCCESS")
            
            # Check submission was updated
            with open(sub_file, "r") as f:
                saved = json.load(f)
            self.assertEqual(saved["email_099"]["client_notification"]["status"], "DISPATCHED")
            self.assertEqual(saved["email_099"]["client_followup_status"], "PENDING_CLIENT_AMENDMENT")

if __name__ == "__main__":
    unittest.main()
