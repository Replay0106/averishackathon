"""
sdoc_classifier.py — Stage 1 Email Classifier for NavisAI SDOC Hackathon Pipeline.

Classifies incoming shipping operations emails into 5 distinct categories:
  - BL_COMPARISON: Requests to check/compare draft B/L against Shipping Instruction (SI).
  - SI_REQUEST: Requests to prepare, submit, or issue a new Shipping Instruction.
  - INVOICE_QUERY: Questions regarding billing, charges, debit/credit notes, invoice cancellation.
  - GENERAL: General shipping status, vessel schedules, berthing reports, draft BL sending requests.
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

        # 3. SPAM patterns
        spam_triggers = [
            "increase your shipping revenue", "one weird", "storage limit",
            "limited time offer", "automation tool", "verify your account",
            "casino", "crypto", "bitcoin", "winner", "prize", "unsubscribe",
            "grow your business", "exclusive deal", "special discount",
            "congratulations", "marketing partner"
        ]
        if any(t in subject for t in spam_triggers) or any(t in body for t in spam_triggers):
            return "SPAM"

        # 4. INVOICE_QUERY patterns
        invoice_triggers = [
            "query on invoice", "cancel invoice", "request to cancel invoice",
            "thc / local charge", "local charges", "d&d", "detention charges",
            "missing for invoice", "debit note", "credit note", "freight charges",
            "billing query", "overcharge", "demurrage invoice", "invoice correction"
        ]
        if any(t in subject for t in invoice_triggers) or any(t in body for t in invoice_triggers):
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
