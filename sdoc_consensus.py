"""
NavisAI | Temporal Multi-Turn Consensus Engine (sdoc_consensus.py)
-----------------------------------------------------------------
Links related emails across asynchronous forwarder communication threads.
Tracks draft revision lineage (v1 -> v2 -> Final) and suppresses
superseded "zombie discrepancies" resolved by later incoming messages.
"""

import re
from typing import Dict, Any, List, Set, Tuple

def extract_thread_keys(email_text: str, subject: str = "") -> List[str]:
    """
    Extracts core business entity anchors from subject lines and email text.
    """
    combined = f"{subject}\n{email_text}"
    keys = []
    
    # 1. Booking References (e.g. BK-12345, BKG98231, MSCU123456)
    bkg_matches = re.findall(r"\b(?:BK|BKG|BOOKING)[\s\-\#\:]*([A-Z0-9]{5,15})\b", combined, re.IGNORECASE)
    for m in bkg_matches:
        keys.append(f"BKG:{m.upper()}")
        
    # 2. Bill of Lading numbers (e.g. BL-98213, MEDU98124)
    bl_matches = re.findall(r"\b(?:BL|B\/L|BOL)[\s\-\#\:]*([A-Z0-9]{6,16})\b", combined, re.IGNORECASE)
    for m in bl_matches:
        keys.append(f"BL:{m.upper()}")
        
    # 3. Vessel & Voyage anchors
    voy_match = re.search(r"(?:vessel|voyage)[\s\:]+([A-Z0-9\s]+?)(?:voy|\n|$)", combined, re.IGNORECASE)
    if voy_match:
        norm_vessel = re.sub(r"[^A-Z0-9]", "", voy_match.group(1).upper())
        if len(norm_vessel) >= 4:
            keys.append(f"VOY:{norm_vessel}")
            
    return list(set(keys))

def extract_version_indicator(subject: str, body: str) -> int:
    """
    Infers document revision version number from text signals.
    e.g. "Draft v2", "Rev 3", "Amended BL", "Second Draft"
    """
    text = f"{subject} {body}".lower()
    
    # Explicit version numbers
    v_match = re.search(r"\b(?:v|ver|version|rev|revision)[\s\.\:]*([0-9]+)\b", text)
    if v_match:
        try:
            return int(v_match.group(1))
        except ValueError:
            pass
            
    if "final" in text:
        return 99
    if "revised" in text or "amended" in text or "updated" in text or "correction" in text:
        return 2
        
    return 1

def build_consensus_graph(
    inbox_emails: List[Dict[str, Any]],
    submission_records: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Builds the multi-turn thread consensus graph.
    Identifies related threads and marks superseded historical discrepancies.
    
    Returns:
        Dict mapping email_id to consensus metadata:
            - thread_id: str
            - revision_number: int
            - is_latest_revision: bool
            - is_superseded: bool
            - superseded_by: Optional[str]
            - consensus_summary: str
    """
    # 1. Cluster emails by shared thread keys
    key_to_emails: Dict[str, List[str]] = {}
    email_to_keys: Dict[str, List[str]] = {}
    
    for email in inbox_emails:
        eid = email.get("id", "")
        subj = email.get("subject", "")
        body = email.get("body", "")
        keys = extract_thread_keys(body, subj)
        email_to_keys[eid] = keys
        for k in keys:
            if k not in key_to_emails:
                key_to_emails[k] = []
            key_to_emails[k].append(eid)
            
    # Group connected components into threads
    email_to_thread: Dict[str, str] = {}
    threads: Dict[str, List[str]] = {}
    
    for email in inbox_emails:
        eid = email.get("id", "")
        if eid in email_to_thread:
            continue
            
        # Discover all connected emails
        visited = set()
        queue = [eid]
        while queue:
            curr = queue.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            for k in email_to_keys.get(curr, []):
                for neighbor in key_to_emails.get(k, []):
                    if neighbor not in visited:
                        queue.append(neighbor)
                        
        thread_id = f"TH-{eid}"
        threads[thread_id] = sorted(list(visited))
        for member in visited:
            email_to_thread[member] = thread_id

    # 2. Analyze chronological versions inside each thread
    consensus_results: Dict[str, Any] = {}
    
    for thread_id, members in threads.items():
        if len(members) == 1:
            # Standalone single email thread
            eid = members[0]
            consensus_results[eid] = {
                "thread_id": thread_id,
                "revision_number": 1,
                "is_latest_revision": True,
                "is_superseded": False,
                "superseded_by": None,
                "consensus_summary": "Single-turn transaction thread."
            }
            continue
            
        # Multi-email thread: determine revisions and discrepancy resolutions
        member_versions = []
        for eid in members:
            email_obj = next((e for e in inbox_emails if e.get("id") == eid), {})
            v = extract_version_indicator(email_obj.get("subject", ""), email_obj.get("body", ""))
            status = submission_records.get(eid, {}).get("status", "OK")
            member_versions.append((eid, v, status))
            
        # Sort chronologically by email ID / version
        member_versions.sort(key=lambda x: (x[1], x[0]))
        latest_eid, latest_v, latest_status = member_versions[-1]
        
        for eid, v, status in member_versions:
            is_latest = (eid == latest_eid)
            # If this earlier email was a MISMATCH, but a later revision exists in the thread
            # that is OK, then the earlier mismatch is superseded!
            is_superseded = False
            superseded_by = None
            if not is_latest and status == "MISMATCH" and latest_status == "OK":
                is_superseded = True
                superseded_by = latest_eid
                summary = f"Defect resolved in later revision {latest_eid} (Draft v{latest_v}). Zombie discrepancy suppressed."
            elif not is_latest:
                summary = f"Historical revision v{v} (Superceded by {latest_eid})."
            else:
                summary = f"Active consensus head (Draft v{v} in thread of {len(members)} messages)."
                
            consensus_results[eid] = {
                "thread_id": thread_id,
                "revision_number": v,
                "is_latest_revision": is_latest,
                "is_superseded": is_superseded,
                "superseded_by": superseded_by,
                "thread_member_count": len(members),
                "consensus_summary": summary
            }
            
    return consensus_results
