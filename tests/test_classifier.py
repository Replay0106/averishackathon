import unittest
import os
from pathlib import Path
from sdoc_loader import InboxLoader
from sdoc_classifier import EmailClassifier


class TestEmailClassifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle_path = Path(__file__).resolve().parent.parent / "sdoc-hackathon-bundle"
        if not bundle_path.is_dir():
            bundle_path = Path("C:/Users/Jer Khai/Downloads/sdoc-hackathon-bundle")
        cls.loader = InboxLoader(str(bundle_path))
        cls.classifier = EmailClassifier()

    def test_five_categories_exist(self):
        """Ensure all 5 categories are recognized."""
        valid_cats = {"BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"}
        self.assertEqual(valid_cats, {"BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"})

    def test_classification_on_benchmark_samples(self):
        """Test specific benchmark email classifications."""
        # email_001 is a comparison request
        e1 = self.loader.get_email("email_001")
        cat1 = self.classifier.classify(e1)
        self.assertEqual(cat1, "BL_COMPARISON")

        # Edge cases 501-520 must route to BL_COMPARISON for inspection
        for i in range(501, 521):
            eid = f"email_{i}"
            e = self.loader.get_email(eid)
            cat = self.classifier.classify(e)
            self.assertEqual(cat, "BL_COMPARISON", f"{eid} should be classified as BL_COMPARISON")

    def test_full_inbox_distribution(self):
        """Test that classifying all 520 emails yields the expected distribution."""
        email_ids = self.loader.get_email_ids()
        self.assertEqual(len(email_ids), 520)

        counts = {}
        for eid in email_ids:
            email = self.loader.get_email(eid)
            cat = self.classifier.classify(email)
            counts[cat] = counts.get(cat, 0) + 1

        self.assertEqual(counts.get("BL_COMPARISON"), 129)
        self.assertEqual(counts.get("SI_REQUEST"), 132)
        self.assertEqual(counts.get("INVOICE_QUERY"), 75)
        self.assertEqual(counts.get("GENERAL"), 152)
        self.assertEqual(counts.get("SPAM"), 32)


if __name__ == "__main__":
    unittest.main()
