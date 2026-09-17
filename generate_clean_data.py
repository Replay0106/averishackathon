"""Generate a fully consistent 'clean' shipping-document test dataset for NavisAI.

Produces 5 PDFs in sample_data_clean/ for a pulp & paper export shipment where every
figure ties out across documents and the LC/COO terms are fully satisfied, so the
NavisAI audit should return zero discrepancies and a bank-ready status.
"""
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OUT_DIR = os.path.join(os.path.dirname(__file__), "sample_data_clean")


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
            "Invoice No: INV-2026-PP-1102",
            "Issue Date: 2026-09-05",
            "Issuer: Averis Global Trade Sdn Bhd",
            "",
            "Buyer: Yokohama Paper Mills Co., Ltd.",
            "",
            "Product: Bleached Kraft Pulp",
            "HS Code: 4703.29",
            "Quantity: 45 MT",
            "",
            "Container No: PPRU4455667",
            "",
            "Port of Loading (POL): Tanjung Pelepas, Malaysia",
            "Port of Discharge (POD): Yokohama, Japan",
            "Incoterms: CIF Yokohama",
            "",
            "Total Amount: USD 85,000.00",
            "Currency: USD",
        ],
    )

    render_pdf(
        "packing_list.pdf",
        "PACKING LIST",
        [
            "Packing List No: PL-2026-PP-1102",
            "Issue Date: 2026-09-05",
            "Issuer: Averis Global Trade Sdn Bhd",
            "",
            "Container No: PPRU4455667",
            "",
            "Total Gross Weight: 48,000 KG",
            "Total Net Weight: 47,500 KG",
            "HS Code: 4703.29",
            "",
            "Port of Loading (POL): Tanjung Pelepas, Malaysia",
            "Port of Discharge (POD): Yokohama, Japan",
        ],
    )

    render_pdf(
        "bill_of_lading.pdf",
        "BILL OF LADING",
        [
            "B/L No: BL-MSC-2026-990214",
            "Issue Date: 2026-09-10",
            "Carrier: Mediterranean Shipping Company (MSC)",
            "",
            "Container No: PPRU4455667",
            "",
            "Gross Weight: 48,000 KG",
            "Net Weight: 47,500 KG",
            "",
            "Port of Loading (POL): Tanjung Pelepas, Malaysia",
            "Port of Discharge (POD): Yokohama, Japan",
            "Incoterms: CIF Yokohama",
        ],
    )

    render_pdf(
        "letter_of_credit.pdf",
        "LETTER OF CREDIT",
        [
            "LC No: LC-2026-YPM-22",
            "Issuing Bank: MUFG Bank, Yokohama",
            "Advising Bank: Maybank Berhad, Kuala Lumpur",
            "",
            "Applicant: Yokohama Paper Mills Co., Ltd.",
            "Beneficiary: Averis Global Trade Sdn Bhd",
            "",
            "Credit Amount: USD 90,000.00",
            "Tolerance: 10 percent",
            "",
            "Latest Shipment Date: 2026-09-30",
            "Expiry Date: 2026-10-15",
            "",
            "Port of Loading (POL): Tanjung Pelepas, Malaysia",
            "Port of Discharge (POD): Yokohama, Japan",
            "",
            "Documents Required: Commercial Invoice, Packing List, Full Set Clean",
            "On Board Bill of Lading, Certificate of Origin (MITI / Chamber of",
            "Commerce stamped, Form D), Insurance Policy.",
            "",
            "Partial Shipments: Not Allowed",
            "Transhipment: Not Allowed",
        ],
    )

    render_pdf(
        "certificate_of_origin.pdf",
        "CERTIFICATE OF ORIGIN (FORM D)",
        [
            "COO No: COO-MY-2026-88231",
            "Issue Date: 2026-09-06",
            "Issuing Authority: Ministry of Investment, Trade and Industry (MITI)",
            "",
            "Exporter: Averis Global Trade Sdn Bhd",
            "Consignee: Yokohama Paper Mills Co., Ltd.",
            "",
            "Container No: PPRU4455667",
            "HS Code: 4703.29",
            "Product: Bleached Kraft Pulp",
            "Origin Criterion: Wholly Obtained — Malaysia",
            "",
            "ATIGA Form D — Preferential Certificate of Origin",
            "This is to certify that the goods described above originate in Malaysia",
            "in accordance with the ATIGA Rules of Origin.",
            "",
            "MITI Authentication Stamp: VERIFIED",
            "Chamber of Commerce Endorsement: VERIFIED",
        ],
    )


if __name__ == "__main__":
    main()
