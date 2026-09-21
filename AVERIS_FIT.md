# NavisAI — Averis Strategic Fit Assessment

Purpose: give the pitch a reasoned, evidence-backed case for Averis. Three kinds of statement are kept apart throughout:

- **PUBLIC** — stated on Averis' own website or in an SAP news release (sources at the end).
- **ALIGNMENT** — my reasonable inference about fit. It is a judgement, not an Averis fact.
- **CONFIRM** — an assumption that only Averis stakeholders can confirm. Nothing internal is assumed.

## 1. What Averis has said publicly

| Topic | PUBLIC statement | Source |
|---|---|---|
| Scale | Founded 2006, 1,000+ professionals, 8 countries, 60,000+ users across 32 locations | Our Story |
| Shipping Documentation | Prepares invoices, packing lists, shipping certificates, certificates of origin (finalised with MITI / chambers of commerce), shipment advice, bills of lading, export permit declarations for Singapore and Malaysia; administers Letters of Credit including policy validation and discrepancy resolution; tracks shipping documentation and billing data; handles shipping inquiries | Shipping Documentation page |
| Digital Services | Digital Platform & Project Management, Enterprise Architecture, Digital DevOps, Data Science & AI, Digital Quality Assurance, Research & Data Acquisition, UI/UX | Digital Services page |
| RPA | Started 2018 "in the spirit of Continuous Improvement and Innovation"; reduces mundane, repetitive tasks so people take on higher-value roles; RPA "did not cut down the number of employees" and created new roles; structured up-skilling | Blog and Our Story |
| Cybersecurity | Cyber security to secure customers' operations, data and intellectual property; local cloud storage, data-centre management | IT Operations page |
| Cloud and SAP | SAP-certified partner; RISE with SAP hybrid model that moved most infrastructure to cloud **while keeping confidential customer information on-premise**; reported 8.5% rise in client satisfaction and 11.5% drop in total cost of ownership | SAP news release |
| Sustainability | Corporate sustainability aligned to UN SDGs (good health, quality education, decent work, responsible consumption); reduce, reuse, recycle | Corporate Sustainability page |

Not found publicly, so not assumed: Averis' ticket volumes, handling times, error rates, its actual shipping-document tooling, any AI policy, or any stance on external AI services.

## 2. The strongest reasoning for the pitch

1. **The workflow is already Averis' workflow.** Averis prepares and administers shipping documents and resolves LC discrepancies. NavisAI checks a prepared document against its source and explains any discrepancy. That is a genuine operational task, not a chatbot. *(ALIGNMENT)*
2. **The philosophy matches.** Averis describes automation as removing repetitive work and moving people to higher-value work, with no headcount cut. NavisAI's design is the same shape: clean matches pass, exceptions go to a person with the evidence attached.
3. **The pattern is measurable.** See section 4.
4. **The main risk is data handling, and it can be named up front.** Averis' own SAP story keeps confidential customer data on-premise. NavisAI currently sends scanned pages to a public AI service. Raising this first, with a fix path, is more credible than having it found (section 6).

## 3. Fit scorecard

Ratings are for what exists in the code today.

| Averis priority | NavisAI alignment | Evidence in the codebase | Gap | Future opportunity |
|---|---|---|---|---|
| Digital transformation | Medium | End-to-end flow from inbox to verified result to logged decision: classifier, extractor, reconciler, review UI (`api/main.py`, `web/`) | Not connected to any Averis system; email dispatch is simulated (`sdoc_monitor.py`) | Case-management layer per shipment |
| AI & data science | Medium | Deterministic extraction first, Gemini vision only for scans; 0.9946 official hackathon score; 260/260 on DOCSTRESS Gemini key | Confidence is heuristic; no monitoring or drift tracking; test key was used for tuning | Calibrated confidence and a held-out set |
| RPA / automation | Medium | 520-email run needs no human on 457 emails; 3 s for 1,299 cached emails | No real action execution; no policy engine | Risk-tiered automation behind approvals |
| Continuous improvement | **Low** | Overrides are recorded to the ledger (`/api/emails/{id}/action`) | Nothing feeds a human decision back into rules or an evaluation set; decisions live in memory for imported datasets; no false-positive / false-negative tracking | The feedback loop in section 5 |
| Shipping documentation | Low–Medium | SI and BL only, 7 fields | Invoice, packing list, CoO, VGM, dangerous goods, export permit, LC not covered | Document-type modules on one shared engine |
| Digital quality assurance | Medium | 55 tests including regression rules from DOCSTRESS (`tests/test_strictness.py`), deterministic validators, "escalate, do not guess" | Coverage not measured; no CI; no production monitoring; no sampling of auto-passed items | QA dashboard: precision/recall over time, sampled audits |
| Cybersecurity | **Low** | Upload sanitisation and caps (`api/importer.py`), gitignored secrets, ledger integrity check | No auth, RBAC or encryption; third-party AI data flow undecided | See `STANDARDS_ALIGNMENT.md` |
| Enterprise integration | Low | FastAPI with OpenAPI, JSON in and out, stateless enough to wrap | No connectors (SAP, email server, DMS, RPA); ad hoc schemas | Connector layer and canonical model |
| Human-centred automation | Medium–High | Review queue, per-field evidence, redline, override actions, explicit reasons for every escalation | No reviewer identity; no feedback or training loop | Reviewer workbench with accountability |
| Sustainability | Low (unmeasured) | None | No measurement | Track pages, rework and amendments avoided; do not claim carbon |

## 4. Measurable improvement — what we have and what Averis must supply

**Measured from our own runs (reproducible):**

| Metric | Value | Source |
|---|---|---|
| Emails processed in official bundle | 520 (220 SI/BL checks, 132 SI requests, 75 invoice queries, 53 general, 40 spam) | `submission.json` |
| Hands-free outcome on SI/BL checks | 157 of 220 clean (71%); 46 discrepancies found (21%); 17 sent to review (8%) | `submission.json` |
| Review reasons | 5 wrong document, 5 missing attachment, 5 missing value, 2 unreadable | `submission.json` |
| Accuracy | Official score 0.9946; classification accuracy 0.987; defect precision and recall 1.00 | Official scorer run |
| Speed | 1,299 emails in about 3 s with cached vision reads (text path is millisecond-scale) | Local run |
| Stress test | 260 of 260 SI/BL checks match the Gemini answer key: 28 discrepancies, 185 reviews, 47 clean | `answer key.xlsx` |

Read the stress numbers carefully. The 185 reviews reflect how the stress set was designed (many deliberately broken documents), not a typical exception rate. The official bundle's 8% review rate is the better guide. The answer key was used while fixing rules, so it is no longer a blind test.

**Averis must supply the baseline.** Do not state savings until then.

| Metric | How to measure | Needs from Averis |
|---|---|---|
| Processing time per check | Timestamp per stage in the ledger | Current minutes per manual SI/BL check |
| Manual touches | Count of human actions per shipment | Current touch count |
| Exception rate | Reviews divided by checks | Current rework rate |
| Rework and amendments | Amendments after first check | Baseline amendment counts |
| Accuracy | Precision and recall against sampled human audits | Sampled audits |
| SLA adherence | Cut-off met versus missed (`sdoc_sla.py` extracts cut-offs) | SLA definitions |
| Cost per transaction | Loaded cost per check before and after | Cost model |
| Review workload | Items per reviewer per day | Staffing data |

Illustrative arithmetic only, with **placeholder** inputs that Averis must replace: hours saved = checks per month × share cleared without a person (0.71 in the official bundle) × minutes per manual check ÷ 60.

## 5. The continuous-improvement loop (what makes the positioning credible)

Target loop:

```
AI decision → validation → human or system outcome → feedback → evaluation dataset → rule / model improvement
```

What exists: AI decision, validation, and outcome capture (partial). What is missing:
- Persisted outcomes: every confirm or override stored with reviewer, reason and the evidence shown.
- Feedback classification: false positive, false negative, or correct.
- Evaluation dataset that grows from real overrides, with a held-out split.
- Regression gate: each rule or prompt change must pass the accumulated set before release (the DOCSTRESS regression tests are a working prototype of this).
- Quality dashboard: precision, recall, override rate and review time over time.
- Sampling: a fixed percentage of auto-cleared items goes to a human, so false negatives are found.

## 6. Cybersecurity and third-party AI (the important risk)

| Question | Finding |
|---|---|
| Could uploaded documents reach an external AI service? | **Yes.** Scanned pages are sent to the Google Gemini API (`sdoc_extractor.py`). Text-based PDFs, classification, comparison and the ledger run locally. In the official bundle only 3 scanned PDFs took the vision path. |
| Is there a control over it? | No consent flag, redaction, per-customer switch or data-processing agreement. |
| Also stored locally | Imported datasets, the ledger and the vision cache (`.cache/`) are plaintext files. No retention or secure-deletion policy; dataset delete removes files but does not securely erase. |
| Access control | None: any caller can list, import or delete datasets and write audit entries. |

How this reads against Averis' public position (hybrid cloud, confidential data kept on-premise): the text path is compatible in principle; the vision path is not until it is changed. Options, cheapest first:
1. A switch that disables vision, so scanned files go straight to human review (already the fallback behaviour).
2. A contractually protected enterprise AI endpoint with a regional data-handling agreement, subject to Averis' view.
3. A self-hosted vision model inside the Averis environment.
4. Redaction of party names and amounts before any external call.

*CONFIRM:* Averis' policy on external AI services and on which customers' documents may leave the environment.

## 7. Enterprise integration

Averis publicly highlights SAP and enterprise IT. NavisAI can coexist as a service, but nothing is connected. Needed, in order:
- Authenticated API with roles, so other systems can call it.
- Canonical shipment/document model (DCSA-shaped) with a mapping layer.
- Connectors: shared mailbox, document store, SAP (for booking and billing references), ticket or RPA hand-off.
- Event outputs (webhooks) for downstream workflow tools.

*CONFIRM:* which systems hold booking, billing and document master data at Averis, and their integration standards.

## 8. From verifier to shipping-document operations platform

Averis' listed documents give a natural sequence. The engine (extract, normalise, validate, reconcile, review, log) is reusable; each document type needs its own field schema and rules.

| Stage | Documents | Cross-checks |
|---|---|---|
| Now | SI, draft BL | SI against BL, 7 fields |
| Next | Commercial invoice, packing list | Invoice against packing list against BL: quantities, weights, consignee |
| Then | Certificate of Origin, VGM, dangerous goods, export permit | Origin and goods against invoice; VGM against BL weight; DG declaration against cargo |
| Later | Letter of Credit terms | Presentation against LC terms (the risk engine already cites UCP 600 as advisory context) |
| Platform | Shipment-level case: all documents, amendments, status, deadline | Case view and audit across documents |

## 9. People and autonomy

Present today: review queue, escalation reasons, per-field evidence and redline, override and confirm actions, one place to see every decision.

Missing: reviewer identity and accountability, comments and training feedback, visibility of what the AI did autonomously (nothing acts autonomously yet), and workload views. Success should be reported as review time freed and exceptions resolved faster, not as staff hours removed, which matches how Averis describes its own RPA.

## 10. Sustainability

No carbon or environmental claim is supportable today. Measurable candidates for a pilot:
- Pages and duplicate documents handled per shipment.
- Rework loops and amendments per shipment before and after.
- Repeat-processing volume avoided.

Shipment emissions intelligence is a future idea and needs data sources Averis has not been shown to provide publicly.

## 11. Can NavisAI credibly be positioned as a continuous-improvement platform?

**Today: not yet.** It is a strong, measured, well-tested document checker with human review. The improvement loop and platform breadth that the positioning claims are not built.

**It becomes credible with these changes**, in priority order:

1. **Product:** persist every human decision with reviewer, reason and evidence; classify each as correct, false positive or false negative; show a quality dashboard.
2. **Architecture:** authentication and roles; per-customer switch for external AI; canonical shipment model; connector layer.
3. **Workflow:** sampled audit of auto-cleared items; regression gate before any rule or prompt change; held-out evaluation set fed by real overrides.
4. **Scope:** add invoice and packing list checks, then one more document type, on the same engine, so the platform claim has evidence.
5. **Measurement:** run a pilot with Averis-supplied baseline (section 4) and report time, touches, exceptions and accuracy.

## 12. Pitch section: "Built for Averis. Designed for Scale."

**Digitise → Validate → Automate → Learn → Improve**

| Step | Today | Next | Future |
|---|---|---|---|
| Digitise | Emails and PDFs become structured data | Invoice, packing list | Full shipment case |
| Validate | Deterministic SI/BL checks with evidence | Cross-document checks | LC and customs checks |
| Automate | Clean matches pass without a person; exceptions route to review | Policy-gated actions | Governed low-risk autonomy |
| Learn | Overrides logged | Feedback to evaluation set | Drift and risk analytics |
| Improve | Regression tests | Quality dashboard | Continuous rule and model improvement |

Suggested one-liner: *"NavisAI takes repetitive verification off Averians' desks and puts their judgement where it counts — on the exceptions — with every decision evidenced, logged and measurable."*

Claims to avoid: cost or time savings before an Averis baseline; "compliant" or "certified"; "no data leaves your environment" until the vision path is resolved; "replaces" staff.

## Sources (accessed September 2026)

- Averis home and services: https://www.averis.com
- Shipping Documentation: https://www.averis.com/services/shipping-documentation
- Digital Services: https://www.averis.com/services/digital-services
- IT Operations and Project Services: https://www.averis.com/services/it-operations-project-services
- Our Story: https://www.averis.com/about-us/our-story
- RPA and continuous improvement blog: https://www.averis.com/life-at-averis/blog/a-focus-on-continuous-improvement-rpa-and-our-people
- Corporate Sustainability: https://www.averis.com/about-us/corporate-sustainability
- SAP Southeast Asia news, Averis and RISE with SAP: https://news.sap.com/sea/2024/11/averis-leverages-rise-with-sap-for-enhanced-agility-resilience-and-cost-savings/

Page contents were summarised by a retrieval tool. Check quotes and figures against the live pages before using them in a presentation.
