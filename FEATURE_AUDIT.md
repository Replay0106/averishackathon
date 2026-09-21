# NavisAI — Feature Audit vs Use Case & Judging Rubric

Checked against *Shipping Document Verification Use Case.pdf* and *Averis x Monash Hackathon 2026 — Preliminary Judging Rubric.pdf*.
Evidence: `run_tests.py` (32/32 pass), a full pipeline run over the 520-email bundle, `submission.json` vs `sample_submission.json` (520/520 keys, identical schema), and a code read of the extractor, reconciler, API and web UI.

Legend: **Done** · **Partial** (works, with a gap) · **Missing**

## 1. Core use-case requirements

| Requirement | Status | Evidence / gap |
|---|---|---|
| Classify 5 categories | **Done** | After the classifier fix (see 1a): accuracy 0.971, macro-F1 0.948. Remaining gap is SPAM recall 0.80 (phishing-style messages filed as GENERAL). |
| Extract fields (plain text) | **Done** | All 7 fields plus per-field evidence (line number, snippet, confidence). |
| Align differently labelled fields | **Done** | Handles "Load Port", "Discharge Port", "POL/POD", "To the order of", "Gross Wt (kgs)", "Notify Party/Intermediate Consignee", and more. |
| Compare and show SI vs BL side by side | **Done** | Live verification, Redline tab, review drawer. |
| Explain each mismatch | **Done** | Templated explanation, e.g. "BL declares 2 additional containers (3 vs 5)". |
| Say "No mismatch detected" when all 7 match | **Partial** | UI says "ALL 7 FIELDS VERIFIED". Use the exact wording the brief asks for. |
| Report: which email, mismatch or not, what needs attention | **Partial** | Visible in the UI. No exportable report (CSV / JSON / PDF) from the web app. |
| Escalate to a human with context | **Done** | 4 reason codes, evidence shown, Confirm / Override, hash-chained audit ledger. |
| Self-evaluation output format | **Done** | `submission.json` matches the sample schema and scores **0.9651** (see section 1a). |

## 1a. Official self-evaluation (2026-09-21)

Run with the organisers' `score_cli.py` from `sdoc-hackathon-docker.zip` (same scorer as `POST /submit`; Docker not installed, so run directly), on a fresh `sdoc_pipeline.py` output. The committed `submission.json` gives the identical score.

| Metric | Result |
|---|---|
| **Final score** (30% Stage 1 + 20% Stage 3 + 50% end-to-end) | **0.9269** |
| Stage 1 classification: accuracy / macro-F1 | 0.796 / 0.820 |
| Per category F1: BL_COMPARISON / SI_REQUEST / INVOICE / GENERAL / SPAM | 0.74 / 0.97 / 1.00 / 0.50 / 0.89 |
| Stage 3 defect recall / precision | 1.000 / 0.920 |
| Stage 3 field-level F1 / exact-match | 0.954 / 0.975 |
| End-to-end defects caught | 45 of 46 (0.978) |
| Escalation recall / precision | 0.850 / 1.000 (17 of 20 flagged) |
| Escalation by reason | wrong_doc_type 5/5, missing_attachment 5/5, missing_value 5/5, **unreadable 2/5** |

Reading: comparison and escalation are strong, and classification is the weak link. Stage 1 recall on BL_COMPARISON is the main upside. Defect precision 0.92 confirms some false alarms, and 3 unreadable files are not being escalated.

**Update after the classifier fix.** 91 emails asking the shipper to send the draft BL for checking (no attachments) were filed as GENERAL, giving BL_COMPARISON recall 0.59. They are now classified BL_COMPARISON (`is_draft_request` in `sdoc_classifier.py`) and reported OK, since nothing is missing or comparable yet. Result: **final 0.9269 to 0.9651**, accuracy 0.796 to 0.971, macro-F1 0.820 to 0.948, BL_COMPARISON F1 0.74 to 1.00. Sending them as NEEDS_REVIEW instead dropped escalation precision to 0.16, so they stay OK. Tests: 32/32 (distribution test updated to 220 / 132 / 75 / 61 / 32). The table above is the pre-fix run.

**Update after the spam, table-parsing and scanned-document fixes: final score 0.9946.** Classification accuracy 0.987 / macro-F1 0.982 (SPAM F1 1.00), Stage 3 defect precision 1.000 / recall 1.000, escalation 20 of 20 (unreadable 5/5), 46 of 46 defects caught end to end. 33/33 tests pass.
- **Spam:** phishing and "bank officer" scam phrases added to the spam triggers.
- **Tables:** `Consignee (Non-Negotiable)` no longer splits at the hyphen, and the `TOTAL Gross Wt` line is now read before the table header, so container numbers are no longer parsed as weights. The 6 suspect emails resolve correctly (email_351 stays a genuine 15 vs 16 container defect).
- **Scans:** the hard-coded `OFFLINE_SCANNED_CACHE` is removed. Scanned PDFs are read by Gemini vision, and if vision fails or returns nothing they are marked unreadable and go to human review. Successful live reads are cached by file hash in `.cache/vision_cache.json`. Re-measured with live vision (default model `gemini-3.6-flash`, override with `GEMINI_MODEL`; falls through to `gemini-3.5-flash-lite` when a model's free-tier daily quota of 20 is used up): the final score is still **0.9946**, but the 3 scans are now read successfully (SI = BL), so they report OK and the diagnostic escalation recall is 0.85 (unreadable 2 of 5). The reference treats those scans as unreadable; that axis is not part of the final score.

## 2. Advanced stage

| Challenge | Status | Evidence / gap |
|---|---|---|
| PDF and Word attachments | **Partial** | Loader parses pdf (28), docx (8) and xlsx (22) files, and results come back for all. Table extraction accuracy is unvalidated. |
| Scanned documents | **Partial, risky** | Vision path exists, but `OFFLINE_SCANNED_CACHE` in `sdoc_extractor.py` is checked first and hard-codes results for 6 named files (email_512–514). A judge reading the code will see this as hard-coding. The new API does not load `.env`, so live vision is off there and those 3 shipments show OK with no visible scan warning. |
| Messier inputs (labels, subjects, missing attachments) | **Partial** | Labels, missing attachments and wrong document types are handled. About 6 of the 50 reported mismatches look like reading errors, not real discrepancies: consignee captured as "Negotiable)", and weight 4,736,471 vs 131,322 kg (email_059/208/273/351/407/499). These are false alarms, which the brief penalises. |
| Reliability and human review | **Partial** | Confirm and override work and are audited. Retry exists only in the Streamlit app, not the React UI. Overrides in the API live in memory and are lost on restart, and they are not written back to `submission.json`. |

## 3. Extras built beyond the brief

Done in the React UI: inbox triage, animated verification pipeline, redline, compliance gate, amendment flow, analytics, audit trail, copilot, command palette, offline snapshot fallback.

Built in the Python pipeline but **not exposed** in the new UI or API: risk scoring (`sdoc_risk`), SLA prioritisation (`sdoc_sla`), consensus (`sdoc_consensus`), security scan (`sdoc_security`), client rectification and autonomous monitor (`sdoc_monitor`), and the scanned-image quality banner.

## 4. Rubric self-assessment (my estimate, not an official score)

| Criterion | Max | Likely band | What moves it up |
|---|---|---|---|
| System Design & Architecture | 15 | Strong (8–11) | Add an architecture and data-flow diagram to the README and justify the deterministic-first, LLM-fallback design. |
| Working Core Prototype | 25 | Strong (13–18) | Remove false mismatches, prove the scanned path without the cache, and make one command start API and UI. |
| Technology Integration | 15 | Strong (8–11) | Show the LLM used only where needed (scans), with the API, `.env`, and error handling wired in properly. |
| Technical Feasibility & Validation | 15 | Developing–Strong (7–11) | Record the `/submit` score, a confusion matrix for classification and defects, and a list of known limitations. |
| Problem Understanding | 10 | Strong–Excellent | Already strong (PRODUCT.md, 4 review reasons). Cite the brief's own three problems in the demo. |
| Innovation & Approach | 10 | Strong | Lead with "escalate, never guess" and the tamper-evident ledger. |
| Practical Value & Potential | 10 | Strong | Add a roadmap with the DCSA / EDI path, already in the UI, and a time-saved estimate. |

## 5. Recommended upgrades, in priority order

1. **Remove the scanned-file hard-coding** or clearly label it as a cached demo fallback. Load `.env` in the API so live vision runs, and show a "Scanned document" banner with legibility in the web UI.
2. **Stop false alarms** (defect precision 0.92). Route to NEEDS_REVIEW when an entity value is truncated (ends in `)`, under about 10 characters) or when weights differ by more than 3x. Re-check the 6 flagged emails.
3. **Next scoring gains.** SPAM recall 0.80 (phishing-style messages are filed as GENERAL), escalate the 3 unreadable files currently missed, and cut the false defects (precision 0.92). Then save the scorer output in `docs/`.
4. **Close the reliability loop in the web UI.** Add a Retry action, show per-email processing failures, persist overrides to disk, and update the report after a reviewer decision.
5. **Use the exact phrase "No mismatch detected"** and add a downloadable report.
6. **Expose the existing Python features** through the API and UI: risk score, SLA ordering, security scan.
7. **Replace illustrative UI values.** The inbox received-times, waiting times and classification confidence are placeholders, so use real values or label them clearly.
8. **Packaging.** Add `docker compose up`, one start script, a README architecture diagram, and remove the Streamlit duplicate once the React UI reaches parity.
9. **Hardening.** Restrict CORS from `*`, add basic auth, rate-limit the copilot endpoint, and add tests for the API and for the false-alarm cases.
