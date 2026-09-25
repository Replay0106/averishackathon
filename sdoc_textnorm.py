"""
sdoc_textnorm.py — clean-up that runs before the rule-based readers, so damaged or unusual layouts reach the same
patterns as clean ones.

Documents: carriage-return line endings, HTML exports, fullwidth punctuation, `=` / tab / CSV separators,
horizontal tables, OCR line numbers, checkboxes and markdown, page breaks between a label and its value, cover
sheets in front of the document, misspelled or OCR-damaged labels, and labels that only look like a field
("Shipper Contact:"). Only labels are ever corrected, never values, and a misspelled label is accepted only for
a field the document does not already state under its proper label.

Emails: quoted headers and signatures are ignored, broken and spaced-out words are joined, common shorthand is
expanded and typos in a few key words are corrected.
"""
from __future__ import annotations

import csv
import html
import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Optional, Tuple


def osa_distance(a: str, b: str, limit: int = 3) -> int:
    """Edit distance counting a swap of two neighbouring letters as one edit (optimal string alignment)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: List[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def _typo_limit(word: str) -> int:
    return 1 if len(word) < 10 else 2


# ---------------------------------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------------------------------

# Canonical label -> label keys (lower case, letters only). The canonical labels are the ones the extractor's
# patterns already read.
FIELD_LABELS: Dict[str, Tuple[str, ...]] = {
    "Shipper": ("shipper", "exporter"),
    "Consignee": ("consignee", "totheorderof"),
    "Notify Party": ("notifyparty", "partytonotify", "notify"),
    "Port of Loading": ("portofloading", "loadport", "pol", "portofload", "loadingport"),
    "Port of Discharge": ("portofdischarge", "dischargeport", "pod", "portofdisch"),
    "Total Containers": ("totalcontainers", "containercount", "noofcontainers", "numberofcontainers", "containers",
                         "nocntrs", "noofcntrs", "cntrs", "totalcntrs", "ctrs"),
    "Gross Weight": ("grossweight", "totalgrossweight", "grosswt", "gw", "gwt"),
}
_KEY_TO_LABEL = {k: lab for lab, keys in FIELD_LABELS.items() for k in keys}

# Words that turn a field label into something else: "Shipper Contact", "Consignee Reference", "Gross Weight Tolerance".
QUALIFIERS = {"contact", "contacts", "reference", "ref", "code", "tolerance", "email", "mail", "phone", "tel",
              "telephone", "fax", "address", "id", "vat", "tax", "account", "person", "name", "signature"}

# Real words that are one letter away from a label and must never be read as it.
NOT_LABELS = {"shipped", "shipping", "notified", "consigned", "portal", "pool", "pods",
              "containerno", "containernos", "containernumber", "containerid"}

TITLE_RE = re.compile(r"^\s*(?:draft\s+)?(?:bill\s+of\s+lading|sea\s*waybill|shipping\s+instructions?)\b", re.IGNORECASE)
COVER_RE = re.compile(r"\b(?:cover|transmittal|sample|template|specimen|unfilled|not\s+shipment\s+data)\b", re.IGNORECASE)
PAGE_LINE_RE = re.compile(r"^\s*(?:[-=_*\s]*page\s*break[-=_*\s]*|.{0,40}\bpage\s+\d+\s*(?:of|/)\s*\d+\b.{0,40})$", re.IGNORECASE)
PREFIX_RE = re.compile(r"^\s*(?:L\d{2,4}\s+|\[[ xX✓✔]?\]\s*|[☐☑☒•▪◦]\s*|#{1,6}\s+)")
NUMBERING_RE = re.compile(r"^(\s*\d{1,2}[.)]\s*)")
HTML_RE = re.compile(r"<\s*(?:p|br|div|tr|td|th|li|h[1-6]|body|html|table)\b", re.IGNORECASE)
UNIT_RE = re.compile(r"\b(KGS?|MTS?|LBS?|TONNES?)\b", re.IGNORECASE)


def label_key(label: str) -> str:
    s = unicodedata.normalize("NFKC", label).lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    # OCR puts digits for letters inside words: P0rt, L0ading, Tota1
    s = re.sub(r"(?<=[a-z])0|0(?=[a-z])", "o", s)
    s = re.sub(r"(?<=[a-z])1|1(?=[a-z])", "l", s)
    s = re.sub(r"(?<=[a-z])5|5(?=[a-z])", "s", s)
    return re.sub(r"[^a-z]", "", s)


@lru_cache(maxsize=4096)
def match_label(label: str, fuzzy: bool = False) -> Tuple[Optional[str], bool]:
    """(canonical label or None, is_qualified). `is_qualified` means the label is a field name followed by a
    qualifier such as "Contact" or "Tolerance", so its value is not the field."""
    words = [label_key(w) for w in re.findall(r"[^\W_]+", re.sub(r"\([^)]*\)", " ", unicodedata.normalize("NFKC", label)))]
    words = [w for w in words if w]
    key = "".join(words)
    if not key:
        return None, False
    if key in _KEY_TO_LABEL:
        return _KEY_TO_LABEL[key], False
    for k in range(len(words) - 1, 0, -1):
        head = "".join(words[:k])
        rest = [w for w in words[k:] if w != "s"]  # "Shipper's Reference"
        if head in _KEY_TO_LABEL and rest and all(w in QUALIFIERS for w in rest):
            return _KEY_TO_LABEL[head], True
    if fuzzy and key not in NOT_LABELS:
        best = None
        for alias, lab in _KEY_TO_LABEL.items():
            if len(alias) >= 5 and osa_distance(key, alias, _typo_limit(alias)) <= _typo_limit(alias):
                if best and best != lab:
                    return None, False  # close to two different fields: do not guess
                best = lab
        if best:
            return best, False
    return None, False


def _split_label_value(line: str) -> Optional[Tuple[str, str, str]]:
    """(numbering prefix, label, value) for a `label: value`, `label = value` or `label<TAB>value` line."""
    m = NUMBERING_RE.match(line)
    prefix = m.group(1) if m else ""
    body = line[len(prefix):]
    for sep in (r":", r"\s*=\s*", r"\t+"):
        mm = re.match(r"^([^:=\t\n]{1,45}?)\s*" + sep + r"\s*(.*)$", body)
        if mm and re.search(r"[A-Za-z]", mm.group(1)):
            return prefix, mm.group(1).strip(), mm.group(2).strip()
    return None


def _table_cells(line: str) -> Optional[List[str]]:
    if " | " in line or "\t" in line:
        cells = re.split(r"\s+\|\s+|\t+", line.strip().strip("|"))
    elif "," in line:
        try:
            cells = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            return None
    else:
        return None
    cells = [c.strip() for c in cells]
    return cells if len(cells) >= 2 else None


def strip_cover(text: str) -> str:
    """Blanks a cover sheet or unfilled sample printed in front of the document title, keeping line numbers."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if TITLE_RE.match(line):
            if i and COVER_RE.search("\n".join(lines[:i])):
                return "\n".join([""] * i + lines[i:])
            return text
    return text


def normalize_document_text(text: str) -> str:
    """Rewrites a document so every field it states appears as a plain `Label: value` line."""
    if not text:
        return text
    t = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    if HTML_RE.search(t):
        t = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " | ", t, flags=re.IGNORECASE)
        t = re.sub(r"<\s*/?\s*(?:p|br|div|tr|li|h[1-6]|table|tbody|thead|body|html)\b[^>]*>", "\n", t, flags=re.IGNORECASE)
        t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = strip_cover(t)

    lines = []
    for line in t.split("\n"):
        line = PREFIX_RE.sub("", line).replace("**", "")
        lines.append("" if PAGE_LINE_RE.match(line) else line)

    # Horizontal tables: a header row of field labels followed by a row of values.
    out: List[str] = []
    i = 0
    while i < len(lines):
        head = _table_cells(lines[i])
        vals = _table_cells(lines[i + 1]) if head and i + 1 < len(lines) else None
        if head and vals and len(head) == len(vals) >= 3 and sum(match_label(h)[0] is not None for h in head) >= 3:
            out.extend(f"{h}: {v}" for h, v in zip(head, vals))
            i += 2
            continue
        # Two-column CSV / table rows: "Shipper","Meridian Ltd"
        if head and len(head) == 2 and not _split_label_value(lines[i]) and match_label(head[0], fuzzy=True)[0]:
            out.append(f"{head[0]}: {head[1]}")
        else:
            out.append(lines[i])
        i += 1

    # Canonical labels. Exact labels first; a misspelled one only for a field that is not stated properly.
    parsed = [_split_label_value(l) for l in out]
    exact = {match_label(p[1])[0] for p in parsed if p and not match_label(p[1])[1]} - {None}
    fixed: List[str] = []
    for line, p in zip(out, parsed):
        if not p:
            fixed.append(line)
            continue
        prefix, label, value = p
        canon, qualified = match_label(label)
        if qualified:
            fixed.append(f"({label}) {value}")  # a contact / reference / tolerance line, not the field itself
            continue
        if canon is None:
            if len(exact) == len(FIELD_LABELS):
                fixed.append(line)  # every field is already stated under its proper label
                continue
            canon, _ = match_label(label, fuzzy=True)
            if canon is None or canon in exact:
                fixed.append(line)
                continue
            exact.add(canon)
        unit = UNIT_RE.search(" ".join(re.findall(r"\(([^)]*)\)", label)))
        if canon == "Gross Weight" and unit and value and not re.search(r"[A-Za-z]", value):
            value = f"{value} {unit.group(1)}"
        fixed.append(f"{prefix}{canon}: {value}")
    return "\n".join(fixed)


# ---------------------------------------------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------------------------------------------

SHORTHAND = {
    "pls": "please", "plz": "please", "pse": "please", "snd": "send", "shpg": "shipping", "shp": "shipping",
    "instr": "instructions", "instrs": "instructions", "instrn": "instructions", "inv": "invoice", "invs": "invoices",
    "amt": "amount", "chk": "check", "ur": "your", "u": "you", "accnt": "account", "acct": "account", "b4": "before",
    "thx": "thanks", "pwd": "password", "pw": "password", "docs": "documents", "req": "request", "reqd": "required",
}
# Key words whose misspellings are corrected. Only words of 7+ letters, so short everyday words are never changed.
KEY_WORDS = ("shipping", "instruction", "instructions", "invoice", "invoices", "password", "account", "suspended",
             "confirm", "immediately", "correct", "duplicate", "credentials", "billing", "incorrect", "explain",
             "submit", "prepare", "discrepancy", "verify")
SEGMENT_WORDS = sorted(set(KEY_WORDS) | {"please", "send", "the", "your", "our", "for", "and", "to", "submit",
                                          "prepare", "provide", "today", "asap", "now", "si", "draft", "bl", "this",
                                          "check", "cancel", "wrong", "amount", "claim", "prize", "details", "kindly", "we", "need",
                                          "request", "attached", "find", "is", "a", "an", "of", "by", "with"},
                       key=len, reverse=True)


def strip_quoted_metadata(body: str) -> str:
    """Drops what the sender did not write as the message: gateway headers, pasted ticket headers and signatures."""
    out = []
    for line in (body or "").splitlines():
        s = line.strip()
        if re.fullmatch(r"-{2,}\s*", s):
            break  # signature delimiter: the rest is footer
        if re.match(r"^x-[\w-]+\s*:", s, re.IGNORECASE):
            continue
        if re.match(r"^(?:imported|forwarded|archived|original|pasted)\b.{0,30}\b(?:header|metadata)\b", s, re.IGNORECASE):
            continue
        if re.match(r"^end\b.{0,20}\bmetadata\b", s, re.IGNORECASE):
            continue
        out.append(line)
    return "\n".join(out)


def _segment(token: str) -> Optional[List[str]]:
    """Splits a run-together word ("pleasesubmittheshippinginstructions") into known words, or None."""
    n = len(token)
    best: List[Optional[List[str]]] = [None] * (n + 1)
    best[0] = []
    for i in range(n):
        if best[i] is None:
            continue
        for w in SEGMENT_WORDS:
            if token.startswith(w, i) and (best[i + len(w)] is None or len(best[i]) + 1 < len(best[i + len(w)])):
                best[i + len(w)] = best[i] + [w]
    return best[n]


def _fix_word(word: str) -> str:
    low = word.lower()
    if low in SHORTHAND:
        return SHORTHAND[low]
    if len(low) >= 14 and low.isalpha():
        parts = _segment(low)
        if parts:
            return " ".join(parts)
        # Not every piece is known: still split around the key words that are ("...shippinginstructions")
        split = re.sub("(" + "|".join(sorted(KEY_WORDS, key=len, reverse=True)) + ")", r"  ", low).split()
        if len(split) > 1:
            return " ".join(split)
    if len(low) >= 6 and low.isalpha() and low not in KEY_WORDS:
        for k in KEY_WORDS:
            if len(k) >= 7 and osa_distance(low, k) <= _typo_limit(k):
                return k
    return word


def normalize_email_text(text: str) -> str:
    """A cleaned copy of an email's text for a second pass of the intent rules."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"(\w)-[ \t]*\n[ \t]*(\w)", r"\1\2", t)  # "in-\nvoice"
    t = re.sub(r"\s*\n\s*", " ", t)  # a phrase wrapped over two lines
    t = re.sub(r"\b(?:[A-Za-z] ){2,}[A-Za-z]\b", lambda m: m.group(0).replace(" ", ""), t)  # "p a s s w o r d"
    return re.sub(r"[A-Za-z0-9]+", lambda m: _fix_word(m.group(0)), t)
