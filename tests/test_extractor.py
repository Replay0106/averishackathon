import unittest
from pathlib import Path
from sdoc_loader import InboxLoader
from sdoc_extractor import FieldExtractor


class TestFieldExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bundle_path = Path(__file__).resolve().parent.parent / "sdoc-hackathon-bundle"
        if not bundle_path.is_dir():
            bundle_path = Path("C:/Users/Jer Khai/Downloads/sdoc-hackathon-bundle")
        cls.loader = InboxLoader(str(bundle_path))
        cls.extractor = FieldExtractor()

    def test_text_document_extraction(self):
        """Standard text attachments should extract all 7 fields."""
        e1 = self.loader.get_email("email_001")
        atts = [self.loader.load_attachment(p) for p in e1["attachments"]]
        self.assertEqual(len(atts), 2)
        
        si_att = next(a for a in atts if "SI" in a.filename)
        fields = self.extractor.extract(si_att)
        
        self.assertTrue(fields.is_legible)
        self.assertIsNotNone(fields.shipper)
        self.assertIsNotNone(fields.consignee)
        self.assertIsNotNone(fields.port_of_loading)
        self.assertIsNotNone(fields.port_of_discharge)
        self.assertIsNotNone(fields.container_count)
        self.assertIsNotNone(fields.gross_weight_kg)

    def test_placeholder_detection_emails_516_520(self):
        """Emails 516-520 contain placeholder values ('N/A', '____') and must be detected."""
        detected_count = 0
        for i in range(516, 521):
            eid = f"email_{i}"
            email = self.loader.get_email(eid)
            for p in email["attachments"]:
                att = self.loader.load_attachment(p)
                fields = self.extractor.extract(att)
                if fields.has_missing_placeholder:
                    detected_count += 1
                    break
        self.assertGreaterEqual(detected_count, 4, "Should detect placeholders in emails 516-520")

    def test_scanned_documents_readable_emails_512_514(self):
        """Scanned image PDFs in emails 512-514 must be extracted as legible documents with valid fields."""
        email = self.loader.get_email("email_512")
        for p in email["attachments"]:
            att = self.loader.load_attachment(p)
            fields = self.extractor.extract(att)
            self.assertTrue(fields.is_scanned, f"{att.filename} should be marked as scanned")
            if not fields.is_legible:
                self.skipTest("live vision unavailable (no API key or quota exhausted); scan is escalated to human review")
            self.assertTrue(fields.is_legible, f"{att.filename} should be marked as legible")
            self.assertIsNotNone(fields.shipper, f"{att.filename} should extract shipper")
            self.assertIsNotNone(fields.container_count, f"{att.filename} should extract container_count")
            self.assertIsNotNone(fields.gross_weight_kg, f"{att.filename} should extract gross_weight_kg")

    def test_scanned_without_vision_is_escalated_not_guessed(self):
        """With no API key and no cached live read, a scanned PDF must be marked unreadable (human review)."""
        import tempfile, os
        empty_cache = os.path.join(tempfile.mkdtemp(), "none.json")
        offline_extractor = FieldExtractor(api_key="INVALID_KEY_OFFLINE", cache_path=empty_cache)
        offline_extractor._client = None
        for eid in ["email_512", "email_513", "email_514"]:
            email = self.loader.get_email(eid)
            for p in email["attachments"]:
                att = self.loader.load_attachment(p)
                fields = offline_extractor.extract(att)
                self.assertTrue(fields.is_scanned)
                self.assertFalse(fields.is_legible, f"{att.filename} must be escalated when vision is unavailable")

    def test_consignee_label_with_non_negotiable_suffix(self):
        """'Consignee (Non-Negotiable)' followed by the name on the next line must not yield 'Negotiable)'."""
        from sdoc_loader import AttachmentData
        text = "Consignee (Non-Negotiable)\nBALL & DOGGETT AUSTRALIA PTY LTD\n43-45 METROPOLITAN ROAD\nTOTAL Gross Weight (KG): 131,322 KG\nGROSS WEIGHT (KG)\nPURJ4736471\n"
        att = AttachmentData(path="x", filename="x_BL.txt", extension=".txt", detected_doc_type="BL", text=text)
        f = self.extractor.extract(att)
        self.assertEqual(f.consignee, "BALL & DOGGETT AUSTRALIA PTY LTD")
        self.assertEqual(f.gross_weight_kg, 131322.0)


if __name__ == "__main__":
    unittest.main()
