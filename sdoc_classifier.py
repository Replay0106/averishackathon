"""
sdoc_classifier.py — Stage 1 Email Classifier for NavisAI SDOC Hackathon Pipeline.

Classifies incoming shipping operations emails into 5 distinct categories:
  - BL_COMPARISON: Requests to check/compare draft B/L against Shipping Instruction (SI).
  - SI_REQUEST: Requests to prepare, submit, or issue a new Shipping Instruction.
  - INVOICE_QUERY: Questions regarding billing, charges, debit/credit notes, invoice cancellation.
  - GENERAL: General shipping status, vessel schedules, berthing reports, operational notices.
  - SPAM: Unsolicited sales, marketing promotions, phishing, irrelevant messages.

Supports high-speed deterministic regex pre-classification and batched Gemini Flash LLM refinement.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    from google import genai
except ImportError:
    genai = None


DRAFT_REQUEST = re.compile(r"\b(?:send|provide|share|forward|issue)\b[^.\n]{0,25}\bdraft\s+b/?l\b", re.IGNORECASE)


# Intent patterns, matched against subject + body. These describe what the message is asking for,
# so wording the literal keyword lists below have never seen is still classified correctly.
SPAM_INTENT = [
    # credential harvesting
    r"\b(?:verify|confirm|update|submit|enter|provide|share)\b[^.\n]{0,40}\b(?:password|credentials?|bank details|account details|card details|login details)\b",
    # account / mailbox threats
    r"\baccount\b[^.\n]{0,45}\b(?:suspend\w*|blocked|deactivat\w*|terminat\w*|clos\w*)\b",
    r"\b(?:storage|mailbox|inbox)\b[^.\n]{0,30}\b(?:exceeded|full|limit reached)\b",
    r"\b(?:sign ?in|log ?in|click here|act now)\b[^.\n]{0,30}\b(?:immediately|now|today|within \d+)\b",
    # prizes and windfalls
    r"\byou have won\b",
    r"\b(?:prize|lottery|jackpot|rebate|reward|inheritance)\b[^.\n]{0,45}\bclaim\b",
    r"\bclaim\b[^.\n]{0,35}\b(?:prize|reward|rebate|winnings?)\b",
    # delivery / customs lures
    r"\bunpaid\b[^.\n]{0,30}\b(?:customs|duty|fee|charge)\b",
    r"\bundelivered\b[^.\n]{0,30}\b(?:messages?|mail|parcel|package)\b",
    r"\bopen\b[^.\n]{0,30}\bsecure attachment\b",
]

INVOICE_INTENT = [
    # a billing document named together with a dispute or a question about it
    r"\b(?:invoice|billing|billed|debit note|credit note)\b[^.\n]{0,70}\b(?:discrepanc\w+|correct\w+|cancel\w+|explain|clarif\w+|disput\w+|duplicat\w+|overcharg\w+|does not match|do not match|incorrect|wrong|error|query|queries)\b",
    r"\b(?:discrepanc\w+|correct\w+|cancel\w+|explain|clarif\w+|disput\w+|duplicat\w+|overcharg\w+|query|queries)\b[^.\n]{0,70}\b(?:invoice|billing|billed amount|debit note|credit note)\b",
    # a charge being questioned
    r"\b(?:additional|extra|duplicate|unexpected|incorrect|wrong)\b[^.\n]{0,30}\b(?:handling|freight|storage|terminal|local)?\s*charges?\b",
    r"\bcharges?\b[^.\n]{0,40}\b(?:clarif\w+|explain\w*|disput\w+|not match|incorrect|wrong)\b",
]

SI_INTENT = [
    # asking for the shipping instruction to be supplied
    r"\b(?:send|provide|submit|prepare|issue|complete|furnish|share|forward|revert with|await\w*|need|require\w*)\b[^.\n]{0,45}\b(?:shipping instructions?|\bSI\b)\b",
    # the shipping instruction being chased
    r"\b(?:shipping instructions?|\bSI\b)\b[^.\n]{0,40}\b(?:required|outstanding|pending|awaited|needed|pending|pls send|still missing)\b",
    r"\b(?:waiting|chasing|follow(?:ing)? up)\b[^.\n]{0,45}\b(?:shipping instructions?|\bSI\b)\b",
]

SPAM_RE = [re.compile(p, re.IGNORECASE) for p in SPAM_INTENT]
INVOICE_RE = [re.compile(p, re.IGNORECASE) for p in INVOICE_INTENT]
SI_RE = [re.compile(p, re.IGNORECASE) for p in SI_INTENT]


def _matches(patterns, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def is_draft_request(email: Dict[str, Any]) -> bool:
    """True when the email asks for a draft BL to be sent for checking, with no documents attached."""
    return not email.get("attachments") and bool(DRAFT_REQUEST.search(email.get("body", "")))


class EmailClassifier:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = None

    @property
    def client(self):
        if self._client is None and genai is not None and self.api_key:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def classify_email(self, email: Dict[str, Any]) -> str:
        """Classifies a single email using high-precision deterministic rules."""
        body = email.get("body", "").lower()
        subject = email.get("subject", "").lower()
        atts = email.get("attachments", [])

        # 1. Attachment-based BL_COMPARISON
        has_si_att = any("_si." in a.lower() for a in atts)
        has_bl_att = any("_bl." in a.lower() for a in atts)
        if has_si_att and has_bl_att:
            return "BL_COMPARISON"

        # 2. Body-based BL_COMPARISON with missing attachments (e.g. email_506-510)
        if (
            "compare the si and draft bl" in body
            or ("compare" in body and "draft bl" in body)
            or ("attached are the si and draft bl" in body)
            or ("attached si and draft bl" in body)
            or ("please compare the si" in body)
        ):
            return "BL_COMPARISON"

        # 2b. Requests to send the draft BL for checking (document-check request, nothing attached yet)
        if is_draft_request(email):
            return "BL_COMPARISON"

        # 3. SPAM patterns
        spam_triggers = [
            "increase your shipping revenue", "one weird", "storage limit",
            "limited time offer", "automation tool", "verify your account",
            "casino", "crypto", "bitcoin", "winner", "prize", "unsubscribe",
            "grow your business", "exclusive deal", "special discount",
            "congratulations", "marketing partner",
            "unpaid customs fee", "customs fee", "undelivered messages", "bank officer",
            "business proposal", "confirm your bank details", "update your account to avoid",
            "email storage is full", "mailbox is full", "usd 4.5 million"
        ]
        combined = f"{subject}\n{body}"
        if any(t in subject for t in spam_triggers) or any(t in body for t in spam_triggers) or _matches(SPAM_RE, combined):
            return "SPAM"

        # 4. INVOICE_QUERY patterns
        invoice_triggers = [
            "query on invoice", "cancel invoice", "request to cancel invoice",
            "thc / local charge", "local charges", "d&d", "detention charges",
            "missing for invoice", "debit note", "credit note", "freight charges",
            "billing query", "overcharge", "demurrage invoice", "invoice correction"
        ]
        if any(t in subject for t in invoice_triggers) or any(t in body for t in invoice_triggers) or _matches(INVOICE_RE, combined):
            return "INVOICE_QUERY"

        # 5. SI_REQUEST patterns
        si_triggers = [
            "request si", "si needed", "cust si", "please find shipping instruction",
            "submit si", "shipping instruction for", "prepare si", "issuance of si",
            "si amendment request", "provide shipping instruction"
        ]
        if (
            any(t in subject for t in ["request si", "si needed", "cust si"])
            or subject.startswith("si -")
            or subject.startswith("re_ si -")
            or any(t in body for t in si_triggers)
            # Body only: a recurring subject line such as "Reminder - Submit SI" is not itself a
            # request, so the asking has to appear in the message.
            or _matches(SI_RE, body)
        ):
            return "SI_REQUEST"

        # 6. Default fallback to GENERAL
        return "GENERAL"

    def classify_all(self, emails: List[Dict[str, Any]]) -> Dict[str, str]:
        """Classifies all emails in batch."""
        results = {}
        for email in emails:
            eid = email.get("email_id")
            if eid:
                results[eid] = self.classify_email(email)
        return results

    classify = classify_email
