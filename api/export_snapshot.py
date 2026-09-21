"""Exports a static snapshot of the demo pipeline output so the web UI still works if the API is offline.

Run from repo root:  python -m api.export_snapshot
"""
import json

from api.main import DATASETS, ROOT, emails, run_email, summary

d = DATASETS["demo"]
out = ROOT / "web" / "src" / "data" / "snapshot.json"
out.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "emails": emails("demo"),
    "summary": summary("demo"),
    "details": {eid: run_email(d, eid) for eid in d.loader.get_email_ids()},
}
out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
