"""
Unit tests for sdoc_risk.py (Financial & Demurrage Risk Engine)
"""
import unittest
from sdoc_risk import compute_shipment_risk

class TestRiskEngine(unittest.TestCase):
    def test_ok_shipment_negligible_risk(self):
        record = {"status": "OK", "defect_fields": []}
        risk = compute_shipment_risk("email_001", record)
        self.assertEqual(risk["risk_level"], "NEGLIGIBLE")
        self.assertEqual(risk["total_exposure_usd"], 0.0)

    def test_title_discrepancy_critical_risk(self):
        record = {"status": "MISMATCH", "defect_fields": ["consignee"]}
        data = {"container_count": 4}
        risk = compute_shipment_risk("email_002", record, extracted_data=data)
        self.assertEqual(risk["risk_level"], "CRITICAL")
        self.assertGreater(risk["bank_presentation_risk_usd"], 0.0)
        self.assertTrue(any("UCP 600" in v["framework"] for v in risk["statutory_violations"]))

    def test_weight_discrepancy_solas_vgm(self):
        record = {"status": "MISMATCH", "defect_fields": ["gross_weight_kg"]}
        data = {"container_count": 2}
        risk = compute_shipment_risk("email_003", record, extracted_data=data)
        self.assertEqual(risk["risk_level"], "HIGH")
        self.assertTrue(any("SOLAS" in v["framework"] for v in risk["statutory_violations"]))

    def test_needs_review_demurrage(self):
        record = {"status": "NEEDS_REVIEW", "review_reason": "unreadable"}
        data = {"container_count": 1}
        risk = compute_shipment_risk("email_511", record, extracted_data=data)
        self.assertIn(risk["risk_level"], ["HIGH", "CRITICAL"])
        self.assertGreater(risk["demurrage_exposure_usd"], 0.0)

if __name__ == "__main__":
    unittest.main()
