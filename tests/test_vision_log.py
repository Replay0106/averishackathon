"""The extractor records latency, tokens and outcome for each live vision call, without document content."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from sdoc_extractor import FieldExtractor


class TestVisionCallLog(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.extractor = FieldExtractor(api_key="", cache_path=str(self.dir / "vision_cache.json"))
        self.log = self.dir / "vision_calls.jsonl"

    def entries(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_successful_call_records_tokens_and_latency(self):
        resp = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=1200, candidates_token_count=90, total_token_count=1290))
        self.extractor._log_call("a" * 64, "gemini-test", time.perf_counter(), resp=resp, image_bytes=4096)
        (e,) = self.entries()
        self.assertEqual((e["prompt_tokens"], e["output_tokens"], e["total_tokens"]), (1200, 90, 1290))
        self.assertEqual((e["outcome"], e["model"], e["image_bytes"]), ("ok", "gemini-test", 4096))
        self.assertGreaterEqual(e["latency_ms"], 0)
        self.assertEqual(e["doc"], "a" * 12)

    def test_failed_call_records_the_error_type(self):
        self.extractor._log_call("b" * 64, "gemini-test", time.perf_counter(), error=RuntimeError("quota"))
        (e,) = self.entries()
        self.assertEqual(e["outcome"], "RuntimeError")
        self.assertIsNone(e["total_tokens"])

    def test_logging_failure_never_breaks_extraction(self):
        self.extractor.cache_path = Path("/nonexistent-dir-\0/x.json")
        self.extractor._log_call("c" * 64, "m", time.perf_counter())


if __name__ == "__main__":
    unittest.main()
