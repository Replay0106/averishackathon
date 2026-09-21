# NavisAI — Competitive Advantage Audit

All numbers were measured from the codebase and the two DOCSTRESS answer keys unless stated otherwise. Nothing here describes any other team's work. Benchmark scripts were run from outside the repo.

**Correction to earlier descriptions:** NavisAI does not use batched Gemini inference. The classifier is pure regex (`sdoc_classifier.py`); the docstring at line 11 mentioning "batched Gemini Flash LLM refinement" describes something that is not in the code. Extraction is regex too. Gemini runs only on scanned PDFs, one call per document, cached by file hash. Do not claim batching.

## 1. Potential advantages

| Area | What NavisAI does | Conventional approach | Potential advantage | How to prove it |
|---|---|---|---|---|
| Model calls | Regex handles classification and text extraction; Gemini vision only for scanned files | One LLM call per email or document | 0 live model calls in the measured run on the 520-email bundle. 6 of 440 SI/BL documents (1.4%) take the vision path, all 6 cache hits | Count calls with a counter; rerun with an empty cache for the true cold count |
| Speed | Deterministic stages in-process | Network round-trip per email | 520 emails in 0.35 s warm (1,472 emails/s), 4.33 s cold; warm p50 0.25 ms, p95 2.7 ms | Publish the benchmark script and rerun it live |
| Do not guess | Missing, conflicting, duplicate, wrong or unreadable input goes to NEEDS_REVIEW with a named reason | Model returns its best answer | 0 real mismatches passed as OK and 0 false mismatches on both stress sets | Show the confusion tables and `tests/test_strictness.py` |
| Traceability | Each text-extracted field stores source line, line number and confidence | LLM returns values only | 100% of fields from text documents carry a source line | Show the redline for any mismatch |
| Determinism | Rule-based comparison (entity suffixes, MT/LBS to KG, port aliases, 0.5 kg tolerance) | LLM judges "same or different" | Same input gives same output; `submission.json` was byte-identical across reruns | Hash two runs |
| Modularity | Separate loader, classifier, extractor, reconciler | One prompt does everything | Stages are tested alone: 55 tests, 15 of them regression cases for comparison rules | `python -m unittest discover -s tests` |
| Secrets | API key in gitignored `.env`, plus `.env.example` | Key in code | Basic hygiene only | Show `.gitignore` |

## 2. Latency (520-email bundle, single thread)

Totals: cold 4.33 s (120 emails/s, p50 5.1 ms, p95 21.5 ms); warm 0.35 s (1,472 emails/s, p50 0.25 ms, p95 2.7 ms, p99 9.1 ms).

| Stage | Mean per item, cold | Mean per item, warm |
|---|---|---|
| Load email | 6.2 ms | 0.08 ms |
| Classify | 0.11 ms | 0.11 ms |
| Load attachments (220 checks) | 4.4 ms | 0.9 ms |
| Extract | 0.31 ms | 0.20 ms |
| Reconcile | 0.06 ms | 0.05 ms |

The cold run is dominated by reading files, not by NavisAI's logic. The AI-related stages together cost under 1 ms per email.

**Tokens and cost:** now recorded per live vision call (see section 12). Only a 2-document sample exists, and no per-token price is stored, so no dollar figure is quoted.

## 3. Reliability against the two answer keys

| Result | Test doc (260 checks) | Test-doc-2 (190 checks) |
|---|---|---|
| Matches expected status, every condition | 260/260 | 190/190 |
| Real mismatch passed as OK | 0 | 0 |
| Flagged MISMATCH though truly OK | 0 | 0 |
| Escalated to review | 185 | 136 |
| Escalated but semantically a real mismatch | 12 | 8 |
| Escalated but semantically fine | 173 | 128 |

Conditions covered: clean, field mismatch, missing field, conflicting field, wrong document type, duplicate pair, encrypted, corrupted, rotated, noisy, blurred, low resolution.

Cautions:
- The high review rate on the stress sets is not a benefit. The key expects review for encrypted, corrupted or incomplete files. On the official bundle the review rate is 17 of 220 (7.7%); use that figure.
- Both stress sets come from one generator, and rules were tuned on the first. Neither is a blind test.

## 4. Explainability

The chain SI value, BL value, normalised comparison, difference, evidence, decision, reason exists in the API response. Gaps:
- Scanned documents read by vision carry no source evidence (0 of 252 fields in the Test-doc-2 sample). Only 55% of fields on clean OK results have SI and BL evidence for this reason.
- 24 of 28 defect fields (first set) and 18 of 21 (second set) have evidence on both sides.
- The normalisation rule applied (suffix, unit, port alias) is not recorded in the result.

## 5. Human-in-the-loop, automation, architecture, security

- **HITL is part of the flow.** The reconciler decides escalation and the UI routes on it with a named reason.
- **Confidence is a heuristic** (fixed 0.98 for text extractions, 0.40 with a placeholder) and does not drive escalation; structural conditions do. Do not call it calibrated.
- **Furthest stage reached:** Extract, Compare, Explain, Recommend. "Prepare action" is a drafted notice. Execute is simulated (no email is sent). Verify is not built. NavisAI is not autonomous.
- **Extensibility is partial.** The loader already recognises invoices, packing lists and certificates of origin (`sdoc_loader.py:212`), but extraction and comparison are hard-coded to the seven SI/BL fields (`gross_weight_kg` appears 12 times in the extractor and 4 in the reconciler). A new document type currently needs new patterns and compare rules, not a config file.
- **Security:** see `STANDARDS_ALIGNMENT.md`. No authentication, plaintext storage, and scanned pages go to Gemini.

## 6. Metrics to benchmark, ranked by decision value

1. Real mismatches passed as OK (0 on both keys)
2. Escalation rate on realistic data, split into correctly stopped versus unnecessary
3. Live model calls per 520 emails from a cold cache
4. Evidence coverage, including the scanned path
5. Reproducibility (byte-identical reruns)
6. Latency p50 and p95, cold and warm
7. Tokens and cost per scanned document (needs usage capture)
8. Test count and regression coverage
9. Human touches per shipment (needs an Averis baseline)

## 7. Easily overlooked advantages

- Vision reads are cached by file hash, so a re-run of the same scan costs 0 calls, and a failed read is never cached.
- On a daily quota error the extractor moves to the next model instead of retrying.
- A field stated twice, or a second copy of the BL, becomes a review item instead of a silent pick.
- The 0.5 kg weight tolerance catches a 1 kg difference but ignores rounding noise (tested both ways).
- Import is hardened: path sanitising, extension allow-list, size caps.
- A failure on one email becomes NEEDS_REVIEW with the error text and never crashes the batch.

## 8. Claims we can safely make

- "520 emails processed in under half a second warm (4.3 s cold), with no live model calls in the measured run. Only 6 of 440 documents needed a vision read."
- "Zero real mismatches passed as clean across 450 stress-test document checks against two answer keys."
- "Every field read from a text document carries its source line."
- "Unreadable, missing, conflicting or duplicate documents are escalated with a named reason instead of guessed."
- "Comparison is deterministic and repeatable."

## 9. Claims we should not make

- "Batched LLM inference", "structured Pydantic outputs" or "AI-powered classification".
- "Autonomous", "executes actions", or "carrier amendment sent". Nothing is sent.
- Any token, cost or dollar figure.
- "Better than other teams", or any statement about competitors.
- "Calibrated confidence", "100% evidence coverage" (scans have none), "compliant" or "certified".
- "Easy to add new document types" until one has actually been added.

## 10. Final positioning

NavisAI is a deterministic-first verification engine that uses AI only where rules cannot read the document. That gives three properties that can be demonstrated live: it needs almost no model calls, it is repeatable, and it stops instead of guessing.

## 11. Changes that would strengthen the case (not yet made)

1. ~~Record tokens and latency per vision call~~ — done: each live call is now logged to `.cache/vision_calls.jsonl` (model, latency, prompt/output/total tokens, outcome; no document content). A first sample of 2 scanned documents is in section 12.
2. Add source evidence (quoted text or a region) to vision reads, so traceability covers every field.
3. Record the normalisation rule used in each comparison result.
4. Run one held-out test on documents no rule was tuned against, and report it.
5. Add one more document type (invoice against packing list) end to end, to turn "extensible" into evidence.
6. Fix the stale classifier docstring and keep the benchmark script in the repo so judges can rerun it.

## 12. First measured vision-call sample (2 scanned documents, live)

| Document | Model that answered | Latency | Prompt tokens | Output tokens | Total |
|---|---|---|---|---|---|
| Scan A | gemini-3.5-flash-lite | 1,956 ms | 1,403 | 158 | 1,561 |
| Scan B | gemini-3.5-flash-lite | 2,919 ms | 1,403 | 158 | 1,561 |

- The primary model (`gemini-3.6-flash`) failed first on both documents (a server error and client errors, probably quota), so the fallback model answered. The failed attempts took 0.2–4.1 s each and no tokens were reported for them.
- Prompt size was the same (1,403 tokens) for both single-page scans, so cost per scanned page is roughly constant. Price per token is not recorded, so no dollar figure is given.
- I forced three documents through the vision path; one was actually readable as text, so only two made live calls. One scan (Scan A) came back with a successful call but was judged not legible, so it was not cached and would go to human review.
- Sample size is tiny (free-tier quota is 20 requests per day per model). Treat these as indicative, not as a benchmark. A fair figure needs 30 or more calls.
- The answering model varies with quota, so the same scan can be read by different models on different days. Model and prompt version are not part of the cache key yet.

