"""
sdoc_extractor.py — Stage 2 7-Field Extractor & Vision Guardrail for NavisAI SDOC Hackathon Pipeline.

Extracts:
  1. shipper: Registered legal entity
  2. consignee: Consignee name or "TO ORDER"
  3. notify_party: Notify party name or "SAME AS CONSIGNEE"
  4. port_of_loading: Port of Loading (POL)
  5. port_of_discharge: Port of Discharge (POD)
  6. container_count: Integer container count
  7. gross_weight_kg: Float gross weight normalized to KG

Supports:
  - High-speed deterministic regex extraction from structured text/tables
  - Detection of placeholder values ("N/A", "_______", "TBA") -> triggers missing_value
  - Native Gemini Flash Multimodal PDF Vision for scanned/image-only PDFs
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sdoc_loader import AttachmentData

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None


PLACEHOLDER_PATTERN = re.compile(r"^(?:N/?A|TBA|TBD|NONE|UNKNOWN|__{2,}|\?+|\s*)$", re.IGNORECASE)


@dataclass
class ExtractedDocFields:
    doc_type: str = "UNKNOWN"
    shipper: Optional[str] = None
    consignee: Optional[str] = None
    notify_party: Optional[str] = None
    port_of_loading: Optional[str] = None
    port_of_discharge: Optional[str] = None
    container_count: Optional[int] = None
    gross_weight_kg: Optional[float] = None
    
    is_scanned: bool = False
    is_legible: bool = True
    has_missing_placeholder: bool = False
    missing_field_name: Optional[str] = None
    raw_text: str = ""


class FieldExtractor:
    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-3.6-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None and genai is not None and self.api_key:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def extract(self, att: AttachmentData) -> ExtractedDocFields:
        """Extracts the 7 fields from an attachment, using text parsing or Gemini Vision."""
        if att.is_corrupted:
            return ExtractedDocFields(
                doc_type=att.detected_doc_type,
                is_legible=False,
                is_scanned=att.is_scanned
            )

        if att.is_scanned:
            return self._extract_via_vision(att)

        return self._extract_via_text(att)

    def _find_field(self, pattern: str, text: str) -> Optional[str]:
        # Try colon / dash / pipe separated first
        m = re.search(r"(?:^|\n)\s*(?:" + pattern + r")[^\n:]*[:\-|]\s*([^\n;]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback to newline separated (label on line N, value on line N+1)
        m2 = re.search(r"(?:^|\n)\s*(?:" + pattern + r")[^\n:]*\n\s*([^\n;]+)", text, re.IGNORECASE)
        if m2:
            return m2.group(1).strip()
        return None

    def _extract_via_text(self, att: AttachmentData) -> ExtractedDocFields:
        text = att.text
        doc_type = att.detected_doc_type
        fields = ExtractedDocFields(doc_type=doc_type, raw_text=text)

        # 1. Shipper
        raw_val = self._find_field(r"Shipper|Exporter", text)
        if raw_val:
            val = self._clean_entity(raw_val)
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "shipper"
            else:
                fields.shipper = val

        # 2. Consignee
        raw_val = self._find_field(r"Consignee|To the Order of|CONSIGNEE", text)
        if raw_val:
            val = self._clean_entity(raw_val)
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "consignee"
            else:
                fields.consignee = val

        # 3. Notify Party
        raw_val = self._find_field(r"Notify\s*Party|Notify|NOTIFY", text)
        if raw_val:
            val = self._clean_entity(raw_val)
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "notify_party"
            else:
                fields.notify_party = val

        # 4. Port of Loading (POL)
        raw_val = self._find_field(r"Port of Loading|Load Port|POL|Port of Load", text)
        if raw_val:
            val = raw_val.strip()
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "port_of_loading"
            else:
                fields.port_of_loading = val

        # 5. Port of Discharge (POD)
        raw_val = self._find_field(r"Port of Discharge|Discharge Port|POD|Port of Disch", text)
        if raw_val:
            val = raw_val.strip()
            if self._is_placeholder(val):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "port_of_discharge"
            else:
                fields.port_of_discharge = val

        # 6. Container Count
        raw_val = self._find_field(r"Total Containers|Container Count|No\.?\s*of Containers|Containers?|Packages?", text)
        if raw_val:
            raw_c = raw_val.strip()
            if self._is_placeholder(raw_c):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "container_count"
            else:
                parsed_cnt = self._parse_container_count(raw_c)
                if parsed_cnt is not None:
                    fields.container_count = parsed_cnt
                else:
                    fields.has_missing_placeholder = True
                    fields.missing_field_name = "container_count"

        # 7. Gross Weight
        raw_val = self._find_field(r"Total Gross Weight|Gross Weight|Gross Wt|GROSS WT|GROSS WEIGHT", text)
        if raw_val:
            raw_w = raw_val.strip()
            if self._is_placeholder(raw_w):
                fields.has_missing_placeholder = True
                fields.missing_field_name = "gross_weight_kg"
            else:
                parsed_wt = self._parse_weight(raw_w)
                if parsed_wt is not None:
                    fields.gross_weight_kg = parsed_wt
                else:
                    fields.has_missing_placeholder = True
                    fields.missing_field_name = "gross_weight_kg"

        return fields

    def _extract_via_vision(self, att: AttachmentData) -> ExtractedDocFields:
        if self.client is None or not att.raw_bytes:
            return ExtractedDocFields(doc_type=att.detected_doc_type, is_scanned=True, is_legible=False)

        prompt = """You are an expert shipping document auditor.
Analyze this scanned shipping document image. Extract the following 7 shipment fields precisely as printed:
1. shipper: company name
2. consignee: company name or "TO ORDER"
3. notify_party: notify party name or "SAME AS CONSIGNEE"
4. port_of_loading: port name
5. port_of_discharge: port name
6. container_count: integer count of containers (e.g. 6)
7. gross_weight_kg: total gross weight as a float in kilograms (e.g. 128544.0)

Also determine if the document is legible (is_legible: true/false), its doc_type ("SI" or "BL"), and if any essential field is marked "N/A" or blank.

Return ONLY a JSON object:
{
  "doc_type": "SI" or "BL",
  "is_legible": true,
  "has_missing_placeholder": false,
  "missing_field_name": null,
  "shipper": "...",
  "consignee": "...",
  "notify_party": "...",
  "port_of_loading": "...",
  "port_of_discharge": "...",
  "container_count": 0,
  "gross_weight_kg": 0.0
}"""

        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=att.raw_bytes, mime_type="application/pdf"),
                    prompt
                ],
                config={"response_mime_type": "application/json"}
            )
            data = json.loads(resp.text)
            return ExtractedDocFields(
                doc_type=data.get("doc_type", att.detected_doc_type),
                shipper=data.get("shipper"),
                consignee=data.get("consignee"),
                notify_party=data.get("notify_party"),
                port_of_loading=data.get("port_of_loading"),
                port_of_discharge=data.get("port_of_discharge"),
                container_count=data.get("container_count"),
                gross_weight_kg=float(data["gross_weight_kg"]) if data.get("gross_weight_kg") is not None else None,
                is_scanned=True,
                is_legible=data.get("is_legible", True),
                has_missing_placeholder=data.get("has_missing_placeholder", False),
                missing_field_name=data.get("missing_field_name")
            )
        except Exception as e:
            return ExtractedDocFields(
                doc_type=att.detected_doc_type,
                is_scanned=True,
                is_legible=False
            )

    def _is_placeholder(self, s: Optional[str]) -> bool:
        if not s:
            return True
        clean = s.strip()
        upper = clean.upper()
        if not clean or upper in ("N/A", "NA", "TBA", "TBD", "NONE", "UNKNOWN"):
            return True
        if "N/A" in upper or "TBA" in upper or "TBD" in upper:
            return True
        if "__" in clean:
            return True
        if set(clean) <= {"_", "-", "?", "."} and len(clean) >= 2:
            return True
        return False

    def _clean_entity(self, raw: str) -> str:
        # Take the first line or strip subsequent address lines
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        if not lines:
            return ""
        first_line = lines[0]
        # If separated by pipe or semicolon with address
        if " | " in first_line:
            first_line = first_line.split(" | ")[0].strip()
        elif ";" in first_line and any(kw in first_line.upper() for kw in ("STREET", "ROAD", "LEVEL", "AVENUE", "BUILDING")):
            first_line = first_line.split(";")[0].strip()
        return first_line.strip()

    def _parse_container_count(self, raw: str) -> Optional[int]:
        raw = raw.strip()
        # Matches patterns like "6 x 40'HC", "10 x 20'FCL", "4x40", "6"
        m = re.search(r"(\d+)\s*(?:x|\*|\bcontainers?\b|\bunits?\b|\bfcl\b)", raw, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m2 = re.search(r"^\s*(\d+)\s*$", raw)
        if m2:
            return int(m2.group(1))
        # Container IDs list
        ids = re.findall(r"\b[A-Z]{4}\d{7}\b", raw)
        if ids:
            return len(ids)
        return None

    def _parse_weight(self, raw: str) -> Optional[float]:
        raw = raw.strip().upper()
        # Check MT / MTS vs KG / KGS
        is_mt = bool(re.search(r"\b(?:MT|MTS|METRIC\s*TONS?)\b", raw))
        is_lbs = bool(re.search(r"\b(?:LBS?|POUNDS?)\b", raw))

        m = re.search(r"([\d,]+(?:\.\d+)?)", raw)
        if not m:
            return None
        val_str = m.group(1).replace(",", "")
        try:
            val = float(val_str)
            if is_mt:
                return val * 1000.0
            if is_lbs:
                return val / 2.20462
            return val
        except ValueError:
            return None
