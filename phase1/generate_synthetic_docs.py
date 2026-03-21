"""
Synthetic bank statement generator for testing the KYC extractor.
Creates three realistic PDFs:
  1. doc_clean.pdf       – pristine, English only
  2. doc_degraded.pdf    – slight skew + simulated watermark text
  3. doc_bilingual.pdf   – English + Hindi transliterations
"""

from pathlib import Path

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.pdfgen import canvas as rl_canvas

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("[WARNING] reportlab not installed – skipping PDF generation.")
    print("  Install with: pip install reportlab --break-system-packages")


# ─────────────────────────────────────────────
# Shared statement data
# ─────────────────────────────────────────────
TRANSACTIONS = [
    # (date, description, debit, credit, balance)
    ("01-06-2024", "Opening Balance",        None,      None,      "25000.00"),
    ("02-06-2024", "NEFT from Rajesh Kumar", None,   "15000.00", "40000.00"),
    ("05-06-2024", "Amazon Pay UPI",       "2499.00",  None,      "37501.00"),
    ("08-06-2024", "SBI ATM Withdrawal",   "5000.00",  None,      "32501.00"),
    ("12-06-2024", "Salary Credit",           None,  "75000.00", "107501.00"),
    ("15-06-2024", "Rent Payment NEFT",    "18000.00", None,      "89501.00"),
    ("18-06-2024", "Zomato UPI",            "850.00",  None,      "88651.00"),
    ("20-06-2024", "PhonePe Cashback",        None,     "200.00",  "88851.00"),
    ("22-06-2024", "LIC Premium",           "3500.00",  None,      "85351.00"),
    ("28-06-2024", "Swiggy UPI",            "1250.00",  None,      "84101.00"),
]

META = {
    "account_holder_name": "Priya Sharma",
    "bank_name": "State Bank of India",
    "account_number": "32145678901234",
    "period": "June 2024",
}

OUT_DIR = Path(__file__).parent.parent / "synthetic_docs"
OUT_DIR.mkdir(exist_ok=True)


def _build_table_data(bilingual: bool = False) -> list:
    headers = ["Date", "Description", "Debit (₹)", "Credit (₹)", "Balance (₹)"]
    rows = [headers]
    for d, desc, deb, cre, bal in TRANSACTIONS:
        if bilingual and desc == "Salary Credit":
            desc = "Salary Credit / वेतन क्रेडिट"
        if bilingual and desc == "Opening Balance":
            desc = "Opening Balance / प्रारंभिक शेष"
        rows.append([
            d,
            desc,
            deb if deb else "",
            cre if cre else "",
            bal,
        ])
    return rows


def _table_style() -> TableStyle:
    return TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),   colors.HexColor("#1a3c5e")),
        ("TEXTCOLOR",   (0, 0), (-1, 0),   colors.white),
        ("FONTNAME",    (0, 0), (-1, 0),   "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0),   8),
        ("ALIGN",       (0, 0), (-1, -1),  "CENTER"),
        ("ALIGN",       (1, 1), (1, -1),   "LEFT"),
        ("FONTNAME",    (0, 1), (-1, -1),  "Helvetica"),
        ("FONTSIZE",    (0, 1), (-1, -1),  8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eaf1fb")]),
        ("GRID",        (0, 0), (-1, -1),  0.4, colors.grey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
    ])


# ─────────────────────────────────────────────
# Doc 1: Clean PDF
# ─────────────────────────────────────────────
def generate_clean_pdf(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story = []

    # Header
    story.append(Paragraph(
        f"<b>{META['bank_name']}</b>", styles["Title"]))
    story.append(Paragraph(
        "Account Statement", styles["Heading2"]))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"Account Holder: <b>{META['account_holder_name']}</b>", styles["Normal"]))
    story.append(Paragraph(
        f"Account Number: <b>{META['account_number']}</b>", styles["Normal"]))
    story.append(Paragraph(
        f"Statement Period: <b>{META['period']}</b>", styles["Normal"]))
    story.append(Spacer(1, 0.5*cm))

    # Transactions table
    col_widths = [2.5*cm, 6.5*cm, 2.5*cm, 2.5*cm, 2.5*cm]
    tbl = Table(_build_table_data(), colWidths=col_widths)
    tbl.setStyle(_table_style())
    story.append(tbl)

    doc.build(story)
    print(f"  Created: {path.name}")


# ─────────────────────────────────────────────
# Doc 2: Degraded PDF (watermark overlay)
# ─────────────────────────────────────────────
def generate_degraded_pdf(path: Path) -> None:
    """
    Generates the clean PDF first, then overlays a semi-transparent
    diagonal watermark to simulate a degraded / scan-quality document.
    """
    import io
    from reportlab.pdfgen import canvas as rl_canvas2

    # Build base PDF in memory
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{META['bank_name']}</b>", styles["Title"]),
        Paragraph("Account Statement", styles["Heading2"]),
        Spacer(1, 0.3*cm),
        Paragraph(f"Account Holder: <b>{META['account_holder_name']}</b>", styles["Normal"]),
        Paragraph(f"Account Number: <b>{META['account_number']}</b>", styles["Normal"]),
        Paragraph(f"Statement Period: <b>{META['period']}</b>", styles["Normal"]),
        Spacer(1, 0.5*cm),
    ]
    col_widths = [2.5*cm, 6.5*cm, 2.5*cm, 2.5*cm, 2.5*cm]
    tbl = Table(_build_table_data(), colWidths=col_widths)
    tbl.setStyle(_table_style())
    story.append(tbl)
    doc.build(story)

    # Overlay watermark using PyMuPDF
    try:
        import fitz
        buf.seek(0)
        pdf = fitz.open(stream=buf.read(), filetype="pdf")
        for page in pdf:
            # Add a diagonal watermark text
            tw = fitz.TextWriter(page.rect)
            font = fitz.Font("helv")
            # Place watermark text diagonally at centre
            page.insert_text(
                fitz.Point(80, 400),
                "CONFIDENTIAL",
                fontsize=48,
                rotate=45,
                color=(0.8, 0.8, 0.8),   # light grey
                fill_opacity=0.3,
            )
        pdf.save(str(path))
        pdf.close()
    except Exception as e:
        # Fallback: just save without watermark
        buf.seek(0)
        path.write_bytes(buf.read())
        print(f"  [WARN] Watermark overlay failed ({e}); saved without.")

    print(f"  Created: {path.name}")


# ─────────────────────────────────────────────
# Doc 3: Bilingual PDF (English + Hindi labels)
# ─────────────────────────────────────────────
def generate_bilingual_pdf(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            topMargin=2*cm, bottomMargin=2*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph(
        f"<b>{META['bank_name']} / भारतीय स्टेट बैंक</b>", styles["Title"]))
    story.append(Paragraph(
        "Account Statement / खाता विवरण", styles["Heading2"]))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(
        f"Account Holder / खाताधारक: <b>{META['account_holder_name']}</b>",
        styles["Normal"]))
    story.append(Paragraph(
        f"Account Number / खाता संख्या: <b>{META['account_number']}</b>",
        styles["Normal"]))
    story.append(Paragraph(
        f"Statement Period / विवरण अवधि: <b>{META['period']}</b>",
        styles["Normal"]))
    story.append(Spacer(1, 0.5*cm))

    col_widths = [2.5*cm, 7*cm, 2.2*cm, 2.2*cm, 2.6*cm]
    tbl = Table(_build_table_data(bilingual=True), colWidths=col_widths)
    tbl.setStyle(_table_style())
    story.append(tbl)

    # Footer in Hindi
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        "यह एक स्वचालित रूप से उत्पन्न विवरण है। किसी भी प्रश्न के लिए अपनी शाखा से संपर्क करें।",
        styles["Normal"]))

    doc.build(story)
    print(f"  Created: {path.name}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not REPORTLAB_AVAILABLE:
        raise SystemExit("Install reportlab first: pip install reportlab --break-system-packages")

    print(f"\nGenerating synthetic bank statement PDFs in: {OUT_DIR}\n")
    generate_clean_pdf(OUT_DIR / "doc_clean.pdf")
    generate_degraded_pdf(OUT_DIR / "doc_degraded.pdf")
    generate_bilingual_pdf(OUT_DIR / "doc_bilingual.pdf")
    print("\nDone. Pass these files to extractor.py for testing.\n")
