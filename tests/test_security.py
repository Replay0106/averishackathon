"""
Unit tests for sdoc_security.py (Cybersecurity & Tamper-Evident Audit Ledger)
"""
import unittest
import tempfile
import os
from sdoc_security import SecurityGuard, TamperEvidentAuditLedger

class TestSecurityArchitecture(unittest.TestCase):
    def test_spoofing_detection(self):
        res_legit = SecurityGuard.verify_email_authentication("ops@msc.com", "MSC Desk")
        self.assertFalse(res_legit["is_suspicious"])
        self.assertEqual(res_legit["auth_status"], "NO_FLAGS")
        self.assertEqual(res_legit["spf"], "NOT_CHECKED")

        res_spoofed = SecurityGuard.verify_email_authentication("scam@malicious-domain.xyz", "MSC Customer Service")
        self.assertTrue(res_spoofed["is_suspicious"])
        self.assertEqual(res_spoofed["auth_status"], "SUSPICIOUS")

    def test_pdf_payload_sandbox(self):
        safe_bytes = b"%PDF-1.4 ... valid stream content ..."
        malicious_bytes = b"%PDF-1.4 ... /JavaScript (app.alert('pwned')) ..."
        
        self.assertTrue(SecurityGuard.scan_attachment_payload("safe.pdf", safe_bytes)["is_safe"])
        self.assertFalse(SecurityGuard.scan_attachment_payload("exploit.pdf", malicious_bytes)["is_safe"])

    def test_pii_masking(self):
        text = "Please remit funds to IBAN DE89370400440532013000 at Port of discharge."
        masked = SecurityGuard.mask_sensitive_data(text)
        self.assertNotIn("DE89370400440532013000", masked)
        self.assertIn("DE89 **** **** 3000", masked)

    def test_audit_ledger_cryptographic_integrity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = os.path.join(tmpdir, "test_ledger.json")
            ledger = TamperEvidentAuditLedger(ledger_file)
            
            ledger.record_action("CLERK_A", "email_001", "APPROVED", {"notes": "Verified against LC"})
            ledger.record_action("DESK_LEAD", "email_002", "ESCALATED", {"reason": "Demurrage threshold exceeded"})
            
            is_valid, msg = ledger.verify_integrity()
            self.assertTrue(is_valid)
            self.assertIn("verified", msg.lower())

if __name__ == "__main__":
    unittest.main()
