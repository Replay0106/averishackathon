"""
Unit tests for sdoc_sla.py (Vessel Cut-Off SLA Prioritizer)
"""
import unittest
from sdoc_sla import extract_vessel_and_cutoff, prioritize_review_queue

class TestSLAEngine(unittest.TestCase):
    def test_extract_vessel_and_cutoff(self):
        sample_text = "Vessel: EVER GIVEN Voy: 104E\nSI Cut-off: 21-SEP-2026 14:00"
        sla = extract_vessel_and_cutoff(sample_text, email_id="email_101")
        self.assertIn("vessel_name", sla)
        self.assertIn("hours_to_cutoff", sla)
        self.assertIn(sla["sla_tier"], ["EMERGENCY", "URGENT", "STANDARD", "ROUTINE"])

    def test_prioritization_sorting(self):
        items = [
            {"id": "routine", "sla": {"sla_tier": "ROUTINE", "hours_to_cutoff": 72.0}, "risk": {"risk_level": "LOW"}},
            {"id": "emergency", "sla": {"sla_tier": "EMERGENCY", "hours_to_cutoff": 2.5}, "risk": {"risk_level": "CRITICAL"}},
            {"id": "urgent", "sla": {"sla_tier": "URGENT", "hours_to_cutoff": 14.0}, "risk": {"risk_level": "HIGH"}},
        ]
        sorted_items = prioritize_review_queue(items)
        self.assertEqual(sorted_items[0]["id"], "emergency")
        self.assertEqual(sorted_items[1]["id"], "urgent")
        self.assertEqual(sorted_items[2]["id"], "routine")

if __name__ == "__main__":
    unittest.main()
