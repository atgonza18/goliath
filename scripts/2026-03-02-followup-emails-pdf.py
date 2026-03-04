#!/usr/bin/env python3
"""Generate follow-up email drafts PDF for Tehuacana & Pecan Prairie and email it."""

import smtplib
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from dotenv import load_dotenv

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak
)

load_dotenv("/opt/goliath/.env")

# --- Config ---
OUTPUT_DIR = "/opt/goliath/reports"
os.makedirs(OUTPUT_DIR, exist_ok=True)
PDF_PATH = os.path.join(OUTPUT_DIR, "2026-03-02-followup-email-drafts.pdf")
TO_EMAIL = "bandicoot.hg@gmail.com"
SMTP_USER = os.getenv("GOLIATH_GMAIL")
SMTP_PASS = os.getenv("GOLIATH_GMAIL_APP_PASSWORD")

# --- Colors ---
DSC_BLUE = colors.HexColor("#1A5276")
ACCENT_BLUE = colors.HexColor("#2980B9")
GREEN = colors.HexColor("#27AE60")
LIGHT_GRAY = colors.HexColor("#F4F6F7")
DARK_GRAY = colors.HexColor("#2C3E50")
MED_GRAY = colors.HexColor("#7F8C8D")

# --- Styles ---
styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    name="DocTitle",
    fontName="Helvetica-Bold",
    fontSize=18,
    textColor=DSC_BLUE,
    spaceAfter=6,
))
styles.add(ParagraphStyle(
    name="DocSubtitle",
    fontName="Helvetica",
    fontSize=11,
    textColor=MED_GRAY,
    spaceAfter=20,
))
styles.add(ParagraphStyle(
    name="ProjectHeader",
    fontName="Helvetica-Bold",
    fontSize=14,
    textColor=colors.white,
    spaceBefore=16,
    spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="EmailSubject",
    fontName="Helvetica-Bold",
    fontSize=11,
    textColor=DSC_BLUE,
    spaceBefore=12,
    spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="EmailMeta",
    fontName="Helvetica-Oblique",
    fontSize=9,
    textColor=MED_GRAY,
    spaceAfter=6,
))
styles.add(ParagraphStyle(
    name="EmailBody",
    fontName="Helvetica",
    fontSize=10,
    textColor=DARK_GRAY,
    leading=14,
    spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="EmailBodyBold",
    fontName="Helvetica-Bold",
    fontSize=10,
    textColor=DARK_GRAY,
    leading=14,
    spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="NumberedItem",
    fontName="Helvetica",
    fontSize=10,
    textColor=DARK_GRAY,
    leading=14,
    leftIndent=18,
    spaceAfter=2,
))
styles.add(ParagraphStyle(
    name="Footer",
    fontName="Helvetica",
    fontSize=8,
    textColor=MED_GRAY,
    alignment=TA_CENTER,
))

def project_banner(project_name, status):
    """Green banner for ON TRACK projects."""
    banner_style = ParagraphStyle(
        name=f"Banner_{project_name}",
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=colors.white,
        alignment=TA_CENTER,
        spaceBefore=0,
        spaceAfter=0,
    )
    data = [[Paragraph(f"{project_name}  —  {status}", banner_style)]]
    t = Table(data, colWidths=[7 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
    ]))
    return t

def email_block(subject, to_line, body_paragraphs):
    """Renders a single email draft as a styled block."""
    elements = []
    elements.append(Paragraph(f"Subject: {subject}", styles["EmailSubject"]))
    elements.append(Paragraph(f"To: {to_line}", styles["EmailMeta"]))
    elements.append(Spacer(1, 4))

    for p in body_paragraphs:
        if p.startswith("**"):
            # Bold line
            text = p.replace("**", "")
            elements.append(Paragraph(text, styles["EmailBodyBold"]))
        elif p.startswith("  "):
            # Numbered/indented item
            elements.append(Paragraph(p.strip(), styles["NumberedItem"]))
        else:
            elements.append(Paragraph(p, styles["EmailBody"]))

    elements.append(Spacer(1, 8))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GRAY))
    elements.append(Spacer(1, 8))
    return elements

def build_pdf():
    doc = SimpleDocTemplate(
        PDF_PATH,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    story = []

    # --- Title ---
    story.append(Paragraph("DSC Follow-Up Email Drafts", styles["DocTitle"]))
    story.append(Paragraph("March 2, 2026  •  Tehuacana &amp; Pecan Prairie  •  Prepared by Goliath", styles["DocSubtitle"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=DSC_BLUE))
    story.append(Spacer(1, 16))

    # =========================================================================
    # TEHUACANA
    # =========================================================================
    story.append(project_banner("TEHUACANA", "ON TRACK"))
    story.append(Spacer(1, 12))

    # --- Email 1: GPS Equipment ---
    story.extend(email_block(
        subject="Tehuacana — Carlson GPS Install Status on PD-10 Rental Units",
        to_line="Patrick (Site) / RDO Contact",
        body_paragraphs=[
            "Hi Patrick,",
            "",
            "Following up on the Carlson GPS installations for the 6 rental PD-10 units. Per our last update (2/27), the PO was cut and RDO was heading to Tehuacana after their Mayes visit.",
            "",
            "Need a quick status check on three things:",
            "",
            "  1. Has RDO arrived on site or do we have a confirmed arrival date?",
            "  2. What's the delivery status on the GPS wiring kits — are they on site or in transit?",
            "  3. Once RDO is on site, what's the estimated turnaround to get all 6 units GPS-operational?",
            "",
            "Pile production planning kicks off 3/6 and we need these units ready to go. Any delays here directly impact our ability to hit scheduled production rates.",
            "",
            "Please confirm status by EOD Wednesday so we can plan accordingly.",
            "",
            "Thanks,",
            "[Your Name]",
        ]
    ))

    # --- Email 2: PD-10 Fleet Availability ---
    story.extend(email_block(
        subject="Tehuacana — PD-10 Fleet Status &amp; Delivery Schedule",
        to_line="Tyler Wilcox / Ben Larson (Equipment Team)",
        body_paragraphs=[
            "Tyler / Ben,",
            "",
            "Following up on PD-10 fleet procurement for Tehuacana. Last update (2/26) confirmed 3 machines en route from Three Rivers, with a plan to add 2 units per week until we hit full fleet.",
            "",
            "A few things I need confirmed:",
            "",
            "  1. Have the 3 Three Rivers units arrived on site? If not, what's the ETA?",
            "  2. What's our current operational count as of today?",
            "  3. Is the 2-units-per-week delivery cadence still on track? When do we hit full fleet (all units needed for target production rates)?",
            "  4. For units already on site — are the Carlson GPS systems operational on all of them, or are some still waiting on the GPS install?",
            "",
            "With pile production planning starting 3/6, we need full visibility on fleet readiness. Any gaps in either unit count or GPS functionality will directly impact our production ramp.",
            "",
            "Please send a current fleet roster showing each unit's status (on site / in transit / GPS operational) by EOD Thursday.",
            "",
            "Thanks,",
            "[Your Name]",
        ]
    ))

    # --- Email 3: Shoals Material ---
    story.extend(email_block(
        subject="Tehuacana — Shoals CAB &amp; Messenger Wire PO / Delivery Schedule",
        to_line="Procurement Lead / Shoals Contact",
        body_paragraphs=[
            "Hi team,",
            "",
            "Following up on the Shoals material procurement for Tehuacana — specifically CAB and messenger wire. Per the 2/27 update, the team was working to get the PO executed that week and we were awaiting the final delivery schedule.",
            "",
            "Three things I need confirmed:",
            "",
            "  1. Has the PO been fully executed? If so, what's the PO number and execution date?",
            "  2. What is the confirmed delivery schedule — first shipment date, full delivery completion date, and any phasing/partial shipments?",
            "  3. Does the delivery schedule align with the construction install sequence, or do we need to flag a potential gap to the site team?",
            "",
            "This material was not ordered on time, which already created significant schedule risk. We need to lock down the delivery timeline immediately so we can validate against the install plan and flag any further exposure.",
            "",
            "Please provide the PO confirmation and delivery schedule by EOD Wednesday.",
            "",
            "Thanks,",
            "[Your Name]",
        ]
    ))

    # =========================================================================
    # PECAN PRAIRIE
    # =========================================================================
    story.append(PageBreak())
    story.append(project_banner("PECAN PRAIRIE", "ON TRACK"))
    story.append(Spacer(1, 12))

    # --- Email 1: Acceleration CO ---
    story.extend(email_block(
        subject="Pecan Prairie — Acceleration CO #7 Redline Status ($2.4M)",
        to_line="Linden / Commercial Team",
        body_paragraphs=[
            "Hi team,",
            "",
            "Following up on the Repsol Acceleration Change Order (#7) for Pecan Prairie. Per the 2/27 update, Repsol agreed to the non-accelerated schedule plus $2.4M, and the team was finalizing contract redlines that week.",
            "",
            "Need clarity on a few things:",
            "",
            "  1. What is the current status of the redlines? Have they been sent to Repsol, and if so, have we received their response?",
            "  2. Are there any specific contract language items Repsol is pushing back on? If so, what are the sticking points and what's our position?",
            "  3. What is the realistic timeline to get this CO fully executed? We're now past the original need-by date — is there a hard deadline where this exposure escalates further?",
            "  4. Is there anything the project team needs from DSC leadership to help move this across the finish line?",
            "",
            "This CO has been open for a while and the $2.4M represents significant upfront cost risk until it's locked down. With 30 open constraints on this project, getting the commercial framework settled lets the team focus on execution.",
            "",
            "Please provide an update on redline status and expected execution timeline by EOD Friday.",
            "",
            "Thanks,",
            "[Your Name]",
        ]
    ))

    # --- Footer ---
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MED_GRAY))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Generated by Goliath — DSC Construction Operations Platform", styles["Footer"]))
    story.append(Paragraph(f"{datetime.now().strftime('%B %d, %Y at %I:%M %p CT')}", styles["Footer"]))

    doc.build(story)
    print(f"PDF generated: {PDF_PATH}")

def send_email():
    if not SMTP_USER or not SMTP_PASS:
        print(f"ERROR: Missing credentials. SMTP_USER={SMTP_USER}, SMTP_PASS={'SET' if SMTP_PASS else 'MISSING'}")
        return False

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL
    msg["Subject"] = "Follow-Up Email Drafts — Tehuacana & Pecan Prairie (March 2, 2026)"

    body = """Follow-Up Email Drafts — Tehuacana & Pecan Prairie

4 follow-up emails ready to review and send:

TEHUACANA (3 emails):
  1. PD-10 GPS Equipment — Patrick / RDO
  2. PD-10 Fleet Availability — Tyler Wilcox / Ben Larson
  3. Shoals CAB & Messenger Wire — Procurement / Shoals

PECAN PRAIRIE (1 email):
  4. Acceleration CO #7 Redlines ($2.4M) — Linden / Commercial

All drafts are copy-paste ready. Just add your name and send.

-- Goliath / Nimrod
"""
    msg.attach(MIMEText(body, "plain"))

    # Attach PDF
    with open(PDF_PATH, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            'attachment; filename="Follow-Up-Emails-Tehuacana-Pecan-Prairie-2026-03-02.pdf"'
        )
        msg.attach(part)

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, TO_EMAIL, msg.as_string())
        server.quit()
        print(f"SUCCESS: Email sent to {TO_EMAIL}")
        return True
    except Exception as e:
        print(f"ERROR sending email: {e}")
        return False

if __name__ == "__main__":
    build_pdf()
    send_email()
