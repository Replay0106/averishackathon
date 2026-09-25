"""The Gemini classifier fallback: batched requests, a denied key is dropped, a rate-limited key rests, answers are
remembered across restarts, and the API never waits for Gemini on a request."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import sdoc_llm
from sdoc_classifier import EmailClassifier, LabelCache


def email(n):
    return {"email_id": f"x{n}", "subject": f"Note {n}", "body": f"Vessel update number {n}.", "attachments": []}


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = mock.Mock(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0)


class KeyHealth(unittest.TestCase):
    def setUp(self):
        sdoc_llm._denied.clear()
        sdoc_llm._resting.clear()
        self.addCleanup(sdoc_llm._denied.clear)
        self.addCleanup(sdoc_llm._resting.clear)
        self.calls = []

    def run_with(self, errors, keys="k1,k2"):
        def client(key):
            def generate_content(**_):
                self.calls.append(key)
                if key in errors:
                    raise RuntimeError(errors[key])
                return FakeResponse('{"e1": "SPAM"}')
            return mock.Mock(models=mock.Mock(generate_content=generate_content))
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": keys}), mock.patch.object(sdoc_llm, "_client", client):
            return sdoc_llm.generate_json("p")

    def test_denied_key_is_skipped_from_then_on(self):
        data, rec = self.run_with({"k1": "403 PERMISSION_DENIED. project denied"})
        self.assertEqual((data, rec["ok"]), ({"e1": "SPAM"}, True))
        self.calls.clear()
        self.run_with({})
        self.assertEqual(self.calls, ["k2"])

    def test_rate_limited_key_rests_for_the_delay_gemini_gives(self):
        self.run_with({"k1": "429 RESOURCE_EXHAUSTED {'retryDelay': '25s'}"})
        self.assertAlmostEqual(sdoc_llm._resting["k1"] - sdoc_llm.time.monotonic(), 26, delta=1)
        self.calls.clear()
        self.run_with({})
        self.assertEqual(self.calls, ["k2"])

    def test_no_waiting_on_a_request_path(self):
        data, rec = self.run_with({"k1": "429 {'retryDelay': '25s'}", "k2": "429 {'retryDelay': '25s'}"})
        self.assertIsNone(data)
        self.assertLess(rec["latency_ms"], 1000)
        self.assertGreater(rec["retry_after_s"], 20)


class Batching(unittest.TestCase):
    def test_one_request_per_batch_and_bl_checks_refused(self):
        emails = [email(n) for n in range(45)]
        reply = {f"e{n}": ("BL_COMPARISON" if n == 1 else "SPAM") for n in range(1, 31)}
        with mock.patch.object(sdoc_llm, "available", return_value=True), \
                mock.patch.object(sdoc_llm, "generate_json", return_value=(reply, {"ok": True})) as gen:
            labels = EmailClassifier(use_llm=True).classify_many(emails)
        self.assertEqual(gen.call_count, 2)  # 45 unmatched emails, batches of 30
        self.assertEqual(labels[0], "GENERAL")  # Gemini may not start a document check
        self.assertEqual(labels[1:30], ["SPAM"] * 29)

    def test_reply_shapes(self):
        from sdoc_classifier import reply_labels

        want = {"e1": "SPAM", "e2": "GENERAL"}
        for data in (want, [{"id": "e1", "category": "SPAM"}, {"id": "e2", "category": "GENERAL"}],
                     {"emails": [{"email_id": "e1", "label": "SPAM"}, {"id": "e2", "category": "GENERAL"}]},
                     {"categories": want}):
            self.assertEqual(reply_labels(data), want, data)

    def test_a_short_reply_is_asked_again(self):
        replies = iter([({"note": "x"}, {"ok": True}), ([{"id": "e1", "category": "SI REQUEST"}], {"ok": True})])
        with mock.patch.object(sdoc_llm, "generate_json", side_effect=lambda *a, **k: next(replies)) as gen:
            self.assertEqual(EmailClassifier(use_llm=True).classify_email(email(1)), "SI_REQUEST")
        self.assertEqual(gen.call_count, 2)

    def test_answers_survive_a_restart(self):
        path = Path(tempfile.mkdtemp()) / "labels.json"
        with mock.patch.object(sdoc_llm, "generate_json", return_value=({"e1": "SPAM"}, {"ok": True})) as gen:
            self.assertEqual(EmailClassifier(use_llm=True, cache=LabelCache(str(path))).classify_email(email(1)), "SPAM")
            self.assertEqual(EmailClassifier(use_llm=True, cache=LabelCache(str(path))).classify_email(email(1)), "SPAM")
        self.assertEqual(gen.call_count, 1)

    def test_failures_are_not_remembered(self):
        c = EmailClassifier(use_llm=True)
        with mock.patch.object(sdoc_llm, "generate_json", return_value=(None, {"ok": False})):
            self.assertEqual(c.classify_email(email(1)), "GENERAL")
        with mock.patch.object(sdoc_llm, "generate_json", return_value=({"e1": "SPAM"}, {"ok": True})):
            self.assertEqual(c.classify_email(email(1)), "SPAM")


class ApiDoesNotWait(unittest.TestCase):
    def test_unmatched_email_is_queued_and_its_result_refreshed(self):
        import api.main as m

        d = mock.Mock(cache={"x1": {"category": "GENERAL"}})
        asked = threading.Event()

        def llm_labels(batch, wait_s):
            asked.set()
            return {0: "SPAM"}

        backfill = m._GeminiBackfill()
        with mock.patch.object(m.classifier, "_llm_enabled", return_value=True), \
                mock.patch.object(m.classifier, "cached_llm_label", return_value=None), \
                mock.patch.object(m.classifier, "llm_labels", side_effect=llm_labels), \
                mock.patch.object(m, "gemini_backfill", backfill):
            t0 = time.perf_counter()
            self.assertEqual(m.classify(d, "x1", email(1)), ("GENERAL", "pending"))
            self.assertLess(time.perf_counter() - t0, 0.2)  # the request did not wait for Gemini
            self.assertTrue(asked.wait(5))
            deadline = time.monotonic() + 5
            while "x1" in d.cache and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertNotIn("x1", d.cache)  # the next read recomputes with Gemini's answer


if __name__ == "__main__":
    unittest.main()
