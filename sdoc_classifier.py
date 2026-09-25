"""
sdoc_classifier.py — Stage 1 Email Classifier for NavisAI SDOC Hackathon Pipeline.

Classifies incoming shipping operations emails into 5 distinct categories:
  - BL_COMPARISON: Requests to check/compare draft B/L against Shipping Instruction (SI).
  - SI_REQUEST: Requests to prepare, submit, or issue a new Shipping Instruction.
  - INVOICE_QUERY: Questions regarding billing, charges, debit/credit notes, invoice cancellation.
  - GENERAL: General shipping status, vessel schedules, berthing reports, operational notices.
  - SPAM: Unsolicited sales, marketing promotions, phishing, irrelevant messages.

Classification is rule-based. Quoted gateway headers, pasted ticket headers and signatures are ignored. If no rule
matches, the rules run again on a cleaned copy of the text (typos, shorthand, broken and spaced-out words; see
sdoc_textnorm.py). If still nothing matches and a Gemini key is configured, Gemini classifies the email instead of it
defaulting to GENERAL (NAVIS_LLM_CLASSIFIER=0 turns this off).
"""

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from sdoc_textnorm import normalize_email_text, strip_quoted_metadata

logger = logging.getLogger(__name__)

CATEGORIES = ("BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM")

# Shared by the optional fallback below and by evaluate.py, so the app and the evaluation ask the same question.
LLM_CATEGORY_PROMPT = """You label emails received by a shipping documentation desk. Give each email exactly one category:
- BL_COMPARISON: asks for a draft Bill of Lading to be checked or compared against the Shipping Instruction (or sends those documents for checking).
- SI_REQUEST: asks the recipient to send or submit a Shipping Instruction.
- INVOICE_QUERY: about an invoice, charges, billing or payment.
- SPAM: unsolicited, promotional, phishing or scam messages.
- GENERAL: any other business correspondence.
Return only a JSON object that maps every email id to its category."""


def llm_email_text(email: Dict[str, Any]) -> str:
    atts = ", ".join(Path(a).name for a in email.get("attachments", [])) or "none"
    return (f"id: {email.get('email_id') or email.get('id')}\nsubject: {email.get('subject', '')}\nattachments: {atts}\n"
            f"body: {(email.get('body') or '')[:1500]}")


def llm_fallback_enabled() -> bool:
    """On whenever a Gemini key is configured, unless NAVIS_LLM_CLASSIFIER=0."""
    if os.environ.get("NAVIS_LLM_CLASSIFIER", "1") == "0":
        return False
    import sdoc_llm

    return sdoc_llm.available()


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


def reply_labels(data: Any) -> Dict[str, str]:
    """Email id -> category from a Gemini reply, whichever JSON shape it chose: {"e1": "SPAM"}, a list of
    {"id": "e1", "category": "SPAM"}, or either of those under a key such as "emails"."""
    if isinstance(data, dict):
        nested = [v for v in data.values() if isinstance(v, (list, dict))]
        if nested and not any(isinstance(v, str) for v in data.values()):
            out: Dict[str, str] = {}
            for v in nested:
                out.update(reply_labels(v))
            return out
        return {str(k).strip(): v for k, v in data.items() if isinstance(v, str)}
    if isinstance(data, list):
        out = {}
        for item in data:
            if isinstance(item, dict):
                eid = item.get("id") or item.get("email_id")
                label = item.get("category") or item.get("label")
                if eid and isinstance(label, str):
                    out[str(eid).strip()] = label
        return out
    return {}


def is_draft_request(email: Dict[str, Any]) -> bool:
    """True when the email asks for a draft BL to be sent for checking, with no documents attached."""
    return not email.get("attachments") and bool(DRAFT_REQUEST.search(email.get("body", "")))


class LabelCache:
    """Gemini's answers by email fingerprint, kept across restarts in .cache/llm_categories.json (and in Supabase when
    it is configured, where the disk does not survive a redeploy). Only real answers are stored, never failures."""

    KV_KEY = "llm_categories"

    def __init__(self, path: Optional[str] = None):
        from sdoc_store import _under_test_runner

        # Tests keep answers in memory only (unless they pass a path), so a mocked reply never reaches the real cache.
        if path:
            self.path: Optional[Path] = Path(path)
        elif _under_test_runner():
            self.path = None
        else:
            self.path = Path(os.environ.get("NAVIS_LLM_CACHE") or Path(__file__).resolve().parent / ".cache" / "llm_categories.json")
        self._store = path is None
        self._mem: Optional[Dict[str, str]] = None
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, str]:
        if self._mem is None:
            self._mem = {}
            if self.path is not None:
                if self._store:
                    try:
                        import sdoc_store

                        self._mem.update(sdoc_store.kv_get(self.KV_KEY) or {})
                    except Exception:  # noqa: BLE001 — the cache is an optimisation
                        pass
                try:
                    self._mem.update(json.loads(self.path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
        return self._mem

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._load().get(key)

    def put(self, labels: Dict[str, str]) -> None:
        if not labels:
            return
        with self._lock:
            mem = self._load()
            mem.update(labels)
            if self.path is None:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(mem), encoding="utf-8")
                tmp.replace(self.path)
                if self._store:
                    import sdoc_store

                    sdoc_store.kv_set(self.KV_KEY, mem)
            except Exception:  # noqa: BLE001
                pass


class EmailClassifier:
    # Gemini may not start a document check: BL_COMPARISON comes only from attached documents or an explicit request
    # to compare or send a draft BL (on the hackathon inbox it labelled berthing reports as BL checks).
    LLM_CATEGORIES = ("SI_REQUEST", "INVOICE_QUERY", "SPAM", "GENERAL")
    BATCH = 30  # emails per Gemini request

    def __init__(self, api_key: Optional[str] = None, use_llm: Optional[bool] = None, cache: Optional[LabelCache] = None):
        """use_llm=False keeps classification to the rules alone (evaluate.py measures the rules on their own);
        None follows llm_fallback_enabled(). The API does not call classify_email: it uses rule_category and
        cached_llm_label, and queues the rest for Gemini in the background (api/main.py, _GeminiBackfill)."""
        self.use_llm = use_llm
        self.cache = cache or LabelCache()
        self.calls: List[Dict[str, Any]] = []

    def _llm_enabled(self) -> bool:
        return self.use_llm if self.use_llm is not None else llm_fallback_enabled()

    @staticmethod
    def fingerprint(email: Dict[str, Any]) -> str:
        text = LLM_CATEGORY_PROMPT + "\n" + llm_email_text({**email, "email_id": "e"})
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def cached_llm_label(self, email: Dict[str, Any]) -> Optional[str]:
        return self.cache.get(self.fingerprint(email))

    def llm_labels(self, emails: List[Dict[str, Any]], wait_s: float = 0) -> Dict[int, str]:
        """Gemini's category for each email (by position), from the cache or asked in batches of BATCH. An email
        whose request failed, or whose answer is not one of LLM_CATEGORIES, is left out."""
        import sdoc_llm

        out: Dict[int, str] = {}
        todo = []
        for i, e in enumerate(emails):
            key = self.fingerprint(e)
            hit = self.cache.get(key)
            if hit:
                out[i] = hit
            else:
                todo.append((i, e, key))
        for start in range(0, len(todo), self.BATCH):
            left = todo[start:start + self.BATCH]
            fresh: Dict[str, str] = {}
            refused = 0
            for _round in range(3):  # a reply can leave emails out or use another JSON shape: ask again for those
                text = "\n\n".join(llm_email_text({**e, "email_id": f"e{n}"}) for n, (_, e, _) in enumerate(left, 1))
                data, rec = sdoc_llm.generate_json(LLM_CATEGORY_PROMPT + "\n\n" + text, wait_s=wait_s)
                self.calls.append({**rec, "emails": len(left)})
                if not rec.get("ok"):
                    break
                replies = reply_labels(data)
                missing = []
                for n, item in enumerate(left, 1):
                    i, _, key = item
                    label = str(replies.get(f"e{n}", "")).strip().upper().replace(" ", "_")
                    if label == "BL_COMPARISON":
                        label, refused = "GENERAL", refused + 1  # a final answer: Gemini may not start a document check
                    if label in self.LLM_CATEGORIES:
                        out[i] = fresh[key] = label
                    else:
                        missing.append(item)
                if missing:
                    logger.warning("Gemini answered %d of %d emails (reply shape %s); asking again for the rest",
                                   len(left) - len(missing), len(left), type(data).__name__)
                left = missing
                if not left:
                    break
            self.cache.put(fresh)
            if refused:
                logger.info("Gemini labelled %d email(s) BL_COMPARISON; kept as GENERAL", refused)
            if not rec.get("ok"):
                break  # every key is resting or denied: the rest would fail the same way
        return out

    def rule_category(self, email: Dict[str, Any]) -> str:
        """The rules alone: the raw text first, then a cleaned copy if nothing matched."""
        subject = email.get("subject", "")
        body = strip_quoted_metadata(email.get("body", ""))
        label = self._rule_category(email, subject.lower(), body.lower())
        if label == "GENERAL":
            # Second pass on a cleaned copy: "pls snd shpg instr", "invocie", "p a s s w o r d", "in- / voice"
            label = self._rule_category(email, normalize_email_text(subject).lower(), normalize_email_text(body).lower())
        return label

    def classify_email(self, email: Dict[str, Any]) -> str:
        """Rules first; an email no rule matches is given to Gemini when a key is configured, otherwise GENERAL."""
        label = self.rule_category(email)
        if label != "GENERAL" or not self._llm_enabled():
            return label
        hit = self.cached_llm_label(email)
        if hit:
            return hit
        return self.llm_labels([email]).get(0, "GENERAL")

    def classify_many(self, emails: List[Dict[str, Any]], wait_s: float = 120) -> List[str]:
        """Classifies a whole batch: rules for every email, then one Gemini request per BATCH unmatched emails,
        waiting for a rate-limited key when needed (for batch jobs, not request paths)."""
        labels = [self.rule_category(e) for e in emails]
        if self._llm_enabled():
            idx = [i for i, label in enumerate(labels) if label == "GENERAL"]
            got = self.llm_labels([emails[i] for i in idx], wait_s=wait_s)
            for j, i in enumerate(idx):
                labels[i] = got.get(j, "GENERAL")
        return labels

    def _rule_category(self, email: Dict[str, Any], subject: str, body: str) -> str:
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
