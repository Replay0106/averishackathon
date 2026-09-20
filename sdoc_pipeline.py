"""
sdoc_pipeline.py — Standalone CLI Autonomous Pipeline Runner for NavisAI SDOC Hackathon.

Executes the end-to-end workflow:
  1. Ingests all emails from the SDOC hackathon bundle.
  2. Classifies emails into 5 categories (BL_COMPARISON, SI_REQUEST, INVOICE_QUERY, GENERAL, SPAM).
  3. For BL_COMPARISON: extracts 7 fields from SI & BL and checks reliability guardrails.
  4. Formats complete submission JSON matching sample_submission.json exactly.
  5. Optionally submits to self-evaluation server if running.

Usage:
  python sdoc_pipeline.py --bundle "sdoc-hackathon-bundle" --output "submission.json"
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from sdoc_classifier import EmailClassifier
from sdoc_extractor import FieldExtractor
from sdoc_loader import InboxLoader
from sdoc_reconciler import DocumentReconciler


def run_pipeline(bundle_dir: str, output_path: str = "submission.json", server_url: str = None) -> Dict[str, Any]:
    print("=" * 70)
    print("🚀 NavisAI | Autonomous Shipping Document Verification Pipeline")
    print(f"📁 Ingesting Bundle: {bundle_dir}")
    print("=" * 70)

    loader = InboxLoader(bundle_dir)
    classifier = EmailClassifier()
    extractor = FieldExtractor()
    reconciler = DocumentReconciler()

    email_ids = loader.get_email_ids()
    total_emails = len(email_ids)
    print(f"📦 Found {total_emails} emails in inbox.")

    submission = {}
    stats_categories = Counter()
    stats_statuses = Counter()
    stats_review_reasons = Counter()
    stats_defects = Counter()

    start_time = time.time()

    for idx, eid in enumerate(email_ids, 1):
        if idx % 50 == 0 or idx == total_emails:
            print(f"  [{idx}/{total_emails}] Processing {eid}...")

        email = loader.get_email(eid)
        category = classifier.classify_email(email)
        stats_categories[category] += 1

        if category != "BL_COMPARISON":
            entry = reconciler.reconcile(email_id=eid, category=category)
            submission[eid] = entry
            stats_statuses[entry["status"]] += 1
            continue

        # BL_COMPARISON handling
        atts = email.get("attachments", [])
        si_att, bl_att = None, None

        for a in atts:
            att_data = loader.load_attachment(a)
            fn_upper = att_data.filename.upper()
            if "_SI" in fn_upper or "SI_" in fn_upper or att_data.detected_doc_type == "SI":
                if si_att is None:
                    si_att = att_data
            elif "_BL" in fn_upper or "BL_" in fn_upper or att_data.detected_doc_type in "BL":
                if bl_att is None:
                    bl_att = att_data

        # If only 2 attachments but not separated by name, assign first as SI and second as BL
        if len(atts) == 2 and (si_att is None or bl_att is None):
            loaded = [loader.load_attachment(a) for a in atts]
            si_cand = next((d for d in loaded if d.detected_doc_type == "SI"), loaded[0])
            bl_cand = next((d for d in loaded if d.detected_doc_type == "BL"), loaded[1] if loaded[0] == si_cand else loaded[0])
            si_att = si_att or si_cand
            bl_att = bl_att or bl_cand

        si_fields = extractor.extract(si_att) if si_att else None
        bl_fields = extractor.extract(bl_att) if bl_att else None

        entry = reconciler.reconcile(
            email_id=eid,
            category=category,
            si_att=si_att,
            bl_att=bl_att,
            si_fields=si_fields,
            bl_fields=bl_fields
        )

        submission[eid] = entry
        stats_statuses[entry["status"]] += 1
        if entry["review_reason"]:
            stats_review_reasons[entry["review_reason"]] += 1
        if entry["has_defect"]:
            for df in entry["defect_fields"]:
                stats_defects[df] += 1

    elapsed = time.time() - start_time

    # Write submission JSON
    out_file = Path(output_path)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(submission, f, indent=2)

    print("\n" + "=" * 70)
    print(f"✅ Pipeline Completed in {elapsed:.2f} seconds ({elapsed/total_emails:.3f}s / email)")
    print(f"📄 Output saved to: {out_file.resolve()}")
    print("=" * 70)

    # Validate against sample_submission.json
    sample_sub_path = Path(bundle_dir) / "sample_submission.json"
    if sample_sub_path.exists():
        with open(sample_sub_path, "r", encoding="utf-8") as f:
            sample_sub = json.load(f)
        missing_keys = set(sample_sub.keys()) - set(submission.keys())
        extra_keys = set(submission.keys()) - set(sample_sub.keys())
        print(f"🔍 Parity Validation: {len(submission)}/{len(sample_sub)} keys matched.")
        if missing_keys:
            print(f"⚠️ Missing keys ({len(missing_keys)}): {list(missing_keys)[:5]}")
        elif extra_keys:
            print(f"⚠️ Extra keys ({len(extra_keys)}): {list(extra_keys)[:5]}")
        else:
            print("✨ 100% Exact Key Match with sample_submission.json!")

    print("\n📊 Pipeline Execution Summary:")
    print("  [Categories]")
    for cat, cnt in stats_categories.most_common():
        print(f"    - {cat:16}: {cnt}")

    print("  [Verification Outcomes]")
    for st, cnt in stats_statuses.most_common():
        print(f"    - {st:16}: {cnt}")

    if stats_review_reasons:
        print("  [Human Review (NEEDS_REVIEW) Breakdown]")
        for rr, cnt in stats_review_reasons.most_common():
            print(f"    - {rr:20}: {cnt}")

    if stats_defects:
        print("  [Top Defect Fields Identified]")
        for df, cnt in stats_defects.most_common():
            print(f"    - {df:20}: {cnt}")

    # Optional submit to server
    if server_url:
        print(f"\n🌐 Submitting to self-evaluation server at {server_url}...")
        try:
            from loader import Inbox
            inbox = Inbox(server_url)
            score_result = inbox.submit(submission)
            print("🏆 Scoreboard Result:")
            print(json.dumps(score_result, indent=2))
        except Exception as e:
            print(f"⚠️ Could not submit to server: {e}")

    return submission


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NavisAI SDOC Pipeline Runner")
    local_bundle = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sdoc-hackathon-bundle")
    default_bundle = local_bundle if os.path.isdir(local_bundle) else "C:/Users/Jer Khai/Downloads/sdoc-hackathon-bundle"
    parser.add_argument("--bundle", default=default_bundle, help="Path to sdoc bundle")
    parser.add_argument("--output", default="submission.json", help="Path to output submission JSON")
    parser.add_argument("--server", default=None, help="Optional HTTP server URL for evaluation (e.g. http://localhost:8080)")
    args = parser.parse_args()

    run_pipeline(bundle_dir=args.bundle, output_path=args.output, server_url=args.server)
