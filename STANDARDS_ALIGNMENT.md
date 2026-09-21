# NavisAI — Standards Alignment Assessment

**Status: design-aligned, not certified.** NavisAI holds no certification, attestation or approval against ISO/IEC 42001, ISO/IEC 27001, DCSA, the EU AI Act or SOC 2. This document maps what exists in the codebase to those frameworks, states what is missing, and places each gap on the roadmap. Every "implemented" item cites a file. Framework details should be checked against the current published versions before external use.

## 1. Where NavisAI stands

Maturity: **Level 1–2 of 6** (prototype moving toward governed AI).

| Level | Meaning | Status |
|---|---|---|
| 1 Prototype | AI demonstrates verification | Done |
| 2 Governed AI | Validation, oversight, auditability | Partly done |
| 3 Secure enterprise | Auth, RBAC, encryption, monitoring | Not started |
| 4 Interoperable | DCSA-aligned data model and APIs | Not started |
| 5 Independent assurance | External testing and certifications | Future commercial milestone |
| 6 Autonomous operations | Risk-tiered autonomous actions | Future |

## 2. What is real today

| Capability | Evidence |
|---|---|
| Deterministic SI-vs-BL comparison on 7 fields, with tolerance and port rules | `sdoc_reconciler.py`, `tests/test_strictness.py` |
| "Escalate, do not guess" on unreadable, missing, conflicting or duplicate documents | `sdoc_reconciler.py`, `sdoc_extractor.py` (`_from_vision`, `_detect_conflicts`) |
| Per-field evidence spans and redline view | `sdoc_extractor.py`, `web/src/components/Redline.tsx` |
| Hash-chained audit ledger with integrity check | `sdoc_security.py` (`TamperEvidentAuditLedger`), `/api/health` |
| Hardened folder import (path sanitising, extension allow-list, size caps) | `api/importer.py`, `tests/test_import.py` |
| Measured accuracy | 55 unit tests; hackathon official score 0.9946; DOCSTRESS 260/260 against the Gemini answer key |
| Config-driven model choice, API key kept in a gitignored `.env` | `.env.example`, `.gitignore` |

Caveat on accuracy: the DOCSTRESS key was used while fixing rules, so it is no longer a held-out test.

## 3. Autonomous decision pipeline

| Stage | Status |
|---|---|
| AI proposes (regex first, Gemini vision for scans) | Yes |
| Schema validation | Partial: vision JSON is not type-checked before use |
| Data validation (placeholders, conflicts, legibility) | Yes |
| Cross-document validation | Yes |
| Confidence validation | Partial: heuristic, not calibrated (deterministic extractions fixed at 0.98, 0.40 with placeholder) |
| Risk engine | Partial: `sdoc_risk.py` is advisory, Streamlit-only, uses assumed constants |
| Policy / permission check | Missing |
| Human approval | Partial: decisions are recorded, nothing gates on them |
| Action execution | Simulated (`sdoc_monitor.py` writes a ledger entry, sends nothing) |
| Result verification | Missing |
| Audit log | Yes, with the weaknesses in section 4 |

What the ledger records per action: timestamp, action, actor string and a free-form `details` dict. It does **not** record source-document hash, extracted or normalised values, model and prompt version, confidence, rules applied, risk level, policy decision, external response or final result.

## 4. Gap analysis

**Critical (before real customer data or any autonomy)**
- No authentication or authorisation; audit `actor` is a client-supplied string.
- No policy layer between decision and action.
- Ledger is a local, unsigned JSON file; write failures are swallowed; no locking; no external anchor.
- Ledger does not capture the evidence behind each decision.
- Scanned pages are sent to Gemini with no data-handling decision.
- Human decisions in imported datasets are held in memory only.

**Important**
- Confidence not calibrated; vision output has no confidence.
- Vision cache is keyed by file bytes only, so a model or prompt change reuses stale reads.
- No encryption at rest or in transit, retention policy, backups, structured logging, CI or dependency scanning.
- CORS is open (`allow_origins=["*"]`).
- Need a held-out evaluation set.

**Improvement**
- Pydantic response models and a published OpenAPI contract.
- Prompt-injection and adversarial document tests.
- Reviewer identity and override analytics.
- Replace deprecated `datetime.utcnow()`.

**Future**
- eBL workflows, digital signatures, DCSA data exchange, external ISO/SOC 2 assessment, EU conformity work if classified high-risk.

## 5. Standards scorecard

| Framework | Alignment today | Evidence | Missing | Next action | Roadmap |
|---|---|---|---|---|---|
| ISO/IEC 42001 | Low–Medium | Human-review route, evidence spans, tests, benchmarks, hash-chained ledger | AI risk register, monitoring, change control, roles, data governance | AI system record: purpose, limits, risks, metrics, model and prompt versioning | 2028–29 Govern |
| ISO/IEC 27001 | Low | Upload sanitising and caps, gitignored secrets, ledger integrity check | Auth, RBAC, encryption, retention, backup, incident process | Authentication, roles, TLS, encrypted storage, secrets manager | Basics in 2026; ISMS in 2028–29 |
| DCSA B/L, Booking | Low–Medium | Seven fields map to DCSA concepts; FastAPI emits OpenAPI | UN/LOCODE, structured parties, per-container data, references, states | Canonical Pydantic model with DCSA-named fields | 2027 H2 – 2028 |
| DCSA eBL | Low (not built) | None | Identity, signatures, issuance, surrender, endorsement | Build dependencies first (section 7) | 2030+ |
| EU AI Act principles | Low–Medium | Partial logging, oversight, accuracy evidence | Risk and quality management, technical documentation, data governance | Legal classification review, then technical file | 2028–29 Govern |
| SOC 2 | Low | Processing-integrity evidence only | Security, availability, confidentiality, privacy controls | Controls above, then readiness assessment | After 27001 basics |

## 6. Risk-based autonomy (design target)

Risk decides autonomy; confidence alone does not.

| Tier | Examples | Behaviour |
|---|---|---|
| Low | Format-only differences, equivalent port names, duplicate notices | May be automated |
| Medium | Minor amendments, carrier communication, data corrections | Policy check or one approval |
| High | Consignee or shipper difference, weight beyond tolerance, ambiguous or unreadable document, low confidence | Always human review |

Target flow: document, extraction, schema check, deterministic validation, SI/BL reconciliation, risk tier, policy engine, human approval if required, action, result check, signed audit record. The LLM stays in extraction and explanation; numbers, identities and ports are decided by deterministic code. Autonomy is off by default per customer.

## 7. DCSA and eBL notes

- Current fields map to DCSA parties, locations, equipment and references, but ports are free text (not UN/LOCODE), weight and container count are single totals (not per container), and booking and BL references are regex-extracted into `meta` (`api/main.py`, `_meta`).
- eBL dependencies, in order: authenticated identities, canonical versioned document model with states, signed audit records, carrier and platform APIs, a legal position on platform-party status.

## 8. EU AI Act notes

NavisAI has not been classified. Document verification for logistics does not obviously fall in the listed high-risk areas, but that needs legal review per jurisdiction and use case. Some obligations (such as transparency) can apply regardless of tier, and timelines have shifted, so verify current dates. Relevant principles and current state: logging (partial), human oversight (partial), accuracy and robustness (partial, no adversarial testing), transparency (partial), risk and quality management, technical documentation and data governance (missing).

## 9. Roadmap placement

| Stage | Add |
|---|---|
| 2026 Verify | Fix overclaims (done), authentication and roles, durable and locked ledger recording evidence and model and prompt version, persisted decisions, held-out test set, Gemini data-handling note |
| 2027 H1 Understand | Schema-validated vision output, confidence method for vision, prompt-injection tests, model-versioned cache |
| 2027 H2 Expand | Canonical DCSA-named schema with per-container and dangerous-goods data, UN/LOCODE resolution |
| 2028 Act | Policy engine, risk tiers, real dispatch with result verification, DCSA adapters, Track & Trace events |
| 2028–29 Govern | AI management records, ISMS work, monitoring, retention, incident process, security testing, SOC 2 readiness, legal review |
| 2029+ Predict | Drift and override analytics feeding the risk model |
| 2030+ Autonomize | eBL after signatures, identity and platform-party analysis |

## 10. Pitch statement

> NavisAI is being designed around recognised international frameworks — ISO/IEC 42001, ISO/IEC 27001, DCSA, the EU AI Act principles and SOC 2 trust criteria — rather than certified against them. Today it provides deterministic cross-document validation, evidence for every field, a human-review route for uncertain cases, and a hash-chained audit log, benchmarked at 260 of 260 document checks against a published answer key. We do not yet execute actions autonomously. Autonomy will be introduced by risk tier, behind a policy engine with human approval for high-risk cases, alongside an external assurance programme.

Avoid the words "compliant", "certified", "blockchain", "SOX" and "eBL" in the pitch.
