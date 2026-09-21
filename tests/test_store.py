"""Persistent storage on Supabase (exercised against an in-memory backend, plus the HTTP shapes of the real one)."""
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import sdoc_store
from sdoc_amendment import SEND, build_message
from sdoc_store import (
    Conflict, DecisionStore, MemoryBackend, StoreError, SupabaseBackend, SupabaseLedger, SupabaseOutbox,
)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryBackend()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(sdoc_store.set_store, None)


class TestLedger(StoreCase):
    def test_chain_verifies_and_survives_a_new_instance(self):
        a = SupabaseLedger(self.backend)
        a.record_action("alice", "email_1", "AMENDMENT_DISPATCHED", {"fields": [{"key": "gross_weight_kg", "si": 68137.0, "bl": 71197.0}], "auto": True})
        a.record_action("bob", "email_2", "OVERRIDE_RESULT", {"note": "café ✓", "n": 3})
        b = SupabaseLedger(self.backend)  # a restart: nothing but the table survives
        self.assertEqual(len(b.blocks), 3)
        self.assertTrue(b.verify_integrity()[0])
        self.assertEqual(b.blocks[1]["details"]["fields"][0]["si"], 68137.0)

    def test_two_instances_share_one_chain(self):
        a, b = SupabaseLedger(self.backend), SupabaseLedger(self.backend)
        a.record_action("a", "e1", "X", {})
        b.record_action("b", "e2", "X", {})  # b's cache is stale: the slot is taken, it re-reads and appends after a
        a.record_action("a", "e3", "X", {})
        fresh = SupabaseLedger(self.backend)
        self.assertEqual([x["index"] for x in fresh.blocks], [0, 1, 2, 3])
        self.assertEqual([x["actor"] for x in fresh.blocks[1:]], ["a", "b", "a"])
        self.assertTrue(fresh.verify_integrity()[0])

    def test_tampering_with_a_stored_row_is_detected(self):
        a = SupabaseLedger(self.backend)
        a.record_action("a", "e1", "X", {"k": 1})
        self.backend.tables[sdoc_store.T_LEDGER][1]["details_json"] = json.dumps({"k": 2}, sort_keys=True)
        self.assertFalse(SupabaseLedger(self.backend).verify_integrity()[0])

    def test_genesis_is_created_once(self):
        SupabaseLedger(self.backend)
        SupabaseLedger(self.backend)
        self.assertEqual(len(self.backend.tables[sdoc_store.T_LEDGER]), 1)


def result(defects=("gross_weight_kg",)):
    comp = [{"key": k, "label": k, "si": 1, "bl": 2, "match": False, "missing": False} for k in defects]
    return {"id": "e1", "shipment": "SHP-1", "category": "BL_COMPARISON", "status": "MISMATCH", "sender": "Ann <ann@x.example>",
            "subject": "s", "defect_fields": list(defects), "comparison": comp, "meta": {"booking": "BK1"}}


class TestOutbox(StoreCase):
    def test_same_amendment_is_recorded_once_across_restarts(self):
        msg = build_message(result())
        first = SupabaseOutbox(self.backend).record("ds", "e1", "SHP-1", msg, SEND, "why")
        self.assertIsNotNone(first)
        self.assertIsNone(SupabaseOutbox(self.backend).record("ds", "e1", "SHP-1", msg, SEND, "why"))

    def test_status_block_and_discard(self):
        box = SupabaseOutbox(self.backend)
        row = box.record("ds", "e1", "SHP-1", build_message(result()), SEND, "why")
        box.attach_block(row["id"], 9)
        self.assertTrue(box.set_status("ds", "e1", "confirmed"))
        got = box.all("ds")["e1"]
        self.assertEqual((got["status"], got["block_index"], got["fields"][0]["key"]), ("confirmed", 9, "gross_weight_kg"))
        box.discard(row["id"])
        self.assertIsNone(box.get("ds", "e1"))
        self.assertFalse(box.set_status("ds", "e1", "confirmed"))


class TestDecisions(StoreCase):
    def test_decisions_persist_and_update(self):
        d = DecisionStore(self.backend)
        d.set("ds", "e1", {"action": "OVERRIDE_RESULT", "block": 4})
        d.set("ds", "e1", {"action": "AMENDMENT_CONFIRMED", "block": 5})
        d.set("other", "e9", {"action": "OVERRIDE_RESULT"})
        self.assertEqual(DecisionStore(self.backend).all("ds"), {"e1": {"action": "AMENDMENT_CONFIRMED", "block": 5}})
        d.clear("ds")
        self.assertEqual(DecisionStore(self.backend).all("ds"), {})
        self.assertEqual(len(DecisionStore(self.backend).all("other")), 1)

    def test_without_supabase_decisions_stay_in_memory(self):
        d = DecisionStore(None)
        d.set("ds", "e1", {"action": "X"})
        self.assertEqual(d.all("ds"), {"e1": {"action": "X"}})


class TestVisionAndKv(StoreCase):
    def test_vision_reads_survive_a_new_extractor(self):
        from sdoc_extractor import FieldExtractor
        sdoc_store.set_store(self.backend)
        FieldExtractor(api_key="")._cache_put("k" * 64, {"shipper": "ACME"})
        self.assertEqual(FieldExtractor(api_key="")._cache_get("k" * 64), {"shipper": "ACME"})

    def test_extractor_with_explicit_cache_path_stays_local(self):
        from sdoc_extractor import FieldExtractor
        sdoc_store.set_store(self.backend)
        ex = FieldExtractor(api_key="", cache_path=str(self.tmp / "c.json"))
        ex._cache_put("z" * 64, {"shipper": "ACME"})
        self.assertNotIn(sdoc_store.T_VCACHE, self.backend.tables)

    def test_call_log_is_stored(self):
        import time
        from sdoc_extractor import FieldExtractor
        sdoc_store.set_store(self.backend)
        FieldExtractor(api_key="")._log_call("a" * 64, "m", time.perf_counter(), error=RuntimeError("x"))
        self.assertEqual(self.backend.tables[sdoc_store.T_VCALLS][0]["outcome"], "RuntimeError")

    def test_kv(self):
        sdoc_store.set_store(self.backend)
        sdoc_store.kv_set("gmail_state:x", {"email_counter": 4})
        sdoc_store.kv_set("gmail_state:x", {"email_counter": 5})
        self.assertEqual(sdoc_store.kv_get("gmail_state:x"), {"email_counter": 5})

    def test_many_lookups_read_the_table_once(self):
        calls = []
        real = self.backend.select

        def counting(*a, **k):
            calls.append(a[0])
            return real(*a, **k)

        self.backend.select = counting
        sdoc_store.set_store(self.backend)
        sdoc_store.vision_put("known", {"shipper": "ACME"})
        for i in range(50):
            self.assertIsNone(sdoc_store.vision_get(f"missing-{i}"))
        self.assertEqual(sdoc_store.vision_get("known"), {"shipper": "ACME"})
        self.assertEqual(calls.count(sdoc_store.T_VCACHE), 1)

    def test_no_store_means_no_calls(self):
        sdoc_store.set_store(None)
        self.assertIsNone(sdoc_store.vision_get("k"))
        self.assertIsNone(sdoc_store.kv_get("k"))
        sdoc_store.vision_put("k", {})  # must not raise


def make_dataset(root: Path, n=3):
    (root / "inbox").mkdir(parents=True)
    (root / "attachments").mkdir()
    for i in range(1, n + 1):
        eid = f"email_{i:03d}"
        (root / "attachments" / f"{eid}_SI.txt").write_text(f"SHIPPING INSTRUCTION {i}", encoding="utf-8")
        (root / "inbox" / f"{eid}.json").write_text(json.dumps({"email_id": eid, "from": "a@b.c", "subject": "s", "body": "b", "attachments": [f"attachments/{eid}_SI.txt"]}), encoding="utf-8")


class TestDocuments(StoreCase):
    def test_pack_round_trip_restores_every_file(self):
        src = self.tmp / "src"
        make_dataset(src)
        packs = sdoc_store.save_pack(self.backend, "imp_1", src)
        self.assertEqual(packs, 1)
        dst = self.tmp / "dst"
        sdoc_store.hydrate(self.backend, "imp_1", dst)
        self.assertEqual(sorted(p.name for p in (dst / "inbox").iterdir()), sorted(p.name for p in (src / "inbox").iterdir()))
        self.assertEqual((dst / "attachments" / "email_002_SI.txt").read_text(encoding="utf-8"), "SHIPPING INSTRUCTION 2")

    def test_large_datasets_are_split_into_several_packs(self):
        src = self.tmp / "big"
        make_dataset(src, 4)
        parts = sdoc_store.zip_parts([(f"attachments/{p.name}", p) for p in (src / "attachments").iterdir()], limit=40)
        self.assertGreater(len(parts), 1)

    def test_emails_added_later_are_kept_and_restored(self):
        src = self.tmp / "gm"
        make_dataset(src, 1)
        sdoc_store.save_extra_email(self.backend, "gmail_live", src, "email_001")
        dst = self.tmp / "restored"
        sdoc_store.hydrate(self.backend, "gmail_live", dst)
        self.assertTrue((dst / "inbox" / "email_001.json").is_file())
        self.assertTrue((dst / "attachments" / "email_001_SI.txt").is_file())

    def test_unsafe_archive_paths_are_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.txt", "x")
            z.writestr("inbox/ok.json", "{}")
        dest = self.tmp / "out"
        n = sdoc_store.unzip_safely(buf.getvalue(), dest)
        self.assertEqual(n, 1)
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_delete_removes_objects_and_rows(self):
        src = self.tmp / "s"
        make_dataset(src)
        sdoc_store.save_pack(self.backend, "imp_2", src)
        sdoc_store.save_dataset_row(self.backend, "imp_2", "n", "import", "t", {})
        DecisionStore(self.backend).set("imp_2", "e", {"a": 1})
        sdoc_store.delete_dataset_everywhere(self.backend, "imp_2")
        self.assertEqual(self.backend.list_objects("imp_2"), [])
        self.assertEqual(sdoc_store.list_dataset_rows(self.backend), [])
        self.assertEqual(DecisionStore(self.backend).all("imp_2"), {})


class FakeResponse:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content, self.text = status, body if body is not None else [], content, json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, replies):
        self.headers, self.calls, self.replies = {}, [], list(replies)

    def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.replies.pop(0) if self.replies else FakeResponse()

    def get(self, url, **kw): return self._do("GET", url, **kw)
    def post(self, url, **kw): return self._do("POST", url, **kw)
    def patch(self, url, **kw): return self._do("PATCH", url, **kw)
    def delete(self, url, **kw): return self._do("DELETE", url, **kw)


class TestSupabaseHttp(unittest.TestCase):
    def backend(self, *replies):
        s = FakeSession(replies)
        return SupabaseBackend("https://proj.supabase.co/", "service-key", "navis-documents", session=s), s

    def test_select_builds_postgrest_filters(self):
        b, s = self.backend(FakeResponse(200, [{"id": 1}]))
        b.select("navis_amendments", {"dataset": "ds", "id": ("gt", 3)}, order="id", desc=True, limit=1)
        method, url, kw = s.calls[0]
        self.assertEqual((method, url), ("GET", "https://proj.supabase.co/rest/v1/navis_amendments"))
        self.assertIn(("dataset", "eq.ds"), kw["params"])
        self.assertIn(("id", "gt.3"), kw["params"])
        self.assertIn(("order", "id.desc"), kw["params"])
        self.assertIn(("limit", "1"), kw["params"])
        self.assertEqual(s.headers["apikey"], "service-key")

    def test_upsert_and_conflict(self):
        b, s = self.backend(FakeResponse(201, [{"key": "k"}]), FakeResponse(409, {"code": "23505", "message": "dup"}))
        b.insert("navis_kv", {"key": "k", "value": 1}, on_conflict="key")
        _, _, kw = s.calls[0]
        self.assertIn(("on_conflict", "key"), kw["params"])
        self.assertIn("merge-duplicates", kw["headers"]["Prefer"])
        with self.assertRaises(Conflict):
            b.insert("navis_ledger", {"idx": 1})

    def test_server_errors_raise_store_error(self):
        b, _ = self.backend(FakeResponse(500, {"message": "boom"}))
        with self.assertRaises(StoreError):
            b.select("navis_kv")

    def test_object_calls(self):
        b, s = self.backend(FakeResponse(200, {}), FakeResponse(400, {"error": "not_found"}), FakeResponse(200, content=b"data"))
        b.put_object("ds/packs/pack-0001.zip", b"zip", "application/zip")
        method, url, kw = s.calls[0]
        self.assertEqual(url, "https://proj.supabase.co/storage/v1/object/navis-documents/ds/packs/pack-0001.zip")
        self.assertEqual(kw["headers"]["x-upsert"], "true")
        self.assertIsNone(b.get_object("missing"))
        self.assertEqual(b.get_object("there"), b"data")

    def test_list_recurses_into_folders(self):
        b, s = self.backend(
            FakeResponse(200, [{"name": "packs", "id": None}, {"name": "top.txt", "id": "1"}]),
            FakeResponse(200, [{"name": "pack-0001.zip", "id": "2"}]),
        )
        self.assertEqual(b.list_objects("ds"), ["ds/packs/pack-0001.zip", "ds/top.txt"])

    def test_signed_upload_url_is_absolute(self):
        b, _ = self.backend(FakeResponse(200, {"url": "/object/upload/sign/navis-documents/ds/raw/part-0001.zip?token=abc", "token": "abc"}))
        got = b.signed_upload("ds/raw/part-0001.zip")
        self.assertEqual(got["url"], "https://proj.supabase.co/storage/v1/object/upload/sign/navis-documents/ds/raw/part-0001.zip?token=abc")
        self.assertEqual(got["token"], "abc")


class TestSelection(unittest.TestCase):
    def test_disabled_without_keys(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        try:
            self.assertFalse(sdoc_store.enabled())
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class TestDirectUploadApi(StoreCase):
    """Browser-direct import: signed URLs, then the API reads the uploaded zip and stores the dataset."""

    def setUp(self):
        super().setUp()
        import api.main as m
        self.m = m
        sdoc_store.set_store(self.backend)
        self.old_dir = m.DATASETS_DIR
        m.DATASETS_DIR = self.tmp / "datasets"
        m.DATASETS_DIR.mkdir()
        self.before = set(m.DATASETS)
        self.addCleanup(self.restore)
        from fastapi.testclient import TestClient
        self.client = TestClient(m.app)

    def restore(self):
        self.m.DATASETS_DIR = self.old_dir
        for k in set(self.m.DATASETS) - self.before:
            self.m.DATASETS.pop(k, None)

    def bundle_zip(self):
        src = self.tmp / "bundle"
        make_dataset(src)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for p in sorted(src.rglob("*")):
                if p.is_file():
                    z.writestr("MyFolder/" + str(p.relative_to(src)).replace("\\", "/"), p.read_bytes())
        return buf.getvalue()

    def test_status_reports_direct_upload(self):
        self.assertTrue(self.client.get("/api/storage/status").json()["direct_upload"])

    def test_plan_hands_out_one_signed_url_per_part(self):
        r = self.client.post("/api/datasets/uploads", json={"name": "x", "parts": 2}).json()
        self.assertEqual(len(r["uploads"]), 2)
        self.assertTrue(r["uploads"][0]["url"].endswith(f"{r['dataset_id']}/raw/part-0001.zip"))

    def test_finalize_stores_the_dataset_and_a_restart_restores_it(self):
        m = self.m
        ds_id = self.client.post("/api/datasets/uploads", json={"name": "x", "parts": 1}).json()["dataset_id"]
        self.backend.put_object(f"{ds_id}/raw/part-0001.zip", self.bundle_zip())
        r = self.client.post(f"/api/datasets/{ds_id}/finalize", json={"name": "Folder A", "parts": 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["emails"], 3)
        self.assertEqual(self.backend.list_objects(f"{ds_id}/raw"), [])          # uploads are cleaned up
        self.assertTrue(self.backend.list_objects(f"{ds_id}/packs"))             # the dataset itself is kept
        self.assertEqual(self.backend.tables[sdoc_store.T_DATASETS][0]["name"], "Folder A")

        # simulate a cold start: the local copy and the in-memory registry are gone
        shutil.rmtree(m.DATASETS_DIR / ds_id)
        m.DATASETS.pop(ds_id)
        m._load_saved()
        self.assertTrue(m.DATASETS[ds_id].remote)
        self.assertEqual(self.client.get("/api/datasets").json()[-1]["emails"], 3)  # listed without downloading anything
        emails = self.client.get(f"/api/emails?ds={ds_id}").json()
        self.assertEqual(len(emails), 3)

    def test_another_server_instance_sees_datasets_imported_or_deleted_elsewhere(self):
        m = self.m
        ds_id = self.client.post("/api/datasets/uploads", json={"parts": 1}).json()["dataset_id"]
        self.backend.put_object(f"{ds_id}/raw/part-0001.zip", self.bundle_zip())
        self.client.post(f"/api/datasets/{ds_id}/finalize", json={"name": "A", "parts": 1})

        # a different instance never heard of this dataset: it is found through the table, not answered with 404
        shutil.rmtree(m.DATASETS_DIR / ds_id)
        m.DATASETS.pop(ds_id)
        self.assertEqual(self.client.get(f"/api/emails?ds={ds_id}").status_code, 200)
        self.assertEqual(len(self.client.get(f"/api/emails?ds={ds_id}").json()), 3)

        # deleted through another instance: this one stops listing it
        sdoc_store.delete_dataset_everywhere(self.backend, ds_id)
        self.assertNotIn(ds_id, [d["id"] for d in self.client.get("/api/datasets").json()])
        self.assertEqual(self.client.get(f"/api/emails?ds={ds_id}").status_code, 404)

    def test_finalize_refuses_missing_parts_and_reused_ids(self):
        ds_id = self.client.post("/api/datasets/uploads", json={"parts": 1}).json()["dataset_id"]
        self.assertEqual(self.client.post(f"/api/datasets/{ds_id}/finalize", json={"parts": 1}).status_code, 400)
        self.assertEqual(self.client.post("/api/datasets/not-an-id/finalize", json={"parts": 1}).status_code, 400)

    def test_delete_removes_the_stored_dataset(self):
        ds_id = self.client.post("/api/datasets/uploads", json={"parts": 1}).json()["dataset_id"]
        self.backend.put_object(f"{ds_id}/raw/part-0001.zip", self.bundle_zip())
        self.client.post(f"/api/datasets/{ds_id}/finalize", json={"name": "A", "parts": 1})
        self.assertEqual(self.client.delete(f"/api/datasets/{ds_id}").status_code, 200)
        self.assertEqual(self.backend.list_objects(ds_id), [])
        self.assertEqual(sdoc_store.list_dataset_rows(self.backend), [])


if __name__ == "__main__":
    unittest.main()
