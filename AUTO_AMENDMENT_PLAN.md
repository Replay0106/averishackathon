# Implementation Plan — Automatic Amendment Requests (simulated sending)

Status: implemented (steps 1-8). Items marked **DECISION** were resolved as recorded in section 8.

**Sending stays simulated.** No email leaves the system. An "automatic amendment" means the system builds the amendment email to the original sender, records it as sent in an outbox and in the audit ledger, and tracks it through to resolution, without a person clicking send. The Gmail integration and any real mail channel are out of scope for this plan.

## 1. Decisions already made

| Topic | Decision |
|---|---|
| Recipient | The original sender of the SI/BL email (not the carrier) |
| Sending | Simulated only: outbox record plus ledger entry, nothing emailed |
| What happens without a person looking | Lower-risk fields only: port of loading, port of discharge, container count, gross weight |
| Held for a person | Shipper, consignee, notify party differences, plus every NEEDS_REVIEW case |
| Tabs | Merge Discrepancy Queue and Carrier Actions into one "Cases" tab |

## 2. What exists today (verified in the code)

- `sdoc_monitor.py`: `generate_client_rectification_notice` drafts the notice and `dispatch_client_rectification_email` logs a "dispatch" without sending. This is the closest existing piece.
- The web Carrier page drafts a message to the carrier; its "Confirm & send" calls the action endpoint and writes `AMENDMENT_DISPATCHED`.
- Resolution state (`Dataset.overrides` in `api/main.py`) lives in memory and is lost on restart.
- The Trust Gateway (`sdoc_gateway.py`, `/api/gateway/email-check/{id}`) scores each sender domain and can hold or quarantine an email.
- Nothing decides automatically today: every dispatch is a manual click.

## 3. Target flow

```
Email in -> Trust Gateway (sender cleared?) -> classify -> extract -> reconcile
  -> MISMATCH: only lower-risk fields differ?
        yes -> build amendment email to sender -> record in outbox as "sent (simulated)" -> ledger entry -> awaiting reply
        no  -> Cases tab: "Needs a person"
  -> NEEDS_REVIEW -> Cases tab: "Needs a person"
  -> a person can still send the same email from the Cases drawer for held cases
```

## 4. Steps

**Step 1 — Amendment policy module** (`sdoc_amendment_policy.py`, new). One pure function taking the reconciler result and the gateway verdict, returning `SEND_AUTOMATICALLY`, `HOLD_FOR_HUMAN` or `NEVER_SEND`, plus a reason.
- `NEVER_SEND`: sender quarantined or held by the Trust Gateway; no sender address.
- `HOLD_FOR_HUMAN`: any shipper, consignee or notify party difference; any NEEDS_REVIEW; ambiguous documents.
- `SEND_AUTOMATICALLY`: status MISMATCH and every defect field is one of the four lower-risk fields.
- DECISION: whether a minimum confidence is also required, and whether a weight gap has a size limit (a 1 kg gap and a 5,000 kg gap are the same today).

**Step 2 — Message builder.** Reuse the existing notice generator, addressed to the sender: each differing field with its SI value and BL value, the source documents, a request for a corrected draft BL, and the original subject.

**Step 3 — Durable outbox.** A small SQLite table replaces in-memory `overrides` for amendment state: id, dataset, email id, shipment, recipient, fields, message body, policy decision and reason, status (queued, sent-simulated, cancelled, replied, resolved), timestamps. It stops the same amendment from being "sent" twice after a restart.

**Step 4 — Ledger.** Each simulated send is recorded with fields and values compared, policy decision and reason, gateway verdict and recipient. Ledger write failures must be raised, not ignored.

**Step 5 — Trigger.** Run the policy when a dataset is processed and on demand from the Cases page. One amendment per shipment per document version.

**Step 6 — Simulated outcomes.** Because nothing is really sent, "awaiting reply" is manual: a person marks "corrected document received" from the Cases drawer, which re-verifies the shipment. (DECISION: whether to add a demo control that simulates a reply arriving.)

**Step 7 — Merge the tabs into "Cases".**
- List tabs: Needs a person, Sent, Awaiting reply, Resolved.
- One drawer per case: evidence, the email that was or would be sent, and actions: Send now (held cases), Cancel or Undo, Override result, Request re-upload.
- Keep redirects from the old `carrier` and `discrepancies` targets, and update links in the Compliance page, Overview and Audit.
- The current "Confirm AI result" button goes away for mismatches: the send action records the decision.

**Step 8 — Tests.**
- Policy: table-driven tests for each field combination and sender verdict.
- Builder: golden-file tests for the email text.
- Outbox: no duplicate record after a restart.
- A run over the 520-email bundle checking how many mismatches would be sent automatically (46 mismatches in the bundle; the lower-risk subset is smaller).
- Existing suites must still pass (60 tests plus 47 in `security_layer`).

## 5. Risks

| Risk | Mitigation |
|---|---|
| Viewers assume real emails were sent | No label in the UI (owner decision); the pitch and documentation should still say sending is simulated |
| Wrong amendment recorded because the extractor misread | Lower-risk fields only, undo control |
| Restart loses state | Durable outbox |
| Auto rules hide cases from people | "Sent" tab lists every automatic action, filterable, with the reason |

## 6. Suggested order

1. Policy module and tests (no side effects).
2. Outbox and ledger fields.
3. Run over the 520-email bundle and review the would-send list.
4. Wire the trigger.
5. Build the Cases tab.

## 7. Open decisions

1. Minimum confidence, and any size limit for the weight difference.
2. Whether an undo or cancel control is needed (simulated, so it only voids the outbox record).
3. Whether to add a control that simulates a reply arriving.
4. Whether a person can still send an amendment for a held-back case with one click from the Cases drawer.

## 8. As built

Owner instructions applied: the four open decisions above were dropped, so there is no confidence or weight-size limit, no undo or cancel control, no simulated-reply control, and no one-click send for held cases. Held cases keep the existing Confirm AI result and Override result actions.

Sender rule (owner delegated the choice): a sender the Trust Gateway rejected or quarantined at a security gate is never contacted. A gateway "hold for corroboration" caused by the document mismatch itself does not block, because every mismatch in the 520-email demo inbox carries that hold (with the literal rule, nothing would ever be sent). Imported datasets have no gateway verdict and are not blocked by it.

Where things are:
- `sdoc_amendment.py`: policy (`decide`), message builder, and the SQLite outbox (`.cache/amendments.db`, gitignored).
- `api/main.py`: `ensure_amendments` runs once per dataset per process; the outbox is keyed by the exact differences so a restart never repeats an amendment. Each send writes an `AMENDMENT_DISPATCHED` ledger entry (actor "NavisAI Auto-Amend") with the fields, values, policy, reason, gateway verdict and recipient.
- `sdoc_security.py`: ledger writes now raise on failure instead of being ignored, and are serialised with a lock.
- Web: new `Cases` page (tabs Needs a person, Sent, Awaiting reply, Resolved) replaces Discrepancy Queue and Carrier Actions; old links redirect to it.
- Tests: `tests/test_amendment.py`.

Measured on the 520-email demo bundle: 46 mismatches, of which 28 (only ports, container count or weight differ) are sent automatically and 18 wait for a person; the 17 review cases wait for a person.

Known limits: Confirm and Override decisions on held cases are still kept in memory only and are lost when the API restarts (the ledger entry stays). Deleting an imported dataset does not remove its outbox rows.

## 9. Reviewer send (added later, at the owner's request)

Cases in "Needs a person" have a "Send amendment to sender" button: one click, no preview, sent immediately to the original sender. It covers held mismatches (the same difference list as the automatic email) and review cases, which get their own wording: missing attachment, wrong document, unreadable file, blank field. After sending, the case moves to Sent and Awaiting reply exactly like an automatic one; the ledger entry `AMENDMENT_DISPATCHED` names the reviewer as approver (`auto: false`). The button is disabled, with the reason shown, when there is no usable sender address or the Trust Gateway rejected the sender (2 of the 35 held cases in the demo inbox). Endpoint: `POST /api/emails/{id}/send-amendment`.

## 10. All seven fields automatic (owner change)

The lower-risk restriction was removed: a mismatch on any of the seven compared fields (shipper, consignee, notify party, ports, container count, weight) is now sent automatically. Only review cases (unreadable, missing attachment, wrong document, blank field) still wait for a person, who can use the manual send button. The sender rule is unchanged: a sender the Trust Gateway rejected, or a missing sender address, is never emailed. The mismatches that were held under the old rule were sent when the API restarted (18 in the demo inbox; each dataset sends its own the first time it is opened). This overrides the earlier statements in sections 1 and 4 that party differences wait for a person.

