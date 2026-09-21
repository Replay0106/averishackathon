"""
sdoc_reconciler.py — Stage 3 Semantic Normalizer & Deterministic 7-Field Reconciler for NavisAI SDOC Pipeline.

Evaluates:
  1. shipper
  2. consignee
  3. notify_party (resolves "SAME AS CONSIGNEE")
  4. port_of_loading
  5. port_of_discharge
  6. container_count
  7. gross_weight_kg

Escalates to NEEDS_REVIEW for:
  - wrong_doc_type (commercial invoice, packing list, COO disguised as BL/SI)
  - missing_attachment (fewer than 2 attachments or missing required file)
  - unreadable (corrupt file, unparseable PDF stream, blurry scan)
  - missing_value (essential field is N/A, _______, or blank)

Produces output compliant with sample_submission.json:
  {
    "category": "...",
    "status": "OK" | "MISMATCH" | "NEEDS_REVIEW",
    "review_reason": null | "wrong_doc_type" | "missing_attachment" | "unreadable" | "missing_value",
    "has_defect": bool,
    "defect_fields": ["..."]
  }
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sdoc_extractor import ExtractedDocFields
from sdoc_loader import AttachmentData


def normalize_text(s: Optional[str]) -> str:
    if not s:
        return ""
    # Strip punctuation and collapse whitespace
    s = re.sub(r"[^\w\s]", " ", s.upper())
    return " ".join(s.split())


def normalize_entity(s: Optional[str]) -> str:
    if not s:
        return ""
    clean = normalize_text(s)
    # Strip common legal suffixes (including period-separated variants like L.L.C., P.T.E. L.T.D.)
    legal_suffixes = [
        r"\bSDN\s*BHD\b", r"\bBHD\b", r"\bP\s*T\s*E\s*L\s*T\s*D\b", r"\bPTE\s*LTD\b", r"\bL\s*T\s*D\b", r"\bLTD\b",
        r"\bLIMITED\b", r"\bL\s*L\s*C\b", r"\bLLC\b", r"\bFZ\s*L\s*L\s*C\b", r"\bFZ\s*LLC\b", r"\bINC\b",
        r"\bCO\s*LTD\b", r"\bCOMPANY\b", r"\bCORP\b", r"\bCORPORATION\b",
        r"\bGMBH\b", r"\bS\s*A\b", r"\bB\s*V\b", r"\bPVT\b"
    ]
    for suf in legal_suffixes:
        clean = re.sub(suf, " ", clean)
    return " ".join(clean.split())


def normalize_port(s: Optional[str]) -> str:
    if not s:
        return ""
    clean = normalize_text(s)
    # Port synonyms mapping
    synonyms = {
        "PORT KELANG": "PORT KLANG",
        "KELANG": "PORT KLANG",
        "KLANG": "PORT KLANG",
        "MYPKG": "PORT KLANG",
        "TANJUNG PELEPAS": "TANJUNG PELEPAS",
        "PTP": "TANJUNG PELEPAS",
        "MYTPP": "TANJUNG PELEPAS",
        "JAWAHARLAL NEHRU": "NHAVA SHEVA",
        "JNPT": "NHAVA SHEVA",
        "INNSA": "NHAVA SHEVA"
    }
    for k, v in synonyms.items():
        if k in clean:
            clean = clean.replace(k, v)
    return clean


def extract_city(port_str: str) -> str:
    # Extracts primary city before comma or code
    first = port_str.split(",")[0].strip()
    return re.sub(r"\([A-Z0-9]+\)", "", first).strip()


class DocumentReconciler:
    def reconcile(
        self,
        email_id: Any,
        category: Any = "BL_COMPARISON",
        si_att: Optional[AttachmentData] = None,
        bl_att: Optional[AttachmentData] = None,
        si_fields: Optional[ExtractedDocFields] = None,
        bl_fields: Optional[ExtractedDocFields] = None,
        draft_request: bool = False,
    ) -> Dict[str, Any]:
        """Reconciles an email case and returns the submission entry."""
        # Convenience: support reconcile(si_fields, bl_fields)
        if isinstance(email_id, ExtractedDocFields):
            si_fields = email_id
            bl_fields = category if isinstance(category, ExtractedDocFields) else bl_fields
            email_id = "reconcile_test"
            category = "BL_COMPARISON"
            if si_att is None and si_fields:
                si_att = AttachmentData(path="", filename="si_doc", extension=".pdf", detected_doc_type=si_fields.doc_type)
            if bl_att is None and bl_fields:
                bl_att = AttachmentData(path="", filename="bl_doc", extension=".pdf", detected_doc_type=bl_fields.doc_type)

        # 1. Non-comparison categories
        if category != "BL_COMPARISON":
            return {
                "category": category,
                "status": "OK",
                "review_reason": None,
                "has_defect": False,
                "defect_fields": []
            }

        # 1b. Request to send the draft BL: nothing to compare yet, and nothing is missing
        if draft_request and si_att is None and bl_att is None:
            return {
                "category": "BL_COMPARISON",
                "status": "OK",
                "review_reason": None,
                "has_defect": False,
                "defect_fields": []
            }

        # 2. Check missing attachments
        if si_att is None or bl_att is None:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_attachment",
                "has_defect": False,
                "defect_fields": []
            }

        # 3. Check unreadable/corrupt files
        if (
            si_att.is_corrupted
            or bl_att.is_corrupted
            or (si_fields and not si_fields.is_legible)
            or (bl_fields and not bl_fields.is_legible)
        ):
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "unreadable",
                "has_defect": False,
                "defect_fields": []
            }

        # 4. Check wrong document type (disguised invoice, packing list, COO)
        wrong_types = ("INVOICE", "PACKING_LIST", "COO")
        if si_att.detected_doc_type in wrong_types or bl_att.detected_doc_type in wrong_types:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "wrong_doc_type",
                "has_defect": False,
                "defect_fields": []
            }

        # 5. Check missing values or placeholders
        if si_fields is None or bl_fields is None:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_value",
                "has_defect": False,
                "defect_fields": []
            }

        if si_fields.has_missing_placeholder or bl_fields.has_missing_placeholder:
            return {
                "category": "BL_COMPARISON",
                "status": "NEEDS_REVIEW",
                "review_reason": "missing_value",
                "has_defect": False,
                "defect_fields": []
            }

        # Verify all 7 fields have a non-None value in both SI and BL
        required_attrs = [
            "shipper", "consignee", "notify_party",
            "port_of_loading", "port_of_discharge",
            "container_count", "gross_weight_kg"
        ]
        for attr in required_attrs:
            if getattr(si_fields, attr) is None or getattr(bl_fields, attr) is None:
                return {
                    "category": "BL_COMPARISON",
                    "status": "NEEDS_REVIEW",
                    "review_reason": "missing_value",
                    "has_defect": False,
                    "defect_fields": []
                }

        # 6. Compare the 7 fields
        defect_fields = []

        # (1) Shipper
        if normalize_entity(si_fields.shipper) != normalize_entity(bl_fields.shipper):
            defect_fields.append("shipper")

        # (2) Consignee
        if normalize_entity(si_fields.consignee) != normalize_entity(bl_fields.consignee):
            defect_fields.append("consignee")

        # (3) Notify Party (resolving SAME AS CONSIGNEE)
        si_notify = si_fields.notify_party
        bl_notify = bl_fields.notify_party
        if normalize_text(si_notify) in ("SAME AS CONSIGNEE", "SAME AS ABOVE", "AS ABOVE"):
            si_notify = si_fields.consignee
        if normalize_text(bl_notify) in ("SAME AS CONSIGNEE", "SAME AS ABOVE", "AS ABOVE"):
            bl_notify = bl_fields.consignee

        if normalize_entity(si_notify) != normalize_entity(bl_notify):
            defect_fields.append("notify_party")

        # (4) Port of Loading
        si_pol = normalize_port(si_fields.port_of_loading)
        bl_pol = normalize_port(bl_fields.port_of_loading)
        if si_pol != bl_pol and not (si_pol and bl_pol and (si_pol in bl_pol or bl_pol in si_pol)):
            if extract_city(si_pol) != extract_city(bl_pol):
                defect_fields.append("port_of_loading")

        # (5) Port of Discharge
        si_pod = normalize_port(si_fields.port_of_discharge)
        bl_pod = normalize_port(bl_fields.port_of_discharge)
        if si_pod != bl_pod and not (si_pod and bl_pod and (si_pod in bl_pod or bl_pod in si_pod)):
            if extract_city(si_pod) != extract_city(bl_pod):
                defect_fields.append("port_of_discharge")

        # (6) Container Count
        if si_fields.container_count != bl_fields.container_count:
            defect_fields.append("container_count")

        # (7) Gross Weight (KG)
        if abs(si_fields.gross_weight_kg - bl_fields.gross_weight_kg) > 1.0:
            defect_fields.append("gross_weight_kg")

        if defect_fields:
            return {
                "category": "BL_COMPARISON",
                "status": "MISMATCH",
                "review_reason": None,
                "has_defect": True,
                "defect_fields": defect_fields
            }
        else:
            return {
                "category": "BL_COMPARISON",
                "status": "OK",
                "review_reason": None,
                "has_defect": False,
                "defect_fields": []
            }
