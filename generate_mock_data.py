"""Generate a realistic mock shipping-document test dataset for NavisAI.

Produces 4 PDFs in sample_data/ representing one palm-oil export shipment,
with an intentional container-number typo and weight typo on the Bill of
Lading so the reconciler's discrepancy flags trigger during a demo.
"""
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT_DIR = os.path.join(os.path.dirname(__file__), "sample_data")


def render_pdf(filename: str, title: str, lines: list[str]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, height - 25 * mm, title)
    c.setFont("Helvetica", 10)

    y = height - 40 * mm
    for line in lines:
        if line == "":
            y -= 4 * mm
            continue
        c.drawString(20 * mm, y, line)
        y -= 6 * mm

    c.showPage()
    c.save()
    print(f"Generated {path}")


def main() -> None:
    render_pdf(
        "commercial_invoice.pdf",
        "COMMERCIAL INVOICE",
        [
            "Invoice No: INV-2026-PK-0417",
            "Issue Date: 2026-08-18",
            "Issuer: Averis Global Trade Sdn Bhd",
            "",
            "Buyer: Rotterdam Green Energy Cooperative B.V.",
            "",
            "Product: Refined Bleached Deodorized (RBD) Palm Oil",
            "HS Code: 1511.90",
            "Quantity: 50 MT",
            "",
            "Container No: MSCU9021431",
            "",
            "Port of Loading (POL): Port Klang, Malaysia",
            "Port of Discharge (POD): Rotterdam, Netherlands",
            "Incoterms: CIF Rotterdam",
            "",
            "Total Amount: USD 120,000.00",
            "Currency: USD",
        ],
    )

    render_pdf(
        "packing_list.pdf",
        "PACKING LIST",
        [
            "Packing List No: PL-2026-PK-0417",
            "Issue Date: 2026-08-18",
            "Issuer: Averis Global Trade Sdn Bhd",
            "",
            "Container No: MSCU9021431",
            "",
            "Total Gross Weight: 52,400 KG",
            "Total Net Weight: 50,000 KG",
            "",
            "Port of Loading (POL): Port Klang, Malaysia",
            "Port of Discharge (POD): Rotterdam, Netherlands",
        ],
    )

    render_pdf(
        "bill_of_lading.pdf",
        "BILL OF LADING",
        [
            "B/L No: BL-MSC-2026-778812",
            "Issue Date: 2026-08-20",
            "Carrier: Mediterranean Shipping Company (MSC)",
            "",
            # Intentional typo: 431 -> 432
            "Container No: MSCU9021432",
            "",
            # Intentional typo: 52,400 -> 51,900
            "Gross Weight: 51,900 KG",
            "Net Weight: 50,000 KG",
            "",
            "Port of Loading (POL): Port Klang, Malaysia",
            "Port of Discharge (POD): Rotterdam, Netherlands",
            "Incoterms: CIF Rotterdam",
        ],
    )

    render_pdf(
        "letter_of_credit.pdf",
        "LETTER OF CREDIT",
        [
            "LC No: LC-2026-RGE-09",
            "Issuing Bank: ING Bank N.V., Rotterdam",
            "Advising Bank: Maybank Berhad, Kuala Lumpur",
            "",
            "Applicant: Rotterdam Green Energy Cooperative B.V.",
            "Beneficiary: Averis Global Trade Sdn Bhd",
            "",
            "Credit Amount: USD 120,000.00",
            "Tolerance: 5 percent",
            "",
            "Latest Shipment Date: 2026-08-30",
            "Expiry Date: 2026-09-15",
            "",
            "Port of Loading (POL): Port Klang, Malaysia",
            "Port of Discharge (POD): Rotterdam, Netherlands",
            "",
            "Documents Required: Commercial Invoice, Packing List, Full Set Clean",
            "On Board Bill of Lading, Certificate of Origin (MITI / Chamber of",
            "Commerce stamped, Form D), Insurance Policy.",
            "",
            "Partial Shipments: Not Allowed",
            "Transhipment: Not Allowed",
        ],
    )


if __name__ == "__main__":
    main()
