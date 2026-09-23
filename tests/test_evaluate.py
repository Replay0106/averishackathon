"""The evaluation script's metrics and its LLM comparison path (with a fake model, no network)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import evaluate as ev


def row(eid, key_status, status, key_fields=(), fields=(), cat="BL_COMPARISON"):
    return {"id": eid, "key_category": cat, "category": cat, "status": status, "expected_gemini_status": key_status,
            "expected_local_status": key_status, "key_fields": list(key_fields), "defect_fields": list(fields)}


class Metrics(unittest.TestCase):
    def test_confusion_and_per_class(self):
        pairs = [("A", "A"), ("A", "B"), ("B", "B"), ("B", "B"), ("A", "zzz")]
        m = ev.confusion(pairs, ["A", "B"])
        self.assertEqual(m["A"], {"A": 1, "B": 1, "OTHER": 1})
        self.assertEqual(m["B"], {"A": 0, "B": 2, "OTHER": 0})
        s = ev.per_class(m)
        self.assertAlmostEqual(s["A"]["recall"], 1 / 3)
        self.assertAlmostEqual(s["A"]["precision"], 1.0)
        self.assertAlmostEqual(s["B"]["precision"], 2 / 3)
        self.assertEqual(s["A"]["support"], 3)
        self.assertAlmostEqual(ev.accuracy(pairs), 3 / 5)

    def test_status_outcomes_name_each_error_kind(self):
        rows = [row("ok", "OK", "OK"), row("fc", "MISMATCH", "OK"), row("fa", "OK", "MISMATCH"),
                row("ur", "OK", "NEEDS_REVIEW"), row("mr", "NEEDS_REVIEW", "MISMATCH"), row("rv", "NEEDS_REVIEW", "OK"),
                row("x", "NOT_APPLICABLE", "NOT_APPLICABLE", cat="SPAM")]
        o = ev.status_outcomes(rows, "expected_gemini_status")
        self.assertEqual(o["checks"], 6)
        self.assertEqual(o["correct"], 1)
        self.assertEqual(o["false_clears"], ["fc", "rv"])
        self.assertEqual(o["missed_mismatches"], ["fc"])
        self.assertEqual(o["false_alarms"], ["fa"])
        self.assertEqual(o["unneeded_reviews"], ["ur"])
        self.assertEqual(o["missed_reviews"], ["mr", "rv"])
        self.assertAlmostEqual(o["review_rate"], 1 / 6)

    def test_field_scores_only_on_agreed_mismatches(self):
        rows = [row("a", "MISMATCH", "MISMATCH", ["consignee"], ["consignee", "shipper"]),
                row("b", "MISMATCH", "MISMATCH", ["gross_weight_kg"], ["gross_weight_kg"]),
                row("c", "MISMATCH", "OK", ["shipper"], [])]
        f = ev.field_scores(rows, "expected_gemini_status")
        self.assertEqual((f["compared"], f["exact_set_match"]), (2, 1))
        self.assertEqual(f["per_field"]["shipper"], {"tp": 0, "fp": 1, "fn": 0, "precision": 0.0, "recall": None})
        self.assertEqual(f["per_field"]["consignee"]["tp"], 1)

    def test_percentile_and_sample(self):
        self.assertEqual(ev.percentile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(ev.percentile([], 0.9), 0.0)
        rows = [{"id": f"e{i}", "key_category": "SPAM" if i < 80 else "GENERAL"} for i in range(100)]
        s = ev.stratified_sample(rows, 10)
        self.assertEqual(sum(r["key_category"] == "SPAM" for r in s), 8)
        self.assertEqual(s, ev.stratified_sample(rows, 10))  # deterministic


class FakeModels:
    def __init__(self, answers, fail_first=False):
        self.answers, self.fail_first, self.prompts = answers, fail_first, []

    def generate_content(self, model, contents, config):
        self.prompts.append(contents)
        if self.fail_first and len(self.prompts) == 1:
            raise RuntimeError("quota")
        ids = [line[4:] for line in contents.splitlines() if line.startswith("id: ")]
        usage = SimpleNamespace(prompt_token_count=100, candidates_token_count=10, thoughts_token_count=5)
        return SimpleNamespace(text=json.dumps({i: self.answers[i] for i in ids}), usage_metadata=usage)


class LLMPath(unittest.TestCase):
    def make(self, models):
        c = ev.LLMClassifier.__new__(ev.LLMClassifier)
        c.client = SimpleNamespace(models=models)
        c.model, c.batch, c.calls = "fake", 2, []
        c.cache_path = Path(tempfile.mkdtemp()) / "cache.json"
        c.cache = {}
        return c

    def test_batches_parse_cache_and_retry(self):
        emails = [(f"e{i}", {"subject": "s", "body": "b", "attachments": []}) for i in range(3)]
        models = FakeModels({"e0": "spam", "e1": "GENERAL", "e2": "not-a-category"}, fail_first=True)
        c = self.make(models)
        out = c.classify(emails)
        self.assertEqual(out, {"e0": "SPAM", "e1": "GENERAL", "e2": "ERROR"})
        self.assertEqual(sum(1 for x in c.calls if not x["ok"]), 1)  # the failed batch is retried per email
        cached = json.loads(c.cache_path.read_text()).values()
        self.assertIn("SPAM", [v["label"] for v in cached])
        self.assertEqual(c.costs["e0"]["prompt_tokens"], 100.0)  # the retry answered e0 alone, so it carries the whole call
        n = len(models.prompts)
        self.assertEqual(c.classify(emails[:2]), {"e0": "SPAM", "e1": "GENERAL"})
        self.assertEqual(len(models.prompts), n)  # answered from the cache
        self.assertEqual(set(c.costs), {"e0", "e1"})  # and the cached answers still report their cost
        ok = [x for x in c.calls if x["ok"]]
        self.assertEqual(ok[0]["output_tokens"], 15)


if __name__ == "__main__":
    unittest.main()
