import json
import shutil
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from api import importer
    from api.main import DATASETS, ROOT, app
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    TestClient = None
    importer = None
    DATASETS = ROOT = app = None

BUNDLE = (ROOT / "sdoc-hackathon-bundle") if ROOT else Path("sdoc-hackathon-bundle")


def make_eml(dst: Path, name: str, subject: str, body: str, files):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "ops@example.com", "docs@example.com", subject
    m.set_content(body)
    for fname, data in files:
        m.add_attachment(data, maintype="application", subtype="octet-stream", filename=fname)
    (dst / name).write_bytes(bytes(m))


class TestImport(unittest.TestCase):
    def setUp(self):
        if not FASTAPI_AVAILABLE:
            self.skipTest("fastapi not installed in current environment")
        self.tmp = Path(tempfile.mkdtemp())
        self.client = TestClient(app)
        self.created = []

    def tearDown(self):
        for ds in self.created:
            self.client.delete(f"/api/datasets/{ds}")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _upload(self, folder: Path, name="t"):
        files, paths = [], []
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                paths.append(f"{folder.name}/{p.relative_to(folder).as_posix()}")
                files.append(("files", (p.name, p.read_bytes())))
        r = self.client.post("/api/datasets/import", data={"name": name, "paths": json.dumps(paths)}, files=files)
        if r.status_code == 200:
            self.created.append(r.json()["id"])
        return r

    def _wait(self, ds):
        for _ in range(100):
            j = self.client.get(f"/api/datasets/{ds}").json()["job"]
            if j["status"] == "ready":
                return j
            time.sleep(0.1)
        self.fail("import did not finish")

    def test_bundle_layout(self):
        root = self.tmp / "my_bundle"
        (root / "inbox").mkdir(parents=True)
        (root / "attachments").mkdir()
        for eid in ("email_004", "email_043"):
            shutil.copy(BUNDLE / "inbox" / f"{eid}.json", root / "inbox")
            for k in ("SI", "BL"):
                shutil.copy(BUNDLE / "attachments" / f"{eid}_{k}.txt", root / "attachments")
        r = self._upload(root)
        self.assertEqual(r.status_code, 200, r.text)
        ds = r.json()["id"]
        self._wait(ds)
        rows = self.client.get("/api/emails", params={"ds": ds}).json()
        self.assertEqual(len(rows), 2)
        by = {x["id"]: x for x in rows}
        self.assertEqual(by["email_043"]["status"], "MISMATCH")
        self.assertEqual(by["email_043"]["defect_fields"], ["container_count"])
        self.assertEqual(self.client.get("/api/summary", params={"ds": ds}).json()["mismatch"], 2)

    def test_eml_folder_names_si_bl_by_content(self):
        root = self.tmp / "mailbox"
        root.mkdir()
        si = (BUNDLE / "attachments" / "email_043_SI.txt").read_bytes()
        bl = (BUNDLE / "attachments" / "email_043_BL.txt").read_bytes()
        make_eml(root, "a.eml", "TO CONFIRM DOCS _ 5XYZ", "Attached are the SI and draft BL. Please check.", [("instruction.txt", si), ("draft.txt", bl)])
        make_eml(root, "b.eml", "Exclusive offer: 90% OFF", "Your package could not be delivered due to unpaid customs fee of $2.99.", [])
        r = self._upload(root)
        self.assertEqual(r.status_code, 200, r.text)
        ds = r.json()["id"]
        self._wait(ds)
        rows = {x["subject"][:10]: x for x in self.client.get("/api/emails", params={"ds": ds}).json()}
        self.assertEqual(rows["TO CONFIRM"]["category"], "BL_COMPARISON")
        self.assertEqual(rows["TO CONFIRM"]["status"], "MISMATCH")
        self.assertEqual(rows["Exclusive "]["category"], "SPAM")

    def test_rejects_path_traversal_and_empty_folder(self):
        r = self.client.post("/api/datasets/import", data={"name": "x", "paths": json.dumps(["../evil.json"])}, files=[("files", ("evil.json", b"{}"))])
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/datasets/import", data={"name": "x", "paths": json.dumps(["f/readme.txt"])}, files=[("files", ("readme.txt", b"hi"))])
        self.assertEqual(r.status_code, 422)

    def test_capitalised_inbox_folder_and_custom_ids(self):
        root = self.tmp / "Test doc"
        (root / "Inbox").mkdir(parents=True)
        (root / "Attachments").mkdir()
        rec = json.loads((BUNDLE / "inbox" / "email_043.json").read_text(encoding="utf-8"))
        rec["email_id"] = "DOCSTRESS_00001"
        rec["attachments"] = ["attachments/DOCSTRESS_00001_SI.txt", "attachments/DOCSTRESS_00001_BL.txt"]
        (root / "Inbox" / "DOCSTRESS_00001.json").write_text(json.dumps(rec), encoding="utf-8")
        for k in ("SI", "BL"):
            shutil.copy(BUNDLE / "attachments" / f"email_043_{k}.txt", root / "Attachments" / f"DOCSTRESS_00001_{k}.txt")
        r = self._upload(root)
        self.assertEqual(r.status_code, 200, r.text)
        ds = r.json()["id"]
        self._wait(ds)
        rows = self.client.get("/api/emails", params={"ds": ds}).json()
        self.assertEqual([x["id"] for x in rows], ["DOCSTRESS_00001"])
        self.assertEqual(rows[0]["status"], "MISMATCH")

    def test_more_than_1000_files_accepted(self):
        root = self.tmp / "big"
        (root / "inbox").mkdir(parents=True)
        for i in range(1100):
            (root / "inbox" / f"email_{i:04d}.json").write_text(json.dumps({"email_id": f"email_{i:04d}", "from": "a@b.c", "subject": "Schedule update", "body": "The vessel schedule has moved.", "attachments": []}), encoding="utf-8")
        r = self._upload(root)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["emails"], 1100)

    def test_demo_dataset_protected(self):
        self.assertEqual(self.client.delete("/api/datasets/demo").status_code, 400)
        self.assertIn("demo", DATASETS)

    def test_safe_relpath(self):
        with self.assertRaises(importer.ImportError_):
            importer.safe_relpath("a/../../b")
        with self.assertRaises(importer.ImportError_):
            importer.safe_relpath("C:/x/y")


if __name__ == "__main__":
    unittest.main()
