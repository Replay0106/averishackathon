import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from sdoc_pipeline import update_submission_record


class TestHITLPersistence(unittest.TestCase):
    def setUp(self):
        # Create a temporary submission.json file for isolated testing
        self.test_dir = tempfile.mkdtemp()
        self.submission_path = os.path.join(self.test_dir, "test_submission.json")
        
        # Seed with sample data
        self.sample_data = {
            "email_001": {
                "category": "BL_COMPARISON",
                "status": "MISMATCH",
                "review_reason": None,
                "defect_fields": ["container_count"],
                "has_defect": True,
                "extracted_fields": {
                    "si": {"container_count": 10},
                    "bl": {"container_count": 8}
                }
            },
            "email_002": {
                "category": "GENERAL",
                "status": "OK",
                "review_reason": None,
                "defect_fields": [],
                "has_defect": False
            }
        }
        with open(self.submission_path, "w", encoding="utf-8") as f:
            json.dump(self.sample_data, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_approve_correction_persists_to_disk(self):
        """Approving a flagged email updates status to OK, clears defects, and saves to file."""
        updated_entry = {
            "category": "BL_COMPARISON",
            "status": "OK",
            "review_reason": None,
            "defect_fields": [],
            "has_defect": False,
            "reviewed_by": "operator_jk",
            "reconciliation_notes": "Shipper confirmed container count of 10 was split across 2 drafts."
        }
        
        success = update_submission_record(self.submission_path, "email_001", updated_entry)
        self.assertTrue(success)

        # Read back from disk
        with open(self.submission_path, "r", encoding="utf-8") as f:
            persisted = json.load(f)

        self.assertIn("email_001", persisted)
        self.assertEqual(persisted["email_001"]["status"], "OK")
        self.assertFalse(persisted["email_001"]["has_defect"])
        self.assertEqual(persisted["email_001"]["defect_fields"], [])
        self.assertEqual(persisted["email_001"]["reviewed_by"], "operator_jk")
        # Other records remain intact
        self.assertIn("email_002", persisted)
        self.assertEqual(persisted["email_002"]["status"], "OK")

    def test_escalate_correction_persists_to_disk(self):
        """Escalating an email updates review_reason and status to NEEDS_REVIEW."""
        updated_entry = {
            "category": "BL_COMPARISON",
            "status": "NEEDS_REVIEW",
            "review_reason": "escalated_to_supervisor",
            "defect_fields": ["shipper", "consignee"],
            "has_defect": True
        }
        
        success = update_submission_record(self.submission_path, "email_001", updated_entry)
        self.assertTrue(success)

        with open(self.submission_path, "r", encoding="utf-8") as f:
            persisted = json.load(f)

        self.assertEqual(persisted["email_001"]["status"], "NEEDS_REVIEW")
        self.assertEqual(persisted["email_001"]["review_reason"], "escalated_to_supervisor")


if __name__ == "__main__":
    unittest.main()
