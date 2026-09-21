"""Regression tests for the four comparison rules that a near-identical draft must not slip through.

Derived from the DOCSTRESS benchmark, where a changed port, a one-kilo weight difference, a field
stated twice, or a second copy of the BL must never be reported as a clean match.
"""
import unittest

from sdoc_extractor import ExtractedDocFields, FieldExtractor
from sdoc_loader import AttachmentData
from sdoc_reconciler import DocumentReconciler, ports_match, select_documents

SI_TEXT = (
    "SHIPPING INSTRUCTIONS\n"
    "Shipper: Atlas Export Industries 27\n"
    "Consignee: North Harbor Imports 27\n"
    "Notify Party: Maritime Agency 27\n"
    "Port of Loading: Port Klang\n"
    "Port of Discharge: Rotterdam\n"
    "Container Count: 1\n"
    "Total Gross Weight: 12297 KG\n"
)


def fields(**over):
    base = dict(
        doc_type="SI", shipper="Atlas Export Industries", consignee="North Harbor Imports",
        notify_party="Maritime Agency", port_of_loading="Singapore", port_of_discharge="Tokyo",
        container_count=2, gross_weight_kg=12110.0, is_legible=True,
    )
    base.update(over)
    return ExtractedDocFields(**base)


def att(name, doc_type, text=""):
    return AttachmentData(path=name, filename=name, extension="." + name.rsplit(".", 1)[-1],
                          detected_doc_type=doc_type, text=text)


class TestPortStrictness(unittest.TestCase):
    def test_added_country_or_locode_still_matches(self):
        self.assertTrue(ports_match("JEBEL ALI", "JEBEL ALI, UAE"))
        self.assertTrue(ports_match("NHAVA SHEVA, INDIA", "NHAVA SHEVA, INDIA (INNSA)"))
        self.assertTrue(ports_match("PORT KELANG", "PORT KLANG"))

    def test_changed_port_is_a_mismatch(self):
        self.assertFalse(ports_match("Singapore", "Singapore Changed"))
        self.assertFalse(ports_match("Long Beach", "Long Beach Changed"))
        self.assertFalse(ports_match("TOKYO, JAPAN", "OSAKA, JAPAN"))

    def test_reconciler_flags_changed_port(self):
        res = DocumentReconciler().reconcile(
            email_id="t", si_att=att("a_SI.txt", "SI"), bl_att=att("a_BL.txt", "BL"),
            si_fields=fields(), bl_fields=fields(doc_type="BL", port_of_loading="Singapore Changed"))
        self.assertEqual(res["status"], "MISMATCH")
        self.assertEqual(res["defect_fields"], ["port_of_loading"])


class TestWeightStrictness(unittest.TestCase):
    def test_one_kilo_difference_is_reported(self):
        res = DocumentReconciler().reconcile(
            email_id="t", si_att=att("a_SI.txt", "SI"), bl_att=att("a_BL.txt", "BL"),
            si_fields=fields(gross_weight_kg=12143.0), bl_fields=fields(doc_type="BL", gross_weight_kg=12144.0))
        self.assertEqual(res["status"], "MISMATCH")
        self.assertEqual(res["defect_fields"], ["gross_weight_kg"])

    def test_rounding_noise_still_matches(self):
        res = DocumentReconciler().reconcile(
            email_id="t", si_att=att("a_SI.txt", "SI"), bl_att=att("a_BL.txt", "BL"),
            si_fields=fields(gross_weight_kg=131058.0), bl_fields=fields(doc_type="BL", gross_weight_kg=131058.2))
        self.assertEqual(res["status"], "OK")


class TestConflictingValue(unittest.TestCase):
    def test_field_stated_twice_is_escalated(self):
        extractor = FieldExtractor()
        bl_text = SI_TEXT.replace("BILL", "BILL").replace(
            "Shipper: Atlas Export Industries 27\n",
            "Shipper: Atlas Export Industries 27\nShipper: Conflicting Exporter Ltd\n")
        si = extractor.extract(att("c_SI.txt", "SI", SI_TEXT))
        bl = extractor.extract(att("c_BL.txt", "BL", bl_text))
        self.assertFalse(si.has_conflicting_value)
        self.assertTrue(bl.has_conflicting_value)
        self.assertEqual(bl.conflicting_field_name, "shipper")
        res = DocumentReconciler().reconcile(
            email_id="t", si_att=att("c_SI.txt", "SI"), bl_att=att("c_BL.txt", "BL"), si_fields=si, bl_fields=bl)
        self.assertEqual(res["status"], "NEEDS_REVIEW")

    def test_table_headings_without_a_colon_are_not_a_conflict(self):
        """A repeated column heading such as "CONTAINER NO." must not read as a second value."""
        text = SI_TEXT + "CONTAINER NO.\nABCD1234567\nCONTAINER NO.\nEFGH7654321\n"
        f = FieldExtractor().extract(att("t_SI.txt", "SI", text))
        self.assertFalse(f.has_conflicting_value)


class TestDuplicateDocuments(unittest.TestCase):
    def test_second_copy_of_the_bl_is_ambiguous(self):
        si, bl, ambiguous = select_documents([att("e_SI.txt", "SI"), att("e_BL.txt", "BL"), att("e_BL_copy.txt", "BL")])
        self.assertTrue(ambiguous)
        self.assertIsNotNone(si)
        self.assertIsNotNone(bl)
        res = DocumentReconciler().reconcile(
            email_id="t", si_att=si, bl_att=bl, si_fields=fields(), bl_fields=fields(doc_type="BL"),
            ambiguous_documents=ambiguous)
        self.assertEqual(res["status"], "NEEDS_REVIEW")

    def test_ordinary_pair_is_not_ambiguous(self):
        si, bl, ambiguous = select_documents([att("e_SI.txt", "SI"), att("e_BL.txt", "BL")])
        self.assertFalse(ambiguous)
        self.assertEqual((si.filename, bl.filename), ("e_SI.txt", "e_BL.txt"))


class TestClassifierIntent(unittest.TestCase):
    """Wording the keyword lists have never seen must still land in the right category."""

    def setUp(self):
        from sdoc_classifier import EmailClassifier
        self.classify = EmailClassifier().classify_email

    def mail(self, subject, body, attachments=()):
        return {"email_id": "x", "from": "a@b.c", "subject": subject, "body": body, "attachments": list(attachments)}

    def test_si_requests(self):
        for subject, body in [
            ("Shipping instructions required", "Please send the completed shipping instructions before today's carrier cutoff."),
            ("SI outstanding", "We are still waiting for the final SI details for this booking."),
            ("Cargo particulars", "Kindly provide the shipping instruction so documentation can proceed."),
        ]:
            self.assertEqual(self.classify(self.mail(subject, body)), "SI_REQUEST", subject)

    def test_invoice_queries(self):
        for subject, body in [
            ("Invoice discrepancy", "Please explain the additional handling charge shown on this invoice."),
            ("Billing correction", "The billed amount does not match our agreement. Please issue a corrected invoice."),
            ("Duplicate charge", "Our accounts team found a duplicate freight charge and needs clarification."),
        ]:
            self.assertEqual(self.classify(self.mail(subject, body)), "INVOICE_QUERY", subject)

    def test_phishing_is_spam(self):
        for subject, body in [
            ("Urgent shipment alert", "Your cargo account will be suspended. Verify your password at example.invalid now."),
            ("Mailbox warning", "Storage exceeded. Open the secure attachment and sign in immediately."),
            ("Prize notification", "You have won a logistics rebate. Submit your credentials to claim it today."),
        ]:
            self.assertEqual(self.classify(self.mail(subject, body)), "SPAM", subject)

    def test_operational_chatter_stays_general(self):
        for subject, body in [
            ("Schedule update", "The vessel schedule has moved by one day. We will circulate the confirmed ETA."),
            ("Thank you", "Noted with thanks. We will update the operations team."),
            ("Meeting change", "Tomorrow's coordination call has moved to the afternoon."),
            ("Daily berthing report", "Kindly find the daily berthing report attached. Vessel MMSS 2507 berthed on schedule."),
        ]:
            self.assertEqual(self.classify(self.mail(subject, body)), "GENERAL", subject)

    def test_reminder_subject_alone_is_not_a_request(self):
        """A recurring "Submit SI" subject over an unrelated body is chatter, not an SI request."""
        mail = self.mail("_Reminder_Paper - Submit SI & AED_26-01-2026",
                         "Dear Team, Kindly find the daily berthing report attached. Vessel NAP 914 berthed on schedule.")
        self.assertEqual(self.classify(mail), "GENERAL")

    def test_automated_billing_notice_is_not_a_query(self):
        mail = self.mail("28_01_2026 - UPDATE SUMMARY LE HAVRE",
                         "This is an automated notification. The India HSS SD Billing Process has completed successfully. No action required.")
        self.assertEqual(self.classify(mail), "GENERAL")


if __name__ == "__main__":
    unittest.main()
