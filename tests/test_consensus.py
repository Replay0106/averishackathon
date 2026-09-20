"""
Unit tests for sdoc_consensus.py (Temporal Multi-Turn Consensus Engine)
"""
import unittest
from sdoc_consensus import extract_thread_keys, extract_version_indicator, build_consensus_graph

class TestConsensusEngine(unittest.TestCase):
    def test_extract_thread_keys(self):
        keys = extract_thread_keys("Please find attached draft for BKG-98231 and BL MEDU981240", "Booking 98231")
        self.assertTrue(any("98231" in k for k in keys))

    def test_version_extraction(self):
        v1 = extract_version_indicator("Draft B/L", "Attached initial draft")
        v2 = extract_version_indicator("Revised Draft B/L v2", "Updated weights")
        v_final = extract_version_indicator("Final B/L", "Clean manifest")
        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        self.assertEqual(v_final, 99)

    def test_zombie_discrepancy_suppression(self):
        inbox = [
            {"id": "email_001", "subject": "Draft BL v1 BKG-99999", "body": "Initial draft Booking BKG-99999"},
            {"id": "email_002", "subject": "Revised Draft BL v2 BKG-99999", "body": "Corrected weight Booking BKG-99999"}
        ]
        submissions = {
            "email_001": {"status": "MISMATCH", "defect_fields": ["gross_weight_kg"]},
            "email_002": {"status": "OK", "defect_fields": []}
        }
        consensus = build_consensus_graph(inbox, submissions)
        self.assertTrue(consensus["email_001"]["is_superseded"])
        self.assertEqual(consensus["email_001"]["superseded_by"], "email_002")
        self.assertTrue(consensus["email_002"]["is_latest_revision"])

if __name__ == "__main__":
    unittest.main()
