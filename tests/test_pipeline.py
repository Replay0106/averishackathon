import json
import os
import tempfile
import unittest
from pathlib import Path
from sdoc_pipeline import run_pipeline


class TestSDOCPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle_path = Path(__file__).resolve().parent.parent / "sdoc-hackathon-bundle"
        if not cls.bundle_path.is_dir():
            cls.bundle_path = Path("C:/Users/Jer Khai/Downloads/sdoc-hackathon-bundle")
        
        cls.test_dir = tempfile.mkdtemp()
        cls.output_file = os.path.join(cls.test_dir, "test_submission.json")
        cls.submission = run_pipeline(str(cls.bundle_path), cls.output_file)

    def test_total_count_and_keys(self):
        """Pipeline must process all 520 emails and generate valid JSON."""
        self.assertEqual(len(self.submission), 520)

    def test_sample_submission_parity(self):
        """Generated keys must match sample_submission.json 100%."""
        sample_path = self.bundle_path / "sample_submission.json"
        if sample_path.exists():
            with open(sample_path, "r", encoding="utf-8") as f:
                sample = json.load(f)
            self.assertEqual(set(self.submission.keys()), set(sample.keys()))

    def test_edge_case_clusters(self):
        """Verify each of the 5 deliberate edge-case clusters from the benchmark."""
        # 1. wrong_doc_type: 501-505
        for i in range(501, 506):
            eid = f"email_{i}"
            rec = self.submission[eid]
            self.assertEqual(rec["status"], "NEEDS_REVIEW", f"{eid} status")
            self.assertEqual(rec["review_reason"], "wrong_doc_type", f"{eid} reason")

        # 2. missing_attachment: 506-510
        for i in range(506, 511):
            eid = f"email_{i}"
            rec = self.submission[eid]
            self.assertEqual(rec["status"], "NEEDS_REVIEW", f"{eid} status")
            self.assertEqual(rec["review_reason"], "missing_attachment", f"{eid} reason")

        # 3. unreadable (corrupt stream): 511, 515
        for i in [511, 515]:
            eid = f"email_{i}"
            rec = self.submission[eid]
            self.assertEqual(rec["status"], "NEEDS_REVIEW", f"{eid} status")
            self.assertEqual(rec["review_reason"], "unreadable", f"{eid} reason")

        # 4. scanned docs: 512-514 (legible images extracted via vision)
        for i in [512, 513, 514]:
            eid = f"email_{i}"
            rec = self.submission[eid]
            # They must NOT be marked unreadable because they contain readable data!
            self.assertNotEqual(rec["review_reason"], "unreadable", f"{eid} must not be unreadable")
            self.assertEqual(rec["status"], "OK", f"{eid} should be clean match OK")

        # 5. missing_value: 516-520
        for i in range(516, 521):
            eid = f"email_{i}"
            rec = self.submission[eid]
            self.assertEqual(rec["status"], "NEEDS_REVIEW", f"{eid} status")
            self.assertEqual(rec["review_reason"], "missing_value", f"{eid} reason")


if __name__ == "__main__":
    unittest.main()
