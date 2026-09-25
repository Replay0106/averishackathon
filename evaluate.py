"""Evaluate NavisAI against labelled answer keys, and compare the rule-based classifier with an LLM.

Runs the same classify -> select documents -> extract -> reconcile steps as sdoc_pipeline.py on every email of a
labelled dataset and scores the results against the answer-key spreadsheet (columns: email_id, category,
document_format, condition, semantic_status, expected_local_status, expected_gemini_status, defect_fields).

    python evaluate.py --set "DOCSTRESS 1" "../Test doc" "C:/path/answer key.xlsx" \
                       --set "DOCSTRESS 2" "../Test-doc-2" "../Test-doc-2/answer key.xlsx"
    python evaluate.py ... --llm --llm-sample 200       # also classify a stratified sample with Gemini
    python evaluate.py ... --no-vision                   # score the offline path (no Gemini at all)

Writes EVALUATION.md and eval_results.json. Nothing is sent to Supabase (NAVIS_STORAGE=local), and Gemini is only
called for scanned PDFs (unless --no-vision) and, with --llm, for the classifier comparison. LLM answers are cached in
.cache/eval_llm_cache.json so a rerun costs nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

os.environ.setdefault("NAVIS_STORAGE", "local")  # never touch the shared Supabase project from an evaluation run
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
CATEGORIES = ["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"]
STATUSES = ["OK", "MISMATCH", "NEEDS_REVIEW"]
FIELDS = ["shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge", "container_count", "gross_weight_kg"]


# ---------------------------------------------------------------- metrics (pure, unit-tested)
def confusion(pairs: Iterable[Tuple[str, str]], labels: Sequence[str]) -> Dict[str, Dict[str, int]]:
    """matrix[expected][predicted]; predictions outside `labels` are counted under "OTHER"."""
    m = {e: {p: 0 for p in [*labels, "OTHER"]} for e in labels}
    for exp, pred in pairs:
        if exp in m:
            m[exp][pred if pred in labels else "OTHER"] += 1
    return m


def per_class(matrix: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, float]]:
    labels = list(matrix)
    out = {}
    for c in labels:
        tp = matrix[c][c]
        fp = sum(matrix[e][c] for e in labels if e != c)
        fn = sum(v for p, v in matrix[c].items() if p != c)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[c] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}
    return out


def accuracy(pairs: Sequence[Tuple[str, str]]) -> float:
    return sum(e == p for e, p in pairs) / len(pairs) if pairs else 0.0


def macro_f1(stats: Dict[str, Dict[str, float]]) -> float:
    present = [s["f1"] for s in stats.values() if s["support"]]
    return sum(present) / len(present) if present else 0.0


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def status_outcomes(rows: List[Dict[str, Any]], expected_key: str) -> Dict[str, Any]:
    """Operational error counts for SI/BL checks. A false clear is the costly one: a real problem marked OK."""
    checks = [r for r in rows if r["key_category"] == "BL_COMPARISON"]
    exp = lambda r: r[expected_key]  # noqa: E731
    return {
        "checks": len(checks),
        "correct": sum(exp(r) == r["status"] for r in checks),
        "false_clears": [r["id"] for r in checks if exp(r) in ("MISMATCH", "NEEDS_REVIEW") and r["status"] == "OK"],
        "missed_mismatches": [r["id"] for r in checks if exp(r) == "MISMATCH" and r["status"] != "MISMATCH"],
        "false_alarms": [r["id"] for r in checks if exp(r) == "OK" and r["status"] == "MISMATCH"],
        "unneeded_reviews": [r["id"] for r in checks if exp(r) != "NEEDS_REVIEW" and r["status"] == "NEEDS_REVIEW"],
        "missed_reviews": [r["id"] for r in checks if exp(r) == "NEEDS_REVIEW" and r["status"] != "NEEDS_REVIEW"],
        "review_rate": sum(r["status"] == "NEEDS_REVIEW" for r in checks) / len(checks) if checks else 0.0,
    }


def field_scores(rows: List[Dict[str, Any]], expected_key: str) -> Dict[str, Any]:
    """Which fields were reported as differing, on checks the key marks MISMATCH and the system also flags."""
    both = [r for r in rows if r["key_category"] == "BL_COMPARISON" and r[expected_key] == "MISMATCH" and r["status"] == "MISMATCH"]
    per = {}
    for f in FIELDS:
        tp = sum(f in r["key_fields"] and f in r["defect_fields"] for r in both)
        fp = sum(f not in r["key_fields"] and f in r["defect_fields"] for r in both)
        fn = sum(f in r["key_fields"] and f not in r["defect_fields"] for r in both)
        per[f] = {"tp": tp, "fp": fp, "fn": fn,
                  "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None}
    exact = sum(set(r["key_fields"]) == set(r["defect_fields"]) for r in both)
    return {"compared": len(both), "exact_set_match": exact, "per_field": per}


# ---------------------------------------------------------------- data
def load_key(path: Path) -> Dict[str, Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    head = [str(h).strip() for h in rows[0]]
    key = {}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        d = dict(zip(head, row))
        raw = d.get("defect_fields") or "[]"
        try:
            d["defect_fields"] = json.loads(raw) if isinstance(raw, str) else list(raw)
        except json.JSONDecodeError:
            d["defect_fields"] = [x.strip() for x in str(raw).strip("[]").replace('"', "").split(",") if x.strip()]
        key[str(d["email_id"])] = d
    return key


def run_system(dataset: Path, key: Dict[str, Dict[str, Any]], vision: bool, progress: bool = True) -> List[Dict[str, Any]]:
    from sdoc_classifier import EmailClassifier, is_draft_request
    from sdoc_extractor import FieldExtractor
    from sdoc_loader import InboxLoader
    from sdoc_reconciler import DocumentReconciler, select_documents

    loader = InboxLoader(str(dataset))
    classifier = EmailClassifier(use_llm=False)  # the rules alone; the LLM is measured separately
    if vision:
        extractor = FieldExtractor()
    else:  # no key and an empty cache: scanned documents cannot be read, exactly like an offline deployment
        saved = {k: os.environ.pop(k) for k in ("GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY") if k in os.environ}
        extractor = FieldExtractor(cache_path=str(Path(tempfile.mkdtemp()) / "empty_vision_cache.json"))
        os.environ.update(saved)
    reconciler = DocumentReconciler()
    rows = []
    ids = loader.get_email_ids()
    for i, eid in enumerate(ids, 1):
        if progress and (i % 200 == 0 or i == len(ids)):
            print(f"    {i}/{len(ids)}", flush=True)
        t0 = time.perf_counter()
        email = loader.get_email(eid)
        category = classifier.classify_email(email)
        status, reason, fields, scanned, docs = "NOT_APPLICABLE", None, [], 0, 0
        if category == "BL_COMPARISON":
            si, bl, ambiguous = select_documents([loader.load_attachment(a) for a in email.get("attachments", [])])
            si_f = extractor.extract(si) if si else None
            bl_f = extractor.extract(bl) if bl else None
            scanned = sum(1 for f in (si_f, bl_f) if f is not None and f.is_scanned)
            docs = sum(1 for f in (si_f, bl_f) if f is not None)
            entry = reconciler.reconcile(email_id=eid, category=category, si_att=si, bl_att=bl, si_fields=si_f, bl_fields=bl_f,
                                         draft_request=is_draft_request(email), ambiguous_documents=ambiguous)
            status, reason, fields = entry["status"], entry["review_reason"], list(entry["defect_fields"])
        ms = (time.perf_counter() - t0) * 1000
        k = key.get(eid)
        if k is None:
            continue
        rows.append({
            "id": eid, "category": category, "status": status, "review_reason": reason, "defect_fields": fields, "ms": ms,
            "scanned_docs": scanned, "docs": docs, "email": email,
            "key_category": k["category"], "condition": k.get("condition"), "format": k.get("document_format"),
            "semantic_status": k.get("semantic_status"), "expected_local_status": k.get("expected_local_status"),
            "expected_gemini_status": k.get("expected_gemini_status"), "key_fields": k["defect_fields"],
        })
    return rows


# ---------------------------------------------------------------- LLM classifier (for the comparison only)
LLM_PROMPT_VERSION = "v1"
# The category prompt and email formatting are shared with the app's optional fallback in sdoc_classifier.py.
from sdoc_classifier import LLM_CATEGORY_PROMPT as LLM_INSTRUCTIONS  # noqa: E402
from sdoc_classifier import llm_email_text as _llm_text  # noqa: E402


class LLMClassifier:
    def __init__(self, model: str, batch: int = 20):
        from google import genai

        key = (os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").split(",")[0].strip()
        if not key:
            raise SystemExit("--llm needs GEMINI_API_KEY")
        self.client = genai.Client(api_key=key)
        self.model, self.batch = model, batch
        self.cache_path = ROOT / ".cache" / "eval_llm_cache.json"
        self.cache: Dict[str, str] = json.loads(self.cache_path.read_text(encoding="utf-8")) if self.cache_path.exists() else {}
        self.calls: List[Dict[str, Any]] = []

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}|{LLM_PROMPT_VERSION}|{text}".encode()).hexdigest()

    def classify(self, emails: List[Tuple[str, Dict[str, Any]]]) -> Dict[str, str]:
        """Labels by email id. Each cached answer keeps its share of the call that produced it (tokens, call time), so
        a rerun served from the cache still reports what the answers cost; see self.costs."""
        out, todo = {}, []
        self.costs: Dict[str, Dict[str, Any]] = {}
        for eid, e in emails:
            text = _llm_text({**e, "email_id": eid})
            hit = self.cache.get(self._key(text))
            if hit:
                out[eid] = hit["label"] if isinstance(hit, dict) else hit
                if isinstance(hit, dict):
                    self.costs[eid] = hit
            else:
                todo.append((eid, text))
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            got = self._call(chunk)
            share = {eid: self.calls[-1] for eid, _ in chunk if eid in got}
            missing = [c for c in chunk if c[0] not in got]
            for c in missing:  # one retry, one email at a time
                retry = self._call([c])
                got.update(retry)
                if c[0] in retry:
                    share[c[0]] = self.calls[-1]
            for eid, text in chunk:
                label = got.get(eid, "ERROR")
                out[eid] = label
                if label in CATEGORIES:
                    call = share[eid]
                    n = call["emails"]
                    entry = {"label": label, "prompt_tokens": call.get("prompt_tokens", 0) / n,
                             "output_tokens": call.get("output_tokens", 0) / n, "call_ms": call["latency_ms"], "batch": n}
                    self.cache[self._key(text)] = entry
                    self.costs[eid] = entry
            self.cache_path.parent.mkdir(exist_ok=True)
            self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
            print(f"    LLM {min(i + self.batch, len(todo))}/{len(todo)}", flush=True)
        return out

    def _call(self, chunk: List[Tuple[str, str]]) -> Dict[str, str]:
        prompt = LLM_INSTRUCTIONS + "\n\n" + "\n\n---\n\n".join(t for _, t in chunk)
        t0 = time.perf_counter()
        rec: Dict[str, Any] = {"emails": len(chunk), "model": self.model}
        try:
            r = self.client.models.generate_content(model=self.model, contents=prompt, config={"response_mime_type": "application/json", "temperature": 0})
            data = json.loads(r.text)
            u = r.usage_metadata
            rec.update(ok=True, prompt_tokens=u.prompt_token_count or 0, output_tokens=(u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0))
            return {str(k): str(v).strip().upper() for k, v in data.items() if str(v).strip().upper() in CATEGORIES}
        except Exception as e:  # noqa: BLE001 — recorded, and the email is scored as wrong
            rec.update(ok=False, error=f"{type(e).__name__}: {str(e)[:160]}")
            return {}
        finally:
            rec["latency_ms"] = (time.perf_counter() - t0) * 1000
            self.calls.append(rec)


def stratified_sample(rows: List[Dict[str, Any]], n: int, seed: int = 7) -> List[Dict[str, Any]]:
    if n <= 0 or n >= len(rows):
        return list(rows)
    by = defaultdict(list)
    for r in rows:
        by[r["key_category"]].append(r)
    rnd = random.Random(seed)
    out = []
    for cat, items in sorted(by.items()):
        k = max(1, round(n * len(items) / len(rows)))
        out += rnd.sample(items, min(k, len(items)))
    return sorted(out, key=lambda r: r["id"])


# ---------------------------------------------------------------- vision log
def vision_log_summary() -> Optional[Dict[str, Any]]:
    p = ROOT / ".cache" / "vision_calls.jsonl"
    if not p.exists():
        return None
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    ok = [r for r in rows if r.get("outcome") == "ok"]
    return {
        "calls": len(rows), "ok": len(ok), "failed": dict(Counter(r.get("outcome") for r in rows if r.get("outcome") != "ok")),
        "latency_ms_p50": percentile([r["latency_ms"] for r in ok], 0.5), "latency_ms_p95": percentile([r["latency_ms"] for r in ok], 0.95),
        "tokens_mean": statistics.mean([(r.get("total_tokens") or 0) for r in ok]) if ok else 0,
        "models": sorted({r.get("model") for r in ok if r.get("model")}),
    }


# ---------------------------------------------------------------- report
def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def md_matrix(m: Dict[str, Dict[str, int]]) -> str:
    cols = [c for c in next(iter(m.values())) if c != "OTHER" or any(r["OTHER"] for r in m.values())]
    lines = ["| expected \\ predicted | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    for e, row in m.items():
        lines.append(f"| **{e}** | " + " | ".join(f"**{row[c]}**" if c == e else str(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def md_class_table(stats: Dict[str, Dict[str, float]]) -> str:
    lines = ["| category | precision | recall | F1 | emails |", "|---|---:|---:|---:|---:|"]
    for c, s in stats.items():
        lines.append(f"| {c} | {pct(s['precision'])} | {pct(s['recall'])} | {pct(s['f1'])} | {s['support']} |")
    return "\n".join(lines)


def ids_note(ids: List[str]) -> str:
    return "none" if not ids else f"{len(ids)} ({', '.join(ids[:6])}{', ...' if len(ids) > 6 else ''})"


def shown_path(p: Path) -> str:
    """A path for the report: relative to the project's parent folder when it is inside it, else just the file or
    folder name, so the committed report does not contain a user's home directory."""
    try:
        return Path(p).resolve().relative_to(ROOT.parent).as_posix()
    except ValueError:
        return Path(p).name


def evaluate_set(name: str, dataset: Path, key_path: Path, args) -> Dict[str, Any]:
    print(f"\n== {name}: {dataset}")
    key = load_key(key_path)
    t0 = time.perf_counter()
    rows = run_system(dataset, key, vision=not args.no_vision)
    wall = time.perf_counter() - t0
    expected_key = "expected_local_status" if args.no_vision else "expected_gemini_status"

    cls_pairs = [(r["key_category"], r["category"]) for r in rows]
    cls_m = confusion(cls_pairs, CATEGORIES)
    cls_stats = per_class(cls_m)
    checks = [r for r in rows if r["key_category"] == "BL_COMPARISON"]
    st_pairs = [(r[expected_key], r["status"]) for r in checks]
    by_condition = defaultdict(lambda: [0, 0])
    for r in checks:
        by_condition[r["condition"]][0] += r[expected_key] == r["status"]
        by_condition[r["condition"]][1] += 1
    ms = [r["ms"] for r in rows]
    res: Dict[str, Any] = {
        "name": name, "dataset": shown_path(dataset), "key": shown_path(key_path), "emails": len(rows), "unlabelled_in_key": len(key) - len(rows),
        "mode": "offline (no Gemini)" if args.no_vision else "with Gemini vision for scans (cached reads count)",
        "expected_column": expected_key,
        "classification": {"accuracy": accuracy(cls_pairs), "macro_f1": macro_f1(cls_stats), "per_class": cls_stats, "confusion": cls_m,
                           "errors": [{"id": r["id"], "expected": r["key_category"], "got": r["category"]} for r in rows if r["key_category"] != r["category"]]},
        "status": {"accuracy": accuracy(st_pairs), "confusion": confusion(st_pairs, STATUSES), **status_outcomes(rows, expected_key),
                   "other_column": {"column": "expected_gemini_status" if args.no_vision else "expected_local_status",
                                    "accuracy": accuracy([(r["expected_gemini_status" if args.no_vision else "expected_local_status"], r["status"]) for r in checks])},
                   "by_condition": {c: {"correct": v[0], "total": v[1]} for c, v in sorted(by_condition.items())}},
        "fields": field_scores(rows, expected_key),
        "timing": {"wall_s": wall, "per_email_ms_p50": percentile(ms, 0.5), "per_email_ms_p95": percentile(ms, 0.95), "per_email_ms_max": max(ms) if ms else 0},
        "scanned_documents": sum(r["scanned_docs"] for r in rows),
        "documents_read": sum(r["docs"] for r in rows),
    }
    if args.vision_ablation and not args.no_vision:
        off = run_system(dataset, key, vision=False, progress=False)
        on_by = {r["id"]: r for r in rows}
        decided = [r for r in off if r["key_category"] == "BL_COMPARISON" and r["status"] == "NEEDS_REVIEW"
                   and on_by[r["id"]]["status"] != "NEEDS_REVIEW"]
        res["vision_ablation"] = {
            "offline": {"review_rate": status_outcomes(off, "expected_local_status")["review_rate"],
                        "correct_vs_local_key": accuracy([(r["expected_local_status"], r["status"]) for r in off if r["key_category"] == "BL_COMPARISON"]),
                        "false_clears": len(status_outcomes(off, "expected_local_status")["false_clears"])},
            "decided_by_vision": len(decided),
            "decided_correctly": sum(on_by[r["id"]]["status"] == r["expected_gemini_status"] for r in decided),
            "by_condition": dict(Counter(r["condition"] for r in decided)),
            "outcomes": dict(Counter(on_by[r["id"]]["status"] for r in decided)),
        }
    if args.llm:
        sample = stratified_sample(rows, args.llm_sample)
        llm = LLMClassifier(args.llm_model, batch=args.llm_batch)
        labels = llm.classify([(r["id"], r["email"]) for r in sample])
        rules_pairs = [(r["key_category"], r["category"]) for r in sample]
        llm_pairs = [(r["key_category"], labels.get(r["id"], "ERROR")) for r in sample]
        hyb_pairs = [(r["key_category"], labels.get(r["id"], "ERROR") if r["category"] == "GENERAL" else r["category"]) for r in sample]
        ok_calls = [c for c in llm.calls if c.get("ok")]
        res["llm"] = {
            "model": args.llm_model, "sample": len(sample), "prompt_version": LLM_PROMPT_VERSION,
            "rules": {"accuracy": accuracy(rules_pairs), "macro_f1": macro_f1(per_class(confusion(rules_pairs, CATEGORIES)))},
            "llm_only": {"accuracy": accuracy(llm_pairs), "macro_f1": macro_f1(per_class(confusion(llm_pairs, CATEGORIES))),
                         "confusion": confusion(llm_pairs, CATEGORIES)},
            "hybrid": {"accuracy": accuracy(hyb_pairs), "macro_f1": macro_f1(per_class(confusion(hyb_pairs, CATEGORIES))),
                       "llm_used_for": sum(r["category"] == "GENERAL" for r in sample)},
            # Cost of every answer in the sample, whether it was fetched now or served from the cache.
            "calls_this_run": len(llm.calls), "failed_calls": len(llm.calls) - len(ok_calls),
            "answers_from_cache": sum(1 for r in sample if r["id"] in llm.costs) - sum(c["emails"] for c in ok_calls),
            "answers_with_cost": len(llm.costs),
            "batch_size": args.llm_batch,
            "prompt_tokens": round(sum(c["prompt_tokens"] for c in llm.costs.values())),
            "output_tokens": round(sum(c["output_tokens"] for c in llm.costs.values())),
            "latency_ms_per_call_p50": percentile([c["call_ms"] for c in llm.costs.values()], 0.5),
            "errors": [c["error"] for c in llm.calls if not c.get("ok")][:5],
        }
    for r in rows:
        r.pop("email", None)
    return res


def render(results: List[Dict[str, Any]], vision: Optional[Dict[str, Any]], args) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# NavisAI evaluation",
        "",
        f"Generated by `python evaluate.py` on {now}. Every number below is computed by that script from the answer-key "
        "spreadsheets and the pipeline code in this repository; rerun it to reproduce them.",
        "",
        "**How to read this.** Both DOCSTRESS sets were produced by one synthetic generator, and the rules were tuned while "
        "looking at set 1, so these are regression results on data the team has seen, not a blind estimate of accuracy on real "
        "forwarder email. The honest claims are the error counts (especially false clears) and the error analysis by condition.",
        "",
        "## Summary",
        "",
        "| | " + " | ".join(r["name"] for r in results) + " |",
        "|---|" + "---:|" * len(results),
        "| Emails | " + " | ".join(str(r["emails"]) for r in results) + " |",
        "| Classification accuracy | " + " | ".join(pct(r["classification"]["accuracy"]) for r in results) + " |",
        "| Classification macro-F1 | " + " | ".join(pct(r["classification"]["macro_f1"]) for r in results) + " |",
        "| SI/BL checks | " + " | ".join(str(r["status"]["checks"]) for r in results) + " |",
        "| Check outcome matches the key | " + " | ".join(f"{r['status']['correct']} / {r['status']['checks']} ({pct(r['status']['accuracy'])})" for r in results) + " |",
        "| **False clears** (real problem marked OK) | " + " | ".join(f"**{len(r['status']['false_clears'])}**" for r in results) + " |",
        "| Missed mismatches | " + " | ".join(str(len(r["status"]["missed_mismatches"])) for r in results) + " |",
        "| False alarms (OK flagged as mismatch) | " + " | ".join(str(len(r["status"]["false_alarms"])) for r in results) + " |",
        "| Unneeded reviews | " + " | ".join(str(len(r["status"]["unneeded_reviews"])) for r in results) + " |",
        "| Review rate (checks sent to a person) | " + " | ".join(pct(r["status"]["review_rate"]) for r in results) + " |",
        "| Differing fields named exactly right | " + " | ".join(f"{r['fields']['exact_set_match']} / {r['fields']['compared']}" for r in results) + " |",
        "| Processing time per email, p50 / p95 | " + " | ".join(f"{r['timing']['per_email_ms_p50']:.1f} / {r['timing']['per_email_ms_p95']:.1f} ms" for r in results) + " |",
        "| Whole set, one process | " + " | ".join(f"{r['timing']['wall_s']:.1f} s" for r in results) + " |",
        "| Documents without a text layer (need vision) | " + " | ".join(f"{r['scanned_documents']} of {r['documents_read']}" for r in results) + " |",
        "",
        f"Mode: {results[0]['mode']}; check outcomes are scored against the key's `{results[0]['expected_column']}` column. "
        "Timings are from this single local run and include reading the files; the first run after a restart reads them from "
        "disk and is several times slower than a warm rerun, so quote them as indicative only.",
    ]
    if any("vision_ablation" in r for r in results):
        out += ["", "## What Gemini vision adds", "",
                "Each set was run twice: with Gemini reading scanned documents, and offline (no key, empty cache), where every "
                "scanned document has to go to a person. The offline run is scored against the key's `expected_local_status` column.", "",
                "| Set | Review rate offline | Review rate with vision | Checks decided only because of vision | Of those, correct | False clears offline / with vision |",
                "|---|---:|---:|---:|---:|---:|"]
        for r in results:
            if "vision_ablation" in r:
                a = r["vision_ablation"]
                out.append(f"| {r['name']} | {pct(a['offline']['review_rate'])} | {pct(r['status']['review_rate'])} | {a['decided_by_vision']} "
                           f"({', '.join(f'{k} {v}' for k, v in sorted(a['by_condition'].items()))}) | {a['decided_correctly']} / {a['decided_by_vision']} | "
                           f"{a['offline']['false_clears']} / {len(r['status']['false_clears'])} |")
    if any("llm" in r for r in results):
        out += ["", "## Rules vs LLM vs hybrid classifier", "",
                "The same emails of each set (all of them, or a stratified sample with `--llm-sample`) were classified three ways: the rule-based classifier used in production, "
                "an LLM given only one-line category definitions (zero-shot), and a hybrid that keeps the rules and asks the LLM only "
                "when no rule matched (the rules fall through to GENERAL).", "",
                "Tokens and call time cover every answer in the sample, including answers served from the evaluation cache "
                "(each cached answer keeps its share of the call that produced it). Emails were sent in batches.", "",
                "| Set | Model | Emails | Rules | LLM only | Hybrid | Tokens (in / out) | Tokens per email | Batch call time p50 |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for r in results:
            if "llm" not in r:
                continue
            l = r["llm"]
            out.append(f"| {r['name']} | `{l['model']}` | {l['sample']} | {pct(l['rules']['accuracy'])} | {pct(l['llm_only']['accuracy'])} | "
                       f"{pct(l['hybrid']['accuracy'])} (LLM on {l['hybrid']['llm_used_for']}) | "
                       f"{l['prompt_tokens']:,} / {l['output_tokens']:,} | {(l['prompt_tokens'] + l['output_tokens']) / max(l['sample'], 1):.0f} | "
                       f"{l['latency_ms_per_call_p50'] / 1000:.1f} s for {l['batch_size']} emails |")
        if args.price_in is not None and args.price_out is not None:
            out += ["", f"Cost at the prices given on the command line (${args.price_in}/M input, ${args.price_out}/M output tokens):"]
            for r in results:
                if "llm" in r:
                    l = r["llm"]
                    usd = l["prompt_tokens"] / 1e6 * args.price_in + l["output_tokens"] / 1e6 * args.price_out
                    out.append(f"- {r['name']}: ${usd:.4f} for {l['sample']} emails (${usd / max(l['sample'], 1) * 1000:.3f} per 1,000 emails); the rules cost nothing per email.")
        errs = [e for r in results if "llm" in r for e in r["llm"]["errors"]]
        if errs:
            out += ["", "LLM call errors (first few): " + "; ".join(f"`{e}`" for e in errs[:3])]
    for r in results:
        s = r["status"]
        out += ["", f"## {r['name']}", "", f"Dataset `{r['dataset']}`, key `{r['key']}`.", "",
                "### Classification", "", md_class_table(r["classification"]["per_class"]), "", md_matrix(r["classification"]["confusion"]), "",
                "Misclassified: " + (", ".join(f"{e['id']} ({e['expected']} → {e['got']})" for e in r["classification"]["errors"][:10]) or "none") +
                (" ..." if len(r["classification"]["errors"]) > 10 else ""),
                "", "### SI/BL check outcome", "", md_matrix(s["confusion"]), "",
                f"- False clears: {ids_note(s['false_clears'])}", f"- Missed mismatches: {ids_note(s['missed_mismatches'])}",
                f"- False alarms: {ids_note(s['false_alarms'])}", f"- Unneeded reviews: {ids_note(s['unneeded_reviews'])}",
                f"- Missed reviews: {ids_note(s['missed_reviews'])}",
                f"- Against the other key column (`{s['other_column']['column']}`): {pct(s['other_column']['accuracy'])}",
                "", "By stress condition:", "", "| condition | correct | checks |", "|---|---:|---:|"]
        out += [f"| {c} | {v['correct']} | {v['total']} |" for c, v in s["by_condition"].items()]
        f = r["fields"]
        out += ["", f"### Differing fields ({f['compared']} mismatches flagged by both the key and the system)", "",
                "| field | correct | extra | missed |", "|---|---:|---:|---:|"]
        out += [f"| {k} | {v['tp']} | {v['fp']} | {v['fn']} |" for k, v in f["per_field"].items() if v["tp"] or v["fp"] or v["fn"]]
    if vision:
        out += ["", "## Gemini vision call log", "",
                f"From `.cache/vision_calls.jsonl` (every live call made on this machine, not only this run): {vision['calls']} calls, "
                f"{vision['ok']} succeeded, failures {vision['failed'] or 'none'}. Successful calls: p50 {vision['latency_ms_p50']:.0f} ms, "
                f"p95 {vision['latency_ms_p95']:.0f} ms, {vision['tokens_mean']:.0f} tokens on average, models {', '.join(vision['models']) or 'n/a'}. "
                "Cached reads are not calls and cost nothing."]
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", nargs=3, action="append", metavar=("NAME", "DATASET_DIR", "ANSWER_KEY"), required=True)
    ap.add_argument("--no-vision", action="store_true", help="score the offline path: no Gemini key, empty vision cache")
    ap.add_argument("--vision-ablation", action="store_true", help="also run each set offline and report what vision changes")
    ap.add_argument("--llm", action="store_true", help="also classify a stratified sample with an LLM (needs GEMINI_API_KEY)")
    ap.add_argument("--llm-model", default="gemini-3.5-flash-lite")
    ap.add_argument("--llm-sample", type=int, default=200, help="emails per set for the LLM comparison (0 = all)")
    ap.add_argument("--llm-batch", type=int, default=20)
    ap.add_argument("--price-in", type=float, help="USD per million input tokens, to report LLM cost")
    ap.add_argument("--price-out", type=float, help="USD per million output tokens")
    ap.add_argument("--out-md", default=str(ROOT / "EVALUATION.md"))
    ap.add_argument("--out-json", default=str(ROOT / "eval_results.json"))
    args = ap.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    results = [evaluate_set(name, Path(d), Path(k), args) for name, d, k in args.set]
    vision = vision_log_summary()
    Path(args.out_md).write_text(render(results, vision, args), encoding="utf-8")
    Path(args.out_json).write_text(json.dumps({"results": results, "vision_log": vision}, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {args.out_md} and {args.out_json}")
    for r in results:
        s = r["status"]
        print(f"  {r['name']}: classification {pct(r['classification']['accuracy'])}, checks {s['correct']}/{s['checks']}, "
              f"false clears {len(s['false_clears'])}, review rate {pct(s['review_rate'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
