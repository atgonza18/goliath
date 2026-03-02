#!/usr/bin/env python3
"""Email Salt Branch constraint questions PDF to user."""

import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from dotenv import load_dotenv

load_dotenv("/opt/goliath/.env")

SMTP_USER = os.getenv("GOLIATH_GMAIL")
SMTP_PASS = os.getenv("GOLIATH_GMAIL_APP_PASSWORD")

TO_EMAIL = "bandicoot.hg@gmail.com"
SUBJECT = "Salt Branch - Pre-Call Constraint Questions (Feb 27, 2026)"
PDF_PATH = "/opt/goliath/projects/salt-branch/reports/2026-02-27-salt-branch-constraint-questions.pdf"

BODY = """Salt Branch Pre-Call Constraint Questions

8 constraints covered (5 High Priority, 3 Medium Priority) with probing questions for each.

Key themes:
- Safety hold zeroing out racking & module production
- Pile production collapse (175/day vs 1,300 required)
- $4M+ commercial risk growing daily
- Data discrepancy needs resolution

Demand specifics: names, dates, and numbers. No vague status updates.

-- Goliath / Nimrod
"""

def send_email():
    if not SMTP_USER or not SMTP_PASS:
        print(f"ERROR: Missing credentials. SMTP_USER={SMTP_USER}, SMTP_PASS={'SET' if SMTP_PASS else 'MISSING'}")
        return False

    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = TO_EMAIL
    msg["Subject"] = SUBJECT
    msg.attach(MIMEText(BODY, "plain"))

    # Attach PDF
    with open(PDF_PATH, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="Salt-Branch-Constraint-Questions-2026-02-27.pdf"')
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
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    send_email()
