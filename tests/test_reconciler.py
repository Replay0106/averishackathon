import unittest
from sdoc_extractor import ExtractedDocFields
from sdoc_reconciler import DocumentReconciler


class TestDocumentReconciler(unittest.TestCase):
    def setUp(self):
        self.reconciler = DocumentReconciler()

    def test_clean_match(self):
        """Identical SI and BL documents should yield status OK with no defects."""
        si = ExtractedDocFields(
            doc_type="SI",
            shipper="APRIL FAR EAST (M) SDN BHD",
            consignee="AL GURG STATIONERY LLC",
            notify_party="AL GURG STATIONERY LLC",
            port_of_loading="PORT KLANG",
            port_of_discharge="JEBEL ALI",
            container_count=6,
            gross_weight_kg=128544.0,
            is_legible=True
        )
        bl = ExtractedDocFields(
            doc_type="BL",
            shipper="APRIL FAR EAST (M) SDN. BHD.",
            consignee="AL GURG STATIONERY L.L.C.",
            notify_party="SAME AS CONSIGNEE",
            port_of_loading="PORT KELANG",
            port_of_discharge="JEBEL ALI, UAE",
            container_count=6,
            gross_weight_kg=128544.0,
            is_legible=True
        )
        res = self.reconciler.reconcile(si, bl)
        self.assertEqual(res["status"], "OK")
        self.assertFalse(res["has_defect"])
        self.assertEqual(res["defect_fields"], [])
        self.assertIsNone(res["review_reason"])

    def test_mismatched_container_count_and_weight(self):
        """Mismatched container count and weight should be flagged in defect_fields."""
        si = ExtractedDocFields(
            doc_type="SI",
            shipper="APRIL FAR EAST",
            consignee="GLOBAL TRADING",
            notify_party="GLOBAL TRADING",
            port_of_loading="PENANG",
            port_of_discharge="ROTTERDAM",
            container_count=10,
            gross_weight_kg=200000.0,
            is_legible=True
        )
        bl = ExtractedDocFields(
            doc_type="BL",
            shipper="APRIL FAR EAST",
            consignee="GLOBAL TRADING",
            notify_party="GLOBAL TRADING",
            port_of_loading="PENANG",
            port_of_discharge="ROTTERDAM",
            container_count=8,  # Mismatch: 10 vs 8
            gross_weight_kg=160000.0,  # Mismatch: 200000 vs 160000
            is_legible=True
        )
        res = self.reconciler.reconcile(si, bl)
        self.assertEqual(res["status"], "MISMATCH")
        self.assertTrue(res["has_defect"])
        self.assertIn("container_count", res["defect_fields"])
        self.assertIn("gross_weight_kg", res["defect_fields"])
        self.assertEqual(len(res["defect_fields"]), 2)

    def test_wrong_doc_type_trigger(self):
        """Disguised document (e.g. PACKING_LIST or INVOICE) must trigger wrong_doc_type."""
        si = ExtractedDocFields(doc_type="INVOICE", is_legible=True)
        bl = ExtractedDocFields(doc_type="BL", is_legible=True)
        res = self.reconciler.reconcile(si, bl)
        self.assertEqual(res["status"], "NEEDS_REVIEW")
        self.assertEqual(res["review_reason"], "wrong_doc_type")

    def test_missing_value_trigger(self):
        """Placeholder values (N/A, TBA) must trigger missing_value."""
        si = ExtractedDocFields(doc_type="SI", has_missing_placeholder=True, is_legible=True)
        bl = ExtractedDocFields(doc_type="BL", is_legible=True)
        res = self.reconciler.reconcile(si, bl)
        self.assertEqual(res["status"], "NEEDS_REVIEW")
        self.assertEqual(res["review_reason"], "missing_value")

    def test_unreadable_trigger(self):
        """Illegible or corrupted documents must trigger unreadable."""
        si = ExtractedDocFields(doc_type="SI", is_legible=False)
        bl = ExtractedDocFields(doc_type="BL", is_legible=True)
        res = self.reconciler.reconcile(si, bl)
        self.assertEqual(res["status"], "NEEDS_REVIEW")
        self.assertEqual(res["review_reason"], "unreadable")


if __name__ == "__main__":
    unittest.main()
