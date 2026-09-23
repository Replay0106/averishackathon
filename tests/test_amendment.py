"""Automatic amendment policy, message and durable outbox (sending is simulated: outbox record + ledger entry)."""
import os
import tempfile
import unittest

from sdoc_amendment import (
    HOLD, NEVER, NONE, SEND, AmendmentOutbox, build_message, decide, send_block, sender_address,
)


def result(defects, status="MISMATCH", sender="Hana <hana@shipping-team.example>", **extra):
    comp = [{"key": k, "label": k, "si": si, "bl": bl, "match": False, "missing": False}
            for k, (si, bl) in {
                "gross_weight_kg": (68137.0, 71197.0), "port_of_loading": ("PORT KLANG", "PORT KELANG X"),
                "container_count": (3, 4), "notify_party": ("ACME", "OTHER"), "consignee": ("A", "B"), "shipper": ("S", "T"),
                "port_of_discharge": ("HCMC", "DANANG"),
            }.items() if k in defects]
    r = {"id": "email_1", "shipment": "SHP-2001", "category": "BL_COMPARISON", "status": status, "sender": sender,
         "subject": "Please compare", "defect_fields": list(defects), "comparison": comp,
         "review_reason": None, "meta": {"booking": "BK123"}}
    r.update(extra)
    return r


class TestPolicy(unittest.TestCase):
    def test_mismatch_on_any_of_the_seven_fields_is_sent(self):
        for f in (["gross_weight_kg"], ["port_of_loading", "container_count"], ["consignee"], ["shipper", "gross_weight_kg"], ["notify_party"]):
            self.assertEqual(decide(result(f))[0], SEND, f)

    def test_unrecognised_field_waits_for_a_person(self):
        dec, reason = decide(result(["hs_code"]))
        self.assertEqual(dec, HOLD)
        self.assertIn("needs a person", reason)

    def test_review_cases_wait_for_a_person(self):
        self.assertEqual(decide(result([], status="NEEDS_REVIEW", review_reason="unreadable"))[0], HOLD)

    def test_ok_and_other_categories_need_no_amendment(self):
        self.assertEqual(decide(result([], status="OK"))[0], NONE)
        self.assertEqual(decide(result(["gross_weight_kg"], category="SPAM"))[0], NONE)

    def test_rejected_sender_is_never_contacted(self):
        dec, reason = decide(result(["gross_weight_kg"]), {"accepted": False, "held": False, "failed_gate": "sender_trust"})
        self.assertEqual(dec, NEVER)
        self.assertIn("sender_trust", reason)

    def test_mismatch_hold_by_gateway_does_not_block(self):
        self.assertEqual(decide(result(["gross_weight_kg"]), {"accepted": False, "held": True, "failed_gate": "discrepancy_plausibility"})[0], SEND)

    def test_mismatch_rejection_from_an_unknown_sender_does_not_block(self):
        # Gate 8 rejects a mismatch from a sender with no trust history; that judges the document, not the sender.
        verdict = {"accepted": False, "held": False, "failed_gate": "discrepancy_plausibility"}
        self.assertEqual(decide(result(["gross_weight_kg"]), verdict)[0], SEND)
        self.assertIsNone(send_block(result(["gross_weight_kg"]), verdict))
        blocked = {"accepted": False, "held": False, "failed_gate": "authentication"}
        self.assertEqual(decide(result(["gross_weight_kg"]), blocked)[0], NEVER)
        self.assertIn("authentication", send_block(result(["gross_weight_kg"]), blocked))

    def test_missing_sender_address_is_never_sent(self):
        self.assertEqual(decide(result(["gross_weight_kg"], sender=""))[0], NEVER)
        self.assertIsNone(sender_address("not an address"))
        self.assertEqual(sender_address("Hana <hana@x.example>"), "hana@x.example")


class TestMessage(unittest.TestCase):
    def test_addressed_to_the_sender_and_lists_each_difference(self):
        m = build_message(result(["gross_weight_kg", "container_count"]))
        self.assertEqual(m["recipient"], "hana@shipping-team.example")
        self.assertIn("SHP-2001", m["subject"])
        self.assertIn("68,137 kg", m["body"])
        self.assertIn("71,197 kg", m["body"])
        self.assertIn("Container Count", m["body"])
        self.assertTrue(m["body"].startswith("Dear Hana,"))


class TestOutbox(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "a.db")

    def test_same_amendment_is_never_recorded_twice_even_after_restart(self):
        msg = build_message(result(["gross_weight_kg"]))
        first = AmendmentOutbox(self.path).record("ds", "email_1", "SHP-2001", msg, SEND, "why")
        self.assertIsNotNone(first)
        again = AmendmentOutbox(self.path)  # a restart
        self.assertIsNone(again.record("ds", "email_1", "SHP-2001", msg, SEND, "why"))
        self.assertEqual(again.get("ds", "email_1")["status"], "sent")

    def test_changed_differences_are_a_new_amendment(self):
        box = AmendmentOutbox(self.path)
        box.record("ds", "email_1", "SHP-2001", build_message(result(["gross_weight_kg"])), SEND, "why")
        newer = build_message(result(["gross_weight_kg", "container_count"]))
        self.assertIsNotNone(box.record("ds", "email_1", "SHP-2001", newer, SEND, "why"))

    def test_confirm_and_discard(self):
        box = AmendmentOutbox(self.path)
        row = box.record("ds", "email_1", "SHP-2001", build_message(result(["gross_weight_kg"])), SEND, "why")
        box.attach_block(row["id"], 7)
        self.assertTrue(box.set_status("ds", "email_1", "confirmed"))
        got = box.all("ds")["email_1"]
        self.assertEqual((got["status"], got["block_index"]), ("confirmed", 7))
        box.discard(row["id"])
        self.assertIsNone(box.get("ds", "email_1"))


class TestReviewerSend(unittest.TestCase):
    """A reviewer can send an amendment (or a request for missing documents) for a case held for a person."""

    @staticmethod
    def review(reason, **extra):
        r = {"id": "e1", "shipment": "SHP-9", "category": "BL_COMPARISON", "status": "NEEDS_REVIEW", "review_reason": reason,
             "sender": "Ann <ann@x.example>", "subject": "Please compare", "attachments": ["e1_SI.pdf", "e1_BL.pdf"],
             "meta": {"booking": "BK1"}, "si": {"is_legible": True}, "bl": {"is_legible": False}, "comparison": []}
        r.update(extra)
        return r

    def test_each_review_reason_has_its_own_request(self):
        from sdoc_amendment import build_manual_message
        expect = {
            "missing_attachment": "was not attached",
            "wrong_doc_type": "could not be identified",
            "unreadable": "We could not read e1_BL.pdf",
            "missing_value": "blank or unclear",
        }
        for reason, text in expect.items():
            extra = {"bl": None} if reason == "missing_attachment" else {}
            if reason == "missing_value":
                extra["comparison"] = [{"key": "notify_party", "si": "A", "bl": None, "missing": True}]
            m = build_manual_message(self.review(reason, **extra))
            self.assertIn(text, m["body"], reason)
            self.assertEqual(m["recipient"], "ann@x.example")
            self.assertTrue(m["fields"])

    def test_held_mismatch_uses_the_difference_list(self):
        from sdoc_amendment import build_manual_message
        m = build_manual_message(result(["consignee"]))
        self.assertIn("Consignee", m["body"])

    def test_send_block_reasons(self):
        from sdoc_amendment import send_block
        self.assertIsNone(send_block(self.review("unreadable")))
        self.assertIn("no usable sender", send_block(self.review("unreadable", sender="")))
        self.assertIn("sender_trust", send_block(self.review("unreadable"), {"accepted": False, "held": False, "failed_gate": "sender_trust"}))
        self.assertIsNone(send_block(self.review("unreadable"), {"accepted": False, "held": True, "failed_gate": "discrepancy_plausibility"}))


if __name__ == "__main__":
    unittest.main()
