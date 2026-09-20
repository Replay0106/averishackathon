"""
NavisAI | Financial & Demurrage Risk Engine (sdoc_risk.py)
---------------------------------------------------------
Quantifies financial exposure, demurrage penalties, and statutory
non-compliance risks (ICC UCP 600, IMO SOLAS VGM) for shipping discrepancies.
"""

from typing import Dict, Any, List, Optional

DAILY_DEMURRAGE_RATE_USD = 250.0  # Industry standard average per container/day
AVG_PORT_DWELL_DAYS = 3.0         # Typical customs hold delay for documentary disputes
LC_TRANSACTION_VALUE_USD = 500000.0  # Estimated benchmark Letter of Credit value

def compute_shipment_risk(
    email_id: str,
    record: Dict[str, Any],
    extracted_data: Optional[Dict[str, Any]] = None,
    raw_text: str = ""
) -> Dict[str, Any]:
    """
    Computes financial risk exposure and statutory non-compliance severity.
    
    Returns:
        Dict containing:
            - risk_level: CRITICAL | HIGH | MEDIUM | LOW | NEGLIGIBLE
            - total_exposure_usd: float
            - demurrage_exposure_usd: float
            - bank_presentation_risk_usd: float
            - statutory_violations: List[Dict[str, str]]
            - recommendation: str
    """
    status = record.get("status", "OK")
    defects = record.get("defect_fields", [])
    review_reason = record.get("review_reason")
    
    # Resolve container count
    container_count = 1
    if extracted_data and "container_count" in extracted_data:
        try:
            container_count = max(1, int(extracted_data.get("container_count") or 1))
        except (ValueError, TypeError):
            container_count = 1
            
    statutory_violations = []
    bank_risk_usd = 0.0
    demurrage_usd = 0.0
    
    if status == "OK":
        return {
            "risk_level": "NEGLIGIBLE",
            "total_exposure_usd": 0.0,
            "demurrage_exposure_usd": 0.0,
            "bank_presentation_risk_usd": 0.0,
            "statutory_violations": [],
            "recommendation": "Fast-track for immediate carrier manifest filing and LC presentation."
        }
        
    if status == "NEEDS_REVIEW":
        # Unreadable, wrong doc type, or missing values
        demurrage_usd = container_count * DAILY_DEMURRAGE_RATE_USD * AVG_PORT_DWELL_DAYS
        if review_reason in ("missing_attachment", "wrong_doc_type"):
            statutory_violations.append({
                "framework": "ICC UCP 600 Art. 14(a)",
                "clause": "Examination of Documents",
                "detail": f"Mandatory shipping documents absent or replaced by non-negotiable files ({review_reason}). Bank will refuse presentation.",
                "severity": "CRITICAL"
            })
            bank_risk_usd = LC_TRANSACTION_VALUE_USD
            risk_level = "CRITICAL"
        elif review_reason == "unreadable":
            statutory_violations.append({
                "framework": "ISBP 745 Section A",
                "clause": "General Principles / Legibility",
                "detail": "Corrupt or illegible document prevents verification of title. Port clearance halted.",
                "severity": "HIGH"
            })
            risk_level = "HIGH"
        else: # missing_value
            statutory_violations.append({
                "framework": "ICC UCP 600 Art. 14(d)",
                "clause": "Data Inconsistency / Incomplete Manifest",
                "detail": "Essential title fields contain missing or placeholder values (N/A, TBA).",
                "severity": "HIGH"
            })
            risk_level = "HIGH"
            
        return {
            "risk_level": risk_level,
            "total_exposure_usd": demurrage_usd + (500.0 if bank_risk_usd > 0 else 0.0),
            "demurrage_exposure_usd": demurrage_usd,
            "bank_presentation_risk_usd": bank_risk_usd,
            "statutory_violations": statutory_violations,
            "recommendation": f"Immediate Human Intervention required: {review_reason}. Do not tender to ocean carrier."
        }

    # Status is MISMATCH
    demurrage_usd = container_count * DAILY_DEMURRAGE_RATE_USD * AVG_PORT_DWELL_DAYS
    has_title_defect = any(f in defects for f in ("consignee", "shipper", "notify_party"))
    has_weight_defect = "gross_weight_kg" in defects
    has_container_defect = "container_count" in defects
    
    if has_title_defect:
        statutory_violations.append({
            "framework": "ICC UCP 600 Art. 14(d) & 14(f)",
            "clause": "Documentary Conformity & Negotiability",
            "detail": "Shipper or Consignee entity discrepancy violates Letter of Credit terms. Issuing bank will issue formal Notice of Refusal.",
            "severity": "CRITICAL"
        })
        bank_risk_usd = LC_TRANSACTION_VALUE_USD
        
    if has_weight_defect:
        statutory_violations.append({
            "framework": "IMO SOLAS Chapter VI, Reg 2 (VGM)",
            "clause": "Verified Gross Mass Compliance",
            "detail": "Gross weight disparity exceeds maritime safety tolerance. Terminal master may refuse vessel loading.",
            "severity": "REGULATORY"
        })
        
    if has_container_defect:
        statutory_violations.append({
            "framework": "Customs Manifest 24-Hour Rule",
            "clause": "Container Count Discrepancy",
            "detail": "Container tally mismatch will trigger destination customs inspection and container physical holds.",
            "severity": "HIGH"
        })
        
    if any(f in defects for f in ("port_of_loading", "port_of_discharge")):
        statutory_violations.append({
            "framework": "Maritime Port Clearance",
            "clause": "Port Routing Discrepancy",
            "detail": "Load/discharge port discrepancy will cause misrouted cargo and emergency transshipment penalties.",
            "severity": "HIGH"
        })
        
    # Classify overall risk level
    if has_title_defect:
        risk_level = "CRITICAL"
    elif has_weight_defect or has_container_defect:
        risk_level = "HIGH"
    else:
        risk_level = "MEDIUM"
        
    # Amendment fee penalty ($150 - $450)
    amendment_penalty_usd = 250.0
    total_exposure = demurrage_usd + amendment_penalty_usd
    
    return {
        "risk_level": risk_level,
        "total_exposure_usd": total_exposure,
        "demurrage_exposure_usd": demurrage_usd,
        "bank_presentation_risk_usd": bank_risk_usd,
        "statutory_violations": statutory_violations,
        "recommendation": "Issue Carrier Amendment Notice immediately before SI cut-off to prevent demurrage."
    }
