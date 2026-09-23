"""Ask Navis: answers questions from the pipeline's own results. No language model is involved.

A question is matched against a small set of intents (one shipment, a follow-up on the previous shipment, or a
question about the whole dataset). Every number and value in a reply is read from the verification results, the
amendment outbox and the reviewer decisions; nothing is generated. A question that matches no intent gets a help
reply listing what can be asked, never a guess.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

FIELD_LABELS = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "container_count": "Container Count",
    "gross_weight_kg": "Gross Weight",
}
FIELD_WORDS = {
    "notify_party": r"notify(?:\s*party)?",
    "port_of_loading": r"(?:port\s*of\s*loading|loading\s*port|\bpol\b)",
    "port_of_discharge": r"(?:port\s*of\s*discharge|discharge\s*port|\bpod\b)",
    "container_count": r"containers?(?:\s*(?:count|number))?",
    "gross_weight_kg": r"(?:gross\s*)?weight",
    "consignee": r"consignee",
    "shipper": r"shipper",
}
REASON_LABELS = {
    "missing_attachment": "Missing attachment",
    "wrong_doc_type": "Wrong document type",
    "unreadable": "Unreadable file",
    "missing_value": "Missing field value",
}
REVIEW_ADVICE = {
    "missing_attachment": "Request the missing document from the sender.",
    "wrong_doc_type": "Ask the sender to resend the correct SI and draft BL.",
    "unreadable": "Request a legible re-upload or a clean PDF.",
    "missing_value": "Confirm the missing field with the shipper before verification.",
}
DECIDED = ("CONFIRM_AI_RESULT", "OVERRIDE_RESULT", "AMENDMENT_CONFIRMED")
LIST_LIMIT = 8


@dataclass
class Source:
    """What the copilot may read. Supplied by the API so this module has no server dependencies."""

    dataset: str
    email_ids: Callable[[], List[str]]
    shipment_to_email: Callable[[str], Optional[str]]
    detail: Callable[[str], Dict[str, Any]]      # one email's full result, with amendment, resolution and case
    rows: Callable[[], List[Dict[str, Any]]]     # summary rows for every email, as /api/emails returns them


# ---------------------------------------------------------------- helpers
def bucket(row: Dict[str, Any]) -> Optional[str]:
    """The Cases tab a flagged comparison lives on (same rule as the web app); None when it is not a case."""
    if row.get("category") != "BL_COMPARISON" or row.get("status") == "OK":
        return None
    res = row.get("resolution") or {}
    if res.get("action") in DECIDED:
        return "resolved"
    if row.get("amendment") or res.get("action") == "AMENDMENT_DISPATCHED":
        return "awaiting"
    return "needs"


def _has(q: str, pattern: str) -> bool:
    return re.search(pattern, q, re.I) is not None


def _plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _local_date(iso: str, tz_minutes: int) -> Optional[str]:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (t + timedelta(minutes=tz_minutes)).date().isoformat()


def _item(row: Dict[str, Any], note: str) -> Dict[str, Any]:
    return {"shipment": row["shipment"], "email": row["id"], "note": note}


def _case_note(row: Dict[str, Any]) -> str:
    if row.get("status") == "MISMATCH":
        return "Mismatch: " + ", ".join(FIELD_LABELS.get(f, f) for f in row.get("defect_fields", [])) if row.get("defect_fields") else "Mismatch"
    return REASON_LABELS.get(row.get("review_reason") or "", "Needs review")


def _leaders(rows_: List[Dict[str, Any]], one: str = "has", many: str = "share") -> str:
    """'A has' or 'A and B share' for everything tied with the first entry of a breakdown sorted by count."""
    names = [r["label"] for r in rows_ if r["count"] == rows_[0]["count"]]
    if len(names) == 1:
        return f"{names[0]} {one}"
    shown = names[:3] + ([f"{len(names) - 3} more"] if len(names) > 3 else [])
    return f"{', '.join(shown[:-1])} and {shown[-1]} {many}"


def _find_email(q: str, src: Source) -> Optional[str]:
    """A shipment number (SHP-2048, SHP 2048, shipment 2048) or an email id named in the question."""
    m = re.search(r"\bSHP[\s#-]*(\d{3,6})\b", q, re.I) or re.search(r"\bshipment\s*#?\s*(\d{3,6})\b", q, re.I)
    if m:
        return src.shipment_to_email(f"SHP-{m.group(1)}")
    ids = src.email_ids()
    idset = set(ids)
    for tok in re.findall(r"[A-Za-z0-9_\-.]+", q):
        if tok in idset:
            return tok
    m = re.search(r"\bemail[\s_#-]*(\d+)\b", q, re.I)
    if m:
        n = int(m.group(1))
        for eid in ids:
            digits = re.findall(r"\d+", eid)
            if eid.startswith("email") and digits and int(digits[-1]) == n:
                return eid
    return None


FOLLOW_UP = r"\b(it|its|it's|that one|this one|the same one|that shipment|this shipment|the shipment)\b"
DATASET_WORDS = (r"\b(how many|all|every|count|number of|most|top|summary|overview|shipments|cases|mismatches|amendments|emails|"
                 r"senders|shippers|carriers|fields)\b")
SENT_WORDS = (r"\bamendments?\b|\boutbox\b|\bdispatched\b|\bwe\s+(?:have\s+|had\s+)?sen[dt]\b|\bdid\s+we\s+send\b|"
              r"\bhave\s+we\s+sent\b|\bsent\s+(?:out|back|to\s+(?:the\s+)?senders?)\b")
NOT_SENT = r"\b(?:not|never)\s+(?:been\s+|be\s+)?sent\b|\bcan(?:no|')t\s+be\s+sent\b|\bunsent\b|\bheld\b"


# ---------------------------------------------------------------- one shipment
def _handling(r: Dict[str, Any]) -> Optional[str]:
    res = r.get("resolution") or {}
    a = r.get("amendment")
    if res.get("action") == "OVERRIDE_RESULT":
        return "Resolved: a reviewer overrode the result."
    if res.get("action") == "AMENDMENT_CONFIRMED":
        return "Resolved: the corrected document was received."
    if res.get("action") == "CONFIRM_AI_RESULT":
        return "Resolved: a reviewer confirmed the result."
    if a:
        how = "automatically" if a.get("auto") else "by a reviewer"
        return f"Amendment sent {how} to {a['recipient']} on {a['at'][:16].replace('T', ' ')} UTC; waiting for the corrected document."
    case = r.get("case") or {}
    if case and not case.get("can_send", True):
        return f"Waiting on a person. It cannot be sent to the sender: {case.get('block_reason')}."
    return "Waiting on a person (Cases, Needs a person)."


def shipment_answer(src: Source, eid: str) -> Dict[str, Any]:
    r = src.detail(eid)
    base = {"shipment": r["shipment"], "email": eid, "handling": None}
    if r["category"] != "BL_COMPARISON":
        return {**base, "kind": "not_comparison", "category": r["category"],
                "message": f"{r['shipment']} is classified {r['category']}; no SI/BL comparison applies."}
    handling = _handling(r)
    if r["status"] == "NEEDS_REVIEW":
        missing = [c["label"] for c in r.get("comparison", []) if c.get("missing")]
        return {**base, "kind": "review", "reason": r["review_reason"], "confidence": r["confidence"], "missing": missing,
                "recommendation": REVIEW_ADVICE.get(r["review_reason"] or "", "Escalate to a human reviewer."), "handling": handling}
    bad = [c for c in r.get("comparison", []) if not c["match"] and not c["missing"]]
    if not bad:
        return {**base, "kind": "clear", "message": "No mismatch detected. All seven fields match between the SI and the draft BL."}
    rec = ("Wait for the corrected draft BL from the sender." if r.get("amendment")
           else "Send the amendment to the sender (Cases) to request a corrected draft BL.")
    return {**base, "kind": "mismatch", "issues": bad, "evidence": r["attachments"], "recommendation": rec, "handling": handling}


# ---------------------------------------------------------------- whole dataset
def _reply(title: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"kind": "dataset", "title": title, "message": message, **extra}


def _list_reply(title: str, rows: List[Dict[str, Any]], note: Callable[[Dict[str, Any]], str], empty: str,
                link: Optional[str] = None, lead: Optional[str] = None) -> Dict[str, Any]:
    if not rows:
        return _reply(title, empty, total=0)
    msg = lead or f"{_plural(len(rows), 'shipment')}."
    more = max(0, len(rows) - LIST_LIMIT)
    return _reply(title, msg, total=len(rows), items=[_item(r, note(r)) for r in rows[:LIST_LIMIT]], more=more, link=link)


def _overview(rows: List[Dict[str, Any]], src: Source) -> Dict[str, Any]:
    comps = [r for r in rows if r["category"] == "BL_COMPARISON"]
    buckets = Counter(bucket(r) for r in rows)
    stats = [
        {"label": "Emails", "value": len(rows)},
        {"label": "SI/BL checks", "value": len(comps)},
        {"label": "Clear", "value": sum(r["status"] == "OK" for r in comps), "tone": "ok"},
        {"label": "Mismatches", "value": sum(r["status"] == "MISMATCH" for r in comps), "tone": "bad"},
        {"label": "Need review", "value": sum(r["status"] == "NEEDS_REVIEW" for r in comps), "tone": "warn"},
        {"label": "Amendments sent", "value": sum(1 for r in rows if r.get("amendment"))},
        {"label": "Needs a person", "value": buckets.get("needs", 0)},
        {"label": "Resolved", "value": buckets.get("resolved", 0)},
    ]
    return _reply("Overview", f"{src.dataset}: {_plural(len(rows), 'email')}, {_plural(len(comps), 'SI/BL check')}.", stats=stats, link="cases")


STATUS_INTENTS = [
    # (pattern, title, row filter, note, empty message, link)
    (r"needs? a person|waiting on (?:a|the) (?:person|reviewer)|\bto ?do\b|pending|open cases|unhandled|" + NOT_SENT,
     "Needs a person", lambda r: bucket(r) == "needs", _case_note, "No case is waiting on a person.", "cases"),
    (r"awaiting|waiting (?:for|on) (?:a |the )?(?:reply|sender|correct)|no reply",
     "Awaiting the sender's reply", lambda r: bucket(r) == "awaiting",
     lambda r: f"Sent to {(r.get('amendment') or {}).get('recipient', 'the sender')}", "No amendment is waiting for a reply.", "cases"),
    (r"resolved|closed|done|finished",
     "Resolved cases", lambda r: bucket(r) == "resolved", lambda r: (r.get("resolution") or {}).get("action", "").replace("_", " ").lower(),
     "No case has been resolved yet.", "cases"),
    (r"(?:need|needs|needing|require|requires|requiring|for|in|under)\s+(?:a\s+)?(?:human\s+)?review|human review|reviews?\b|escalat",
     "Cases needing review", lambda r: r["category"] == "BL_COMPARISON" and r["status"] == "NEEDS_REVIEW", _case_note,
     "No comparison needs review.", "cases"),
    (r"mismatch|discrepanc|flagged|errors?|wrong|incorrect|problem|issue",
     "Mismatches", lambda r: r["category"] == "BL_COMPARISON" and r["status"] == "MISMATCH", _case_note, "No mismatches.", "cases"),
    (r"\bclear\b|\bok\b|\bpass(?:ed)?\b|\bclean\b|\bmatch(?:es|ed)?\b|releas",
     "Clear shipments", lambda r: r["category"] == "BL_COMPARISON" and r["status"] == "OK", lambda r: "All seven fields match",
     "No shipment is clear yet.", "compliance"),
]


def _field_in(q: str) -> Optional[str]:
    for key, pat in FIELD_WORDS.items():
        if _has(q, pat):
            return key
    return None


def dataset_answer(q: str, rows: List[Dict[str, Any]], src: Source, tz_minutes: int = 0,
                   now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    flagged = [r for r in rows if bucket(r)]
    counting = _has(q, r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b")

    # Amendments that were sent, optionally only today's.
    if _has(q, SENT_WORDS) and not _has(q, NOT_SENT):
        sent = [r for r in rows if r.get("amendment")]
        when = ""
        if _has(q, r"\btoday\b"):
            today = ((now or datetime.now(timezone.utc)) + timedelta(minutes=tz_minutes)).date().isoformat()
            sent = [r for r in sent if _local_date(r["amendment"]["at"], tz_minutes) == today]
            when = " today"
        sent.sort(key=lambda r: r["amendment"]["at"], reverse=True)
        auto = sum(1 for r in sent if r["amendment"].get("auto"))
        lead = f"{_plural(len(sent), 'amendment')} sent{when}: {auto} automatically, {len(sent) - auto} by a reviewer."
        return _list_reply(f"Amendments sent{when}", sent,
                           lambda r: f"To {r['amendment']['recipient']} · {r['amendment']['at'][:16].replace('T', ' ')} UTC",
                           f"No amendment has been sent{when}.", "cases", lead)

    # Who or what causes the most problems.
    ranking = _has(q, r"\b(most|top|worst|rank|ranking|by|per|breakdown|common|frequent|often)\b")
    if ranking and _has(q, r"\b(shippers?|senders?|customers?|clients?|who)\b") and not _has(q, r"shipper\s+(?:field|mismatch|name)"):
        counts = Counter(r.get("sender") or "unknown sender" for r in flagged)
        rows_ = [{"label": s, "count": n, "note": f"{sum(1 for r in flagged if r.get('sender') == s and r['status'] == 'MISMATCH')} mismatch · "
                  f"{sum(1 for r in flagged if r.get('sender') == s and r['status'] == 'NEEDS_REVIEW')} review"}
                 for s, n in counts.most_common(LIST_LIMIT)]
        if not rows_:
            return _reply("Cases by sender", "No flagged cases, so no sender stands out.")
        return _reply("Cases by sender", f"{_leaders(rows_)} the most flagged cases ({rows_[0]['count']}). Senders are the email addresses the documents came from.",
                      breakdown=rows_, total=len(flagged))
    if _has(q, r"\bcarriers?\b"):
        counts = Counter((r.get("meta") or {}).get("carrier") or "Unassigned" for r in flagged)
        rows_ = [{"label": c, "count": n} for c, n in counts.most_common(LIST_LIMIT)]
        if not rows_:
            return _reply("Cases by carrier", "No flagged cases.")
        return _reply("Cases by carrier", f"{_leaders(rows_)} the most flagged cases ({rows_[0]['count']}). The carrier is read from the booking prefix.",
                      breakdown=rows_, total=len(flagged))
    if _has(q, r"\bwhy\b|\breasons?\b") and _has(q, r"review|person|human|escalat"):
        reviews = [r for r in rows if r["category"] == "BL_COMPARISON" and r["status"] == "NEEDS_REVIEW"]
        counts = Counter(r.get("review_reason") or "unknown" for r in reviews)
        rows_ = [{"label": REASON_LABELS.get(k, k), "count": n, "note": REVIEW_ADVICE.get(k, "")} for k, n in counts.most_common()]
        if not rows_:
            return _reply("Why cases need review", "No comparison needs review.")
        return _reply("Why cases need review", f"{_plural(len(reviews), 'case')} need review. Most common: {rows_[0]['label'].lower()} ({rows_[0]['count']}).",
                      breakdown=rows_, total=len(reviews), link="cases")

    # A specific field: which shipments disagree on it, or which field disagrees most often.
    field = _field_in(q)
    mism = [r for r in rows if r["category"] == "BL_COMPARISON" and r["status"] == "MISMATCH"]
    if ranking and _has(q, r"\bfields?\b|mismatch|discrepanc|errors?") and not field:
        counts = Counter(f for r in mism for f in r.get("defect_fields", []))
        rows_ = [{"label": FIELD_LABELS.get(k, k), "count": n} for k, n in counts.most_common()]
        if not rows_:
            return _reply("Mismatches by field", "No mismatches.")
        return _reply("Mismatches by field", f"{_leaders(rows_, 'is', 'are')} the most common mismatch ({_plural(rows_[0]['count'], 'shipment')}). A shipment can differ on several fields.",
                      breakdown=rows_, total=len(mism), link="analytics")
    if field and _has(q, r"mismatch|discrepanc|differ|wrong|error|incorrect|issue|problem|which|list|show|how many"):
        hit = [r for r in mism if field in r.get("defect_fields", [])]
        label = FIELD_LABELS[field]
        if counting:
            return _reply(f"{label} mismatches", f"{_plural(len(hit), 'shipment')} with a {label.lower()} mismatch, out of {_plural(len(mism), 'mismatch', 'mismatches')}.", total=len(hit),
                          items=[_item(r, _case_note(r)) for r in hit[:LIST_LIMIT]], more=max(0, len(hit) - LIST_LIMIT), link="cases")
        return _list_reply(f"{label} mismatches", hit, _case_note, f"No shipment has a {label.lower()} mismatch.", "cases")

    # A status or Cases tab.
    for pattern, title, keep, note, empty, link in STATUS_INTENTS:
        if _has(q, pattern):
            hit = [r for r in rows if keep(r)]
            if counting:
                return _reply(title, f"{title}: {len(hit)}.", total=len(hit),
                              items=[_item(r, note(r)) for r in hit[:LIST_LIMIT]], more=max(0, len(hit) - LIST_LIMIT), link=link)
            return _list_reply(title, hit, note, empty, link)

    if _has(q, r"summary|overview|status|how (?:are|is) (?:we|it|things)|dashboard|today|how many (?:emails|documents|shipments|files)|\bstats\b"):
        return _overview(rows, src)
    return None


# ---------------------------------------------------------------- entry point
def help_reply(message: Optional[str] = None, rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What can be asked, with a shipment and a field taken from the current dataset."""
    rows = rows or []
    mism = next((r for r in rows if r.get("category") == "BL_COMPARISON" and r.get("status") == "MISMATCH"), None)
    field = next((f for r in rows for f in r.get("defect_fields", [])), None)
    examples = [f"Why is {mism['shipment']} flagged?"] if mism else []
    examples += [
        "How many mismatches are there?",
        "Which cases need a person?",
        "What amendments did we send today?",
        "Which sender has the most errors?",
        "What is the most common mismatch field?",
    ]
    if field:
        examples.append(f"Which shipments have a {FIELD_LABELS.get(field, field).lower()} mismatch?")
    return {"kind": "help", "message": message or "I answer from the verification results only. Try one of these:", "examples": examples}


def answer(q: str, src: Source, context: Optional[str] = None, tz_minutes: int = 0, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Answer one question. `context` is the email id of the shipment the previous answer was about."""
    q = (q or "").strip()
    if not q:
        return help_reply(rows=src.rows())
    eid = _find_email(q, src)
    if eid:
        return shipment_answer(src, eid)
    if re.search(r"\bSHP[\s#-]*\d", q, re.I) or re.search(r"\bshipment\s*#?\s*\d{3,6}\b", q, re.I):
        return help_reply(f"I couldn't find that shipment in {src.dataset}. Check the number on the Inbox page.")
    if context and context in set(src.email_ids()) and _has(q, FOLLOW_UP) and not _has(q, DATASET_WORDS):
        return shipment_answer(src, context)
    rows = src.rows()
    return dataset_answer(q, rows, src, tz_minutes, now) or {
        **help_reply("I can't answer that from the verification results. Try one of these:", rows), "unrecognised": True}


# ---------------------------------------------------------------- free-form questions (optional Gemini translation)
# When the rules above do not recognise a question, Gemini may translate it into one of the queries below. Gemini only
# picks the query; the answer is still computed by answer() from the verification results, and only the question text
# is sent to Gemini (no shipment data). The reply says what the question was interpreted as.
STATUS_QUESTIONS = {
    "needs_person": ("Which cases need a person?", "How many cases need a person?"),
    "awaiting": ("Which cases are awaiting a reply?", "How many cases are awaiting a reply?"),
    "resolved": ("Which cases are resolved?", "How many cases are resolved?"),
    "review": ("Which cases need review?", "How many cases need review?"),
    "mismatch": ("Which shipments have mismatches?", "How many mismatches are there?"),
    "clear": ("Which shipments are clear?", "How many shipments are clear?"),
}
INTENTS = ["shipment", "follow_up", "overview", "list", "count", "sent", "rank_senders", "rank_carriers", "rank_fields",
           "review_reasons", "field", "unsupported"]

TRANSLATE_PROMPT = """You translate a question from an operator at a shipping documentation desk into a structured query.
The system checks Shipping Instructions (SI) against draft Bills of Lading (BL) on seven fields and sends amendment
requests to senders. Do NOT answer the question; only classify it. Reply with one JSON object:
{{"intent": ..., "status": ..., "field": ..., "shipment": ..., "count": true|false, "today": true|false}}

intent is one of:
- "shipment": about one named shipment (put its number, e.g. "SHP-2048", or an email id in "shipment")
- "follow_up": refers to the shipment discussed just before without naming it ("it", "that one", "this shipment");
  use this whenever a previous shipment exists and the question points back to it
- "overview": general status or totals of the whole inbox
- "list" or "count": shipments or cases in one state (use "count" when asking how many); status is one of
  "needs_person" (waiting for a reviewer), "awaiting" (amendment sent, waiting for the sender), "resolved",
  "review" (could not be checked automatically), "mismatch" (SI and BL differ), "clear" (all fields match)
- "sent": amendment requests that were sent (today=true if only today)
- "rank_senders": which senders cause the most problems
- "rank_carriers": which carriers have the most problems
- "rank_fields": which field differs most often
- "review_reasons": why cases need review
- "field": shipments whose SI and BL differ on one field; field is one of shipper, consignee, notify_party,
  port_of_loading, port_of_discharge, container_count, gross_weight_kg (count=true when asking how many)
- "unsupported": anything else (weather, prices, predictions, actions such as sending or deleting)

Previous shipment in this conversation: {previous}
Question: {question}"""


def canonical_question(query: Dict[str, Any], has_previous: bool = True) -> Optional[str]:
    """The fixed question for a structured query, or None when the query is unsupported or malformed (a follow-up
    needs a previous shipment, or the rules would read "why is it flagged?" as a question about all flagged cases)."""
    intent = query.get("intent")
    status, field = query.get("status"), query.get("field")
    if intent == "shipment":
        sid = str(query.get("shipment") or "").strip()
        m = re.fullmatch(r"(?:SHP[\s#-]*)?(\d{3,6})", sid, re.I)
        if m:
            return f"Why is SHP-{m.group(1)} flagged?"
        return f"Tell me about {sid}" if re.fullmatch(r"[A-Za-z0-9_\-.]{1,80}", sid) else None
    if intent == "follow_up":
        return "Why is it flagged?" if has_previous else None
    if intent == "overview":
        return "Give me an overview"
    if intent in ("list", "count") and status in STATUS_QUESTIONS:
        return STATUS_QUESTIONS[status][intent == "count"]
    if intent == "sent":
        return "What amendments did we send today?" if query.get("today") else "What amendments did we send?"
    if intent == "rank_senders":
        return "Which sender has the most errors?"
    if intent == "rank_carriers":
        return "Which carrier has the most cases?"
    if intent == "rank_fields":
        return "What is the most common mismatch field?"
    if intent == "review_reasons":
        return "Why do cases need review?"
    if intent == "field" and field in FIELD_LABELS:
        label = FIELD_LABELS[field].lower()
        return f"How many shipments have a {label} mismatch?" if query.get("count") else f"Which shipments have a {label} mismatch?"
    return None


Translator = Callable[[str, bool], Tuple[Optional[Dict[str, Any]], Dict[str, Any]]]


def gemini_translator(question: str, has_previous: bool) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    import sdoc_llm

    data, rec = sdoc_llm.generate_json(TRANSLATE_PROMPT.format(previous="yes" if has_previous else "none", question=question[:500]))
    return (data if isinstance(data, dict) else None), rec


_translations: Dict[Tuple[str, bool], Tuple[Optional[str], str]] = {}


def answer_with_llm(q: str, src: Source, context: Optional[str] = None, tz_minutes: int = 0, now: Optional[datetime] = None,
                    translator: Optional[Translator] = None) -> Dict[str, Any]:
    """answer(), and for a question the rules do not recognise, a Gemini translation into a supported query."""
    reply = answer(q, src, context, tz_minutes, now)
    if not reply.get("unrecognised"):
        return reply
    has_previous = bool(context and context in set(src.email_ids()))
    key = (" ".join(q.lower().split()), has_previous)
    if key not in _translations:
        query, rec = (translator or gemini_translator)(q, has_previous)
        if not rec.get("ok"):
            return reply  # Gemini unavailable: keep the rule-based help reply
        _translations[key] = (canonical_question(query or {}, has_previous), rec.get("model", ""))
    canonical, model = _translations[key]
    if canonical is None:
        return {**reply, "message": "That is outside what I can answer from the verification results. Try one of these:", "via": model}
    translated = answer(canonical, src, context, tz_minutes, now)
    if translated.get("unrecognised"):
        return reply
    return {**translated, "interpreted_as": canonical, "via": model}
