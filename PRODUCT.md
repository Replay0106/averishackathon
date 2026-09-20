# PRODUCT.md — NavisAI Durable Product Truth

## 1. Product Mission & Purpose
**NavisAI** is an autonomous trade documentation compliance and discrepancy resolution copilot built for high-volume shipping operations and Global Business Services (GBS) teams (e.g. Averis GBS, managing global commodity exports like palm oil and pulp & paper).

NavisAI solves the manual "swivel-chair" audit bottleneck by automating the entire lifecycle:
1. **Email Inbox Triage**: Ingesting messy shared operational inboxes and classifying incoming messages into actionable categories (`BL_COMPARISON`, `SI_REQUEST`, `INVOICE_QUERY`, `GENERAL`, `SPAM`).
2. **Documentary Ground Truth Matching**: Cross-verifying customer **Shipping Instructions (SI — Ground Truth)** against carrier **draft Bills of Lading (BL)** across 7 core fields before drafts are locked into official ocean manifests.
3. **Multimodal Scanned Document Vision**: Automatically detecting scanned/image-only PDFs, executing a visual quality guardrail, and reading tables, container counts, and weights without external OCR dependencies.
4. **Human-in-the-Loop (HITL) Reliability**: Intelligently escalating ambiguous, unreadable, or missing documentation with structured evidence rather than guessing or failing silently.
5. **Autonomous Dispatch & Auto-Correction**: Providing 1-click carrier amendment simulations, EDI dispatch formatting, and conversational forensic copilot reasoning.

---

## 2. Target Users & Operating Personas
- **Chief Trade Compliance Auditor**: Needs high-level risk scores, financial exposure breakdowns, and statutory conformity summaries (ICC UCP 600, ATIGA Form D).
- **Shipping Documentation Clerk**: Needs an intuitive inbox explorer, instantaneous side-by-side redline diffs, and rapid one-click resolution of draft discrepancies.
- **Operations Lead / Desk Manager**: Needs visibility into email classification distribution, HITL escalation queues, carrier response latency, and benchmark audit F1 scores.

---

## 3. The 4-Stage Operational Pipeline
```
[Stage 1: Inbox Ingestion & Classification]
       ↓ (BL_COMPARISON)
[Stage 2: Multimodal Extraction & Vision Gate]
       ↓ (Selectable Text vs Scanned PDF Vision)
[Stage 3: Smart Semantic Normalization & 7-Field Reconciler]
       ↓ (OK / MISMATCH / NEEDS_REVIEW)
[Stage 4: Autonomous Dispatch & Human Review Desk]
```

### The 7 Canonical Comparison Fields:
1. `shipper`: Registered legal entity name.
2. `consignee`: Registered consignee or negotiable "TO ORDER" clause.
3. `notify_party`: Notify entity or "SAME AS CONSIGNEE".
4. `port_of_loading`: Standardized load port.
5. `port_of_discharge`: Standardized discharge port.
6. `container_count`: Total integer count of containers.
7. `gross_weight_kg`: Total gross weight normalized strictly in kilograms.

---

## 4. Human-in-the-Loop (HITL) Escalation Mandate
When the system cannot make a dependable automated decision, it MUST escalate to human review (`status: NEEDS_REVIEW`) with one of four exact reasons:
- `missing_attachment`: The email requests comparison, but one or both required documents are missing.
- `wrong_doc_type`: Attached files are not an SI or BL (e.g., invoices, packing lists, general correspondence).
- `unreadable`: The file is corrupted, illegible, or failed the vision resolution guardrail.
- `missing_value`: One or more of the 7 essential comparison fields cannot be found with high confidence.

---

## 5. Voice & Experience Principles
- **Authoritative & Institutional**: NavisAI is a financial and legal risk gatekeeper. Language is precise, calm, and grounded in shipping and banking law (UCP 600, ISBP 745).
- **Zero Hallucination Tolerance**: Numerical values, container IDs, and port names are never invented, approximated, or assumed.
- **Action-Oriented Clarity**: Every identified discrepancy surfaces an immediate, actionable remedy (carrier amendment draft, EDI push, or review escalation).
