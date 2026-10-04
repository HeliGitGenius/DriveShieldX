from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from database.db_manager import (
    create_report,
    get_all_notices,
    get_all_violations,
    get_owner_rule_violations,
    get_rule_violations,
)

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except Exception:  # pragma: no cover
    colors = None
    A4 = None
    getSampleStyleSheet = None
    Image = Paragraph = SimpleDocTemplate = Spacer = Table = TableStyle = None

BASE_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_reportlab() -> None:
    if SimpleDocTemplate is None:
        raise RuntimeError("reportlab is not installed. Please run: pip install reportlab")


def generate_pdf_report(generated_by: int, from_date: Optional[str] = None, to_date: Optional[str] = None, camera_id: Optional[int] = None) -> str:
    _ensure_reportlab()
    out = REPORT_DIR / f"violations_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    violations = get_all_violations(limit=1000, camera_id=camera_id)
    if from_date:
        violations = [v for v in violations if str(v["violation_time"])[:10] >= from_date]
    if to_date:
        violations = [v for v in violations if str(v["violation_time"])[:10] <= to_date]
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out), pagesize=A4)
    story: List = [Paragraph("DriveShieldX Violation Report", styles["Title"]), Spacer(1, 12)]
    story.append(Paragraph(f"Generated: {datetime.now().isoformat(sep=' ', timespec='seconds')}", styles["Normal"]))
    story.append(Paragraph(f"Rows: {len(violations)}", styles["Normal"]))
    if from_date or to_date:
        story.append(Paragraph(f"Range: {from_date or '-'} to {to_date or '-'}", styles["Normal"]))
    story.append(Spacer(1, 12))
    data = [["Violation ID", "Time", "Vehicle", "Plate", "Speed", "Limit", "Severity", "Location"]]
    for row in violations[:200]:
        data.append([
            row.get("violation_id"),
            str(row.get("violation_time", ""))[:19],
            row.get("vehicle_type", ""),
            row.get("plate_text") or row.get("plate_number") or "-",
            row.get("speed_value"),
            row.get("speed_limit"),
            row.get("severity_level"),
            row.get("location"),
        ])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#f8fafc")]),
    ]))
    story.append(table)
    doc.build(story)
    create_report(generated_by=generated_by, report_type="custom", file_path=str(out), from_date=from_date, to_date=to_date, violation_ids=[row["violation_id"] for row in violations])
    return str(out)


def generate_challan_pdf(notice: dict) -> str:
    _ensure_reportlab()
    out = REPORT_DIR / f"challan_notice_{notice['notice_id']}.pdf"
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out), pagesize=A4)
    story: List = [Paragraph("DriveShieldX Digital E-Challan", styles["Title"]), Spacer(1, 14)]
    fields = [
        ("Notice ID", notice.get("notice_id")),
        ("Issued At", notice.get("issued_at")),
        ("Registration", notice.get("registration") or ("Registered" if notice.get("owner_id") else "Unregistered")),
        ("Owner", notice.get("owner_name") or "—"),
        ("Owner Email", notice.get("owner_email") or "—"),
        ("Plate", notice.get("plate_number")),
        ("Vehicle Type", notice.get("vehicle_type")),
        ("Violation Time", notice.get("violation_time")),
        ("Location", notice.get("location")),
        ("Measured Speed", f"{notice.get('speed_value')} km/h"),
        ("Speed Limit", f"{notice.get('speed_limit')} km/h"),
        ("Offence Tier", str(notice.get("offense_tier", "first")).title()),
        ("Action Taken", str(notice.get("action_taken", "warning_fine")).replace("_", " ").title()),
        ("Licence Suspension", f"{notice.get('suspension_months', 0)} month(s)" if notice.get("suspension_months") else "—"),
        ("Base Fine", f"Rs. {float(notice.get('base_amount') or notice.get('amount') or 0):.2f}"),
        ("Late Fee", f"Rs. {float(notice.get('late_fee') or 0):.2f}"),
        ("Total Amount", f"Rs. {float(notice.get('amount') or 0) + float(notice.get('late_fee') or 0):.2f}"),
        ("Due Date", notice.get("due_date")),
        ("Payment Status", notice.get("payment_status")),
        ("Payment Method", notice.get("payment_method") or "—"),
        ("Notes", notice.get("notes") or "-"),
    ]
    data = [["Field", "Value"]] + [[k, str(v)] for k, v in fields]
    table = Table(data, colWidths=[140, 340])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(table)

    # ---- DriveShieldX layer-2 rule violations tied to the same vehicle ----
    _append_rule_violations_section(story, styles, notice)

    # ---- UPI QR code so the recipient can pay directly ----
    _append_upi_qr_section(story, styles, notice)

    doc.build(story)
    return str(out)


def _append_rule_violations_section(story, styles, notice) -> None:
    """Add a table of helmet / triple / seat-belt records for this vehicle."""
    plate = (notice.get("plate_number") or "").strip()
    if not plate:
        return
    # find rule violations for this exact plate
    rows = [r for r in get_rule_violations(limit=500)
            if (r.get("plate_text") or "").strip().upper() == plate.upper()]
    if not rows:
        return
    story.append(Spacer(1, 14))
    story.append(Paragraph("Additional DriveShieldX violations for this vehicle",
                           styles["Heading3"]))
    header = ["Rule", "Detected", "Track ID", "Fine (Rs.)", "Status"]
    data = [header]
    for r in rows[:8]:
        data.append([
            r["rule_type"].replace("_", " ").title(),
            str(r.get("detected_at", ""))[:19],
            r.get("tracker_id", ""),
            f"{float(r.get('fine_amount') or 0):.0f}",
            r.get("status", "detected"),
        ])
    t = Table(data, colWidths=[110, 130, 80, 80, 80])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(t)


def _append_upi_qr_section(story, styles, notice) -> None:
    """Generate a UPI intent QR (using our helpers) and embed it as an Image."""
    try:
        from detection.rule_violations import upi_intent, upi_qr_png
        import os
        import io
        amount = float(notice.get("amount") or 0) + float(notice.get("late_fee") or 0)
        if amount <= 0:
            amount = 500.0  # fallback for demo notices with no amount set
        intent = upi_intent(
            vpa=os.environ.get("UPI_ID", ""),
            payee=os.environ.get("UPI_PAYEE_NAME", "DriveShieldX Traffic Authority"),
            amount=int(amount),
            tid=str(notice.get("notice_id", "0")),
            note=f"E-Challan {notice.get('notice_id')} · {notice.get('plate_number') or ''}",
        )
        png = upi_qr_png(intent)
        story.append(Spacer(1, 18))
        story.append(Paragraph("Pay via UPI — scan the QR below with any UPI app", styles["Heading3"]))
        story.append(Spacer(1, 6))
        story.append(Image(io.BytesIO(png), width=180, height=180))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"UPI ID: {os.environ.get('UPI_ID', '') or '-'} · "
            f"Payee: {os.environ.get('UPI_PAYEE_NAME', 'DriveShieldX Traffic Authority')} · "
            f"Contact: {os.environ.get('CONTACT_PHONE', '') or '-'}",
            styles["Normal"]))
        try:
            from database import payments_db
            links = [l for l in payments_db.open_links([notice.get("notice_id")]) if l.get("short_url")]
            if links:
                story.append(Spacer(1, 4))
                story.append(Paragraph(f"Pay online (UPI / card / netbanking): {links[0]['short_url']}",
                                       styles["Normal"]))
        except Exception:
            pass
    except Exception:
        # never let QR embedding break challan generation
        return
