"""Ask Navis: every answer is read from the results it is given, and unknown questions get help, not a guess."""
import os
import unittest
from datetime import datetime, timezone

import sdoc_copilot as cp


def row(eid, shipment, status="OK", category="BL_COMPARISON", fields=(), reason=None, sender="a@x.com",
        amendment=None, resolution=None, carrier="Evergreen"):
    return {"id": eid, "shipment": shipment, "sender": sender, "category": category, "status": status,
            "review_reason": reason, "defect_fields": list(fields), "attachments": [f"{eid}_SI.pdf", f"{eid}_BL.pdf"],
            "meta": {"carrier": carrier}, "amendment": amendment, "resolution": resolution, "confidence": 0.9,
            "case": None if status == "OK" else {"can_send": True, "block_reason": None}}


SENT_TODAY = {"recipient": "a@x.com", "at": "2026-09-23T02:00:00Z", "auto": True}
SENT_EARLIER = {"recipient": "b@y.com", "at": "2026-09-20T09:00:00Z", "auto": False}

ROWS = [
    row("e1", "SHP-1001", "MISMATCH", fields=["gross_weight_kg"], amendment=SENT_TODAY),
    row("e2", "SHP-1002", "MISMATCH", fields=["gross_weight_kg", "consignee"], sender="b@y.com", amendment=SENT_EARLIER),
    row("e3", "SHP-1003", "MISMATCH", fields=["container_count"], sender="b@y.com"),
    row("e4", "SHP-1004", "NEEDS_REVIEW", reason="missing_attachment", sender="b@y.com"),
    row("e5", "SHP-1005", "NEEDS_REVIEW", reason="unreadable", resolution={"action": "OVERRIDE_RESULT", "at": "2026-09-22T00:00:00Z"}),
    row("e6", "SHP-1006"),
    row("e7", "SHP-1007", category="GENERAL_INQUIRY"),
]


def comparison(r):
    out = []
    for key, label in cp.FIELD_LABELS.items():
        bad = key in r["defect_fields"]
        missing = r["status"] == "NEEDS_REVIEW" and key == "consignee"
        out.append({"key": key, "label": label, "si": "A", "bl": "B" if bad else "A", "match": not bad and not missing, "missing": missing,
                    "si_evidence": None, "bl_evidence": None})
    return out


def source(rows=ROWS):
    by_id = {r["id"]: r for r in rows}
    return cp.Source(
        dataset="Test inbox",
        email_ids=lambda: list(by_id),
        shipment_to_email=lambda sid: next((r["id"] for r in rows if r["shipment"] == sid), None),
        detail=lambda eid: {**by_id[eid], "comparison": comparison(by_id[eid])},
        rows=lambda: rows,
    )


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def ask(q, ctx=None, tz=0):
    return cp.answer(q, source(), context=ctx, tz_minutes=tz, now=NOW)


class ShipmentQuestions(unittest.TestCase):
    def test_mismatch_lists_every_differing_field(self):
        a = ask("Why is SHP-1002 flagged?")
        self.assertEqual(a["kind"], "mismatch")
        self.assertEqual([i["key"] for i in a["issues"]], ["consignee", "gross_weight_kg"])
        self.assertIn("by a reviewer to b@y.com", a["handling"])
        self.assertIn("Wait for the corrected", a["recommendation"])

    def test_unsent_mismatch_recommends_sending(self):
        a = ask("what's wrong with shp 1003")
        self.assertEqual(a["kind"], "mismatch")
        self.assertIn("Send the amendment", a["recommendation"])
        self.assertIn("Waiting on a person", a["handling"])

    def test_shipment_number_forms_and_email_ids(self):
        for q in ("SHP-1001", "shp1001", "SHP 1001", "shipment 1001", "tell me about e1"):
            self.assertEqual(ask(q)["email"], "e1", q)

    def test_review_clear_and_other_categories(self):
        self.assertEqual(ask("SHP-1004")["kind"], "review")
        self.assertEqual(ask("SHP-1004")["missing"], ["Consignee"])
        self.assertEqual(ask("SHP-1006")["kind"], "clear")
        self.assertEqual(ask("SHP-1007")["kind"], "not_comparison")

    def test_unknown_shipment_is_not_guessed(self):
        a = ask("Why is SHP-9999 flagged?")
        self.assertEqual(a["kind"], "help")
        self.assertIn("couldn't find", a["message"])

    def test_follow_up_uses_context(self):
        self.assertEqual(ask("why is it flagged?", ctx="e3")["email"], "e3")
        self.assertEqual(ask("was that one sent?", ctx="e1")["email"], "e1")

    def test_follow_up_needs_a_valid_context_and_a_single_subject(self):
        self.assertNotEqual(ask("why is it flagged?", ctx="nope").get("email"), "nope")
        # A question about many shipments is answered for the dataset, even with a context.
        self.assertEqual(ask("how many mismatches does it have overall", ctx="e1")["kind"], "dataset")


class DatasetQuestions(unittest.TestCase):
    def test_counts_match_the_rows(self):
        a = ask("How many mismatches are there?")
        self.assertEqual((a["title"], a["total"]), ("Mismatches", 3))
        self.assertEqual(ask("how many need review")["total"], 2)
        self.assertEqual(ask("which shipments are clear")["total"], 1)

    def test_cases_tabs(self):
        self.assertEqual({i["email"] for i in ask("Which cases need a person?")["items"]}, {"e3", "e4"})
        self.assertEqual({i["email"] for i in ask("which are awaiting a reply")["items"]}, {"e1", "e2"})
        self.assertEqual([i["email"] for i in ask("show resolved cases")["items"]], ["e5"])
        self.assertEqual(ask("which cases can't be sent")["title"], "Needs a person")

    def test_amendments_sent_today_uses_the_browser_timezone(self):
        a = ask("What amendments did we send today?")
        self.assertEqual([i["email"] for i in a["items"]], ["e1"])
        self.assertIn("1 automatically, 0 by a reviewer", a["message"])
        # 02:00 UTC on the 23rd is still the 22nd at UTC-5, and "now" (12:00 UTC) is the 23rd there too.
        self.assertEqual(ask("what did we send today", tz=-300)["total"], 0)
        self.assertEqual(ask("what did we send?")["total"], 2)

    def test_rankings(self):
        a = ask("Which sender has the most errors?")
        self.assertEqual(a["breakdown"][0], {"label": "b@y.com", "count": 3, "note": "2 mismatch · 1 review"})
        self.assertTrue(a["message"].startswith("b@y.com has"))
        f = ask("What is the most common mismatch field?")
        self.assertEqual(f["breakdown"][0], {"label": "Gross Weight", "count": 2})
        self.assertEqual(ask("mismatches by carrier")["breakdown"], [{"label": "Evergreen", "count": 5}])
        r = ask("why do cases need review")
        self.assertEqual({b["label"] for b in r["breakdown"]}, {"Missing attachment", "Unreadable file"})

    def test_ties_are_reported_as_ties(self):
        rows = [row("a", "SHP-1", "MISMATCH", sender="x@a.com"), row("b", "SHP-2", "MISMATCH", sender="y@b.com")]
        a = cp.answer("who has the most errors", source(rows), now=NOW)
        self.assertIn("share the most", a["message"])

    def test_field_filter(self):
        a = ask("Which shipments have a gross weight mismatch?")
        self.assertEqual({i["email"] for i in a["items"]}, {"e1", "e2"})
        c = ask("how many have a consignee mismatch")
        self.assertEqual(c["total"], 1)
        self.assertIn("out of 3 mismatches", c["message"])

    def test_overview(self):
        stats = {s["label"]: s["value"] for s in ask("give me an overview")["stats"]}
        self.assertEqual(stats, {"Emails": 7, "SI/BL checks": 6, "Clear": 1, "Awaiting documents": 0, "Mismatches": 3,
                                 "Need review": 2, "Amendments sent": 2, "Needs a person": 2, "Resolved": 1})

    def test_requests_without_documents_are_not_reported_as_clear(self):
        rows = ROWS + [{**row("e8", "SHP-1008"), "awaiting_documents": True}]
        src = source(rows)
        self.assertEqual(cp.answer("SHP-1008", src, now=NOW)["kind"], "awaiting")
        self.assertEqual(cp.answer("which shipments are clear", src, now=NOW)["total"], 1)
        a = cp.answer("which requests have no documents yet", src, now=NOW)
        self.assertEqual([i["email"] for i in a["items"]], ["e8"])
        stats = {s["label"]: s["value"] for s in cp.answer("overview", src, now=NOW)["stats"]}
        self.assertEqual((stats["SI/BL checks"], stats["Awaiting documents"]), (6, 1))

    def test_long_lists_are_cut_with_a_count(self):
        rows = [row(f"m{i}", f"SHP-{2000 + i}", "MISMATCH", fields=["consignee"]) for i in range(12)]
        a = cp.answer("list the mismatches", source(rows), now=NOW)
        self.assertEqual((a["total"], len(a["items"]), a["more"]), (12, cp.LIST_LIMIT, 12 - cp.LIST_LIMIT))

    def test_unrelated_question_gets_help_with_real_examples(self):
        a = ask("what's the weather in Penang")
        self.assertEqual(a["kind"], "help")
        self.assertIn("Why is SHP-1001 flagged?", a["examples"])
        self.assertIn("Which shipments have a gross weight mismatch?", a["examples"])
        self.assertEqual(ask("")["kind"], "help")


class GeminiTranslation(unittest.TestCase):
    """Gemini only picks a supported query; the rules then compute the answer from the results."""

    def setUp(self):
        cp._translations.clear()
        self.calls = []

    def translator(self, query, ok=True):
        def t(question, has_previous):
            self.calls.append((question, has_previous))
            return (query, {"ok": ok, "model": "fake-model"})
        return t

    def ask_llm(self, q, query, ctx=None, ok=True):
        return cp.answer_with_llm(q, source(), context=ctx, now=NOW, translator=self.translator(query, ok))

    def test_every_supported_query_reaches_the_intended_answer(self):
        expect = {
            ("list", "needs_person"): "Needs a person", ("count", "needs_person"): "Needs a person",
            ("list", "awaiting"): "Awaiting the sender's reply", ("count", "awaiting"): "Awaiting the sender's reply",
            ("list", "resolved"): "Resolved cases", ("count", "resolved"): "Resolved cases",
            ("list", "review"): "Cases needing review", ("count", "review"): "Cases needing review",
            ("list", "mismatch"): "Mismatches", ("count", "mismatch"): "Mismatches",
            ("list", "clear"): "Clear shipments", ("count", "clear"): "Clear shipments",
        }
        for (intent, status), title in expect.items():
            a = ask(cp.canonical_question({"intent": intent, "status": status}))
            self.assertEqual(a.get("title"), title, (intent, status))
        for query, title in [({"intent": "sent"}, "Amendments sent"), ({"intent": "sent", "today": True}, "Amendments sent today"),
                             ({"intent": "rank_senders"}, "Cases by sender"), ({"intent": "rank_carriers"}, "Cases by carrier"),
                             ({"intent": "rank_fields"}, "Mismatches by field"), ({"intent": "review_reasons"}, "Why cases need review"),
                             ({"intent": "overview"}, "Overview")]:
            self.assertEqual(ask(cp.canonical_question(query)).get("title"), title, query)
        for field, label in cp.FIELD_LABELS.items():
            for count in (False, True):
                a = ask(cp.canonical_question({"intent": "field", "field": field, "count": count}))
                self.assertEqual(a.get("title"), f"{label} mismatches", (field, count))
        self.assertEqual(ask(cp.canonical_question({"intent": "shipment", "shipment": "SHP-1002"}))["email"], "e2")
        self.assertEqual(ask(cp.canonical_question({"intent": "shipment", "shipment": "e3"}))["email"], "e3")

    def test_malformed_or_unsupported_queries_have_no_question(self):
        for q in ({"intent": "field", "field": "hs_code"}, {"intent": "list", "status": "lost"}, {"intent": "unsupported"},
                  {"intent": "shipment", "shipment": "drop table; --"}, {}):
            self.assertIsNone(cp.canonical_question(q), q)

    def test_recognised_questions_never_call_gemini(self):
        a = self.ask_llm("How many mismatches are there?", {"intent": "unsupported"})
        self.assertEqual(a["total"], 3)
        self.assertNotIn("interpreted_as", a)
        self.assertEqual(self.calls, [])

    def test_free_form_question_is_translated_then_answered_from_results(self):
        a = self.ask_llm("what shipments are stuck because the weight is off?", {"intent": "field", "field": "gross_weight_kg"})
        self.assertEqual(a["interpreted_as"], "Which shipments have a gross weight mismatch?")
        self.assertEqual(a["via"], "fake-model")
        self.assertEqual({i["email"] for i in a["items"]}, {"e1", "e2"})  # computed by the rules, not by the model

    def test_follow_up_translation_uses_the_previous_shipment(self):
        a = self.ask_llm("hmm and what about that?", {"intent": "follow_up"}, ctx="e3")
        self.assertEqual((a["email"], a["interpreted_as"]), ("e3", "Why is it flagged?"))
        self.assertEqual(self.calls, [("hmm and what about that?", True)])

    def test_unsupported_failed_or_unanswerable_translations_fall_back_to_help(self):
        self.assertIn("outside", self.ask_llm("will it rain in Penang?", {"intent": "unsupported"})["message"])
        cp._translations.clear()
        failed = self.ask_llm("will it rain in Penang?", None, ok=False)
        self.assertEqual(failed["kind"], "help")
        self.assertNotIn("via", failed)
        cp._translations.clear()
        # A follow-up with no previous shipment cannot be answered, so the original help reply is kept.
        self.assertEqual(self.ask_llm("and that?", {"intent": "follow_up"})["kind"], "help")

    def test_translations_are_cached(self):
        q = {"intent": "overview"}
        self.ask_llm("how is my desk doing", q)
        self.ask_llm("How is my  desk doing", q)
        self.assertEqual(len(self.calls), 1)


class ClassifierFallback(unittest.TestCase):
    def setUp(self):
        import sdoc_llm
        from unittest import mock

        self.mock, self.llm = mock, sdoc_llm
        self.email = {"subject": "Quick note", "body": "Just checking in about next week.", "attachments": []}

    def classify(self, env, reply):
        from sdoc_classifier import EmailClassifier

        with self.mock.patch.dict("os.environ", env), \
                self.mock.patch.object(self.llm, "available", return_value=True), \
                self.mock.patch.object(self.llm, "generate_json", return_value=reply) as gen:
            return EmailClassifier().classify_email(self.email), gen.call_count

    def test_switched_off(self):
        self.assertEqual(self.classify({"NAVIS_LLM_CLASSIFIER": "0"}, ({"e1": "SPAM"}, {"ok": True})), ("GENERAL", 0))

    def test_on_by_default_when_a_key_is_configured(self):
        with self.mock.patch.dict("os.environ"):
            os.environ.pop("NAVIS_LLM_CLASSIFIER", None)
            self.assertEqual(self.classify({}, ({"e1": "SPAM"}, {"ok": True})), ("SPAM", 1))

    def test_gemini_cannot_start_a_document_check(self):
        self.assertEqual(self.classify({"NAVIS_LLM_CLASSIFIER": "1"}, ({"e1": "BL_COMPARISON"}, {"ok": True})), ("GENERAL", 1))

    def test_when_enabled_only_unmatched_emails_are_sent(self):
        self.assertEqual(self.classify({"NAVIS_LLM_CLASSIFIER": "1"}, ({"e1": "si_request"}, {"ok": True})), ("SI_REQUEST", 1))
        self.assertEqual(self.classify({"NAVIS_LLM_CLASSIFIER": "1"}, ({"e1": "nonsense"}, {"ok": True}))[0], "GENERAL")
        self.assertEqual(self.classify({"NAVIS_LLM_CLASSIFIER": "1"}, (None, {"ok": False}))[0], "GENERAL")
        self.email = {"subject": "Invoice 123 query", "body": "Please check the invoice charges.", "attachments": []}
        self.assertEqual(self.classify({"NAVIS_LLM_CLASSIFIER": "1"}, ({"e1": "SPAM"}, {"ok": True})), ("INVOICE_QUERY", 0))


if __name__ == "__main__":
    unittest.main()
