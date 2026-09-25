"""Damaged or unusual layouts reach the same fields as clean ones; labels are corrected, values never are."""
import unittest

from sdoc_extractor import FieldExtractor
from sdoc_loader import AttachmentData, InboxLoader
from sdoc_textnorm import match_label, normalize_document_text, normalize_email_text, strip_quoted_metadata

CLEAN = {"shipper": "Kestrel Timber Ltd", "consignee": "Nordhafen Handel GmbH", "notify_party": "Blue Dock Agency",
         "port_of_loading": "Penang, Malaysia", "port_of_discharge": "Hamburg, Germany", "container_count": 3,
         "gross_weight_kg": 15250.0}
ROWS = [("Shipper", "Kestrel Timber Ltd"), ("Consignee", "Nordhafen Handel GmbH"), ("Notify Party", "Blue Dock Agency"),
        ("Port of Loading", "Penang, Malaysia"), ("Port of Discharge", "Hamburg, Germany"), ("Total Containers", "3"),
        ("Gross Weight", "15250 KG")]


def read(text):
    att = AttachmentData(path="", filename="x_BL.txt", extension=".txt", text=text, detected_doc_type="BL")
    f = FieldExtractor(api_key="").extract(att)
    return {k: getattr(f, k) for k in CLEAN}, f


def lines(fmt, rows=ROWS, title="BILL OF LADING (DRAFT)"):
    return "\n".join([title] + [fmt.format(k, v) for k, v in rows])


class Layouts(unittest.TestCase):
    def assertReads(self, text):
        got, f = read(text)
        self.assertEqual(got, CLEAN, text)
        self.assertFalse(f.has_missing_placeholder or f.has_conflicting_value, text)

    def test_separators_and_prefixes(self):
        for fmt in ("{}: {}", "{} = {}", "{}\t{}", "{}：{}", "[x] {}: {}", "**{}**: {}", '"{}","{}"', "- {}: {}"):
            self.assertReads(lines(fmt))
        self.assertReads("\n".join(f"L{i:03d}  {k}: {v}" for i, (k, v) in enumerate(ROWS, 1)))

    def test_line_endings_and_html(self):
        self.assertReads(lines("{}: {}").replace("\n", "\r"))
        self.assertReads("<html><body>" + "".join(f"<p>{k}: {v}</p>" for k, v in ROWS) + "</body></html>")

    def test_horizontal_table(self):
        self.assertReads("BILL OF LADING\n" + " | ".join(k for k, _ in ROWS) + "\n" + " | ".join(v for _, v in ROWS))

    def test_misspelled_and_ocr_damaged_labels(self):
        damaged = {"Shipper": "Shiper", "Consignee": "Consingee", "Gross Weight": "Gros Weihgt",
                   "Port of Loading": "P0rt of L0ading", "Total Containers": "Tota1 Containers", "Notify Party": "Noti fy Party"}
        self.assertReads(lines("{}: {}", [(damaged.get(k, k), v) for k, v in ROWS]))

    def test_abbreviations_and_value_after_page_break(self):
        rows = [("No. Cntrs", "3") if k == "Total Containers" else ("G.W.", "15250 KG") if k == "Gross Weight" else (k, v) for k, v in ROWS]
        self.assertReads(lines("{}: {}", rows))
        text = lines("{}: {}", ROWS[:-1]) + "\nGross Weight:\n--- PAGE BREAK ---\nACME LINES / Page 2 of 2\n15250 KG"
        self.assertReads(text)

    def test_lines_that_only_look_like_a_field(self):
        text = "BILL OF LADING\nShipper Contact: Front desk\nConsignee Reference: C-778\nGross Weight Tolerance: 2 percent\n" + lines("{}: {}", title="")
        self.assertReads(text)

    def test_cover_sheet_and_blank_sample(self):
        self.assertReads("FAX COVER\nCOMMERCIAL INVOICE: sent separately\nEND COVER\n" + lines("{}: {}"))
        self.assertReads("UNFILLED SAMPLE\nConsignee: ______\nGross Weight: N/A\n" + lines("{}: {}"))
        loader = InboxLoader(".")
        self.assertEqual(loader._detect_doc_type("TRANSMITTAL COVER\nCOMMERCIAL INVOICE: not attached\n" + lines("{}: {}"), "a.txt"), "BL")
        self.assertEqual(loader._detect_doc_type("COMMERCIAL INVOICE\nInvoice No: 44\nAmount: 900 USD", "a_BL.txt"), "INVOICE")

    def test_unit_in_the_label_is_kept(self):
        got, _ = read(lines("{}: {}", ROWS[:-1] + [("Gross Weight (MT)", "15.25")]))
        self.assertEqual(got["gross_weight_kg"], 15250.0)


class Safety(unittest.TestCase):
    def test_values_are_never_corrected(self):
        got, _ = read(lines("{}: {}", [("Shipper", "Kestrel Timbr Ltd")] + ROWS[1:]))
        self.assertEqual(got["shipper"], "Kestrel Timbr Ltd")

    def test_a_typo_label_never_overrides_a_proper_one(self):
        # "Shiper" is ignored because the document already states "Shipper"; nothing is guessed either way.
        got, _ = read(lines("{}: {}", ROWS + [("Shiper", "Someone Else Ltd")]))
        self.assertEqual(got["shipper"], "Kestrel Timber Ltd")

    def test_near_words_are_not_labels(self):
        for word in ("Shipped", "Shipping Line", "Container No", "Consignor", "Net Weight", "Portal"):
            self.assertIsNone(match_label(word, fuzzy=True)[0], word)

    def test_a_missing_field_still_goes_to_review(self):
        _, f = read(lines("{}: {}", [r for r in ROWS if r[0] != "Consignee"] + [("Consignee", "____")]))
        self.assertTrue(f.has_missing_placeholder)

    def test_clean_text_is_unchanged(self):
        text = lines("{}: {}")
        self.assertEqual(normalize_document_text(text), text)


class Emails(unittest.TestCase):
    def test_cleanup(self):
        self.assertIn("please send shipping instructions", normalize_email_text("pls snd shpg instr b4 noon").lower())
        self.assertIn("invoice", normalize_email_text("the incorrect in-\nvoice"))
        self.assertIn("password", normalize_email_text("your p a s s w o r d"))
        self.assertIn("duplicate invoice", normalize_email_text("the duplciate invocie"))
        self.assertIn("shipping instructions", normalize_email_text("Kindlysubmittheshippinginstructions"))

    def test_quoted_metadata_is_ignored(self):
        body = "X-Filter-Rule: SPAM\nVessel sailed on time.\n--\nTeams: billing query / request SI"
        self.assertEqual(strip_quoted_metadata(body).strip(), "Vessel sailed on time.")

    def test_classifier_uses_both(self):
        from sdoc_classifier import EmailClassifier

        c = EmailClassifier(use_llm=False)
        e = lambda body: {"subject": "Hello", "body": body, "attachments": []}  # noqa: E731
        self.assertEqual(c.classify_email(e("pls snd shpg instr today")), "SI_REQUEST")
        self.assertEqual(c.classify_email(e("Pls corect the duplciate invocie.")), "INVOICE_QUERY")
        self.assertEqual(c.classify_email(e("Confrm ur passwrod now or ur accnt is suspened")), "SPAM")
        self.assertEqual(c.classify_email(e("Berth window unchanged.\n--\nQueues: billing query / request SI")), "GENERAL")


if __name__ == "__main__":
    unittest.main()
