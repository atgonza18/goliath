#!/usr/bin/env python3
"""One-off script: Fetch recent Josh Hogger/Hauger constraint emails from Gmail IMAP."""

import imaplib
import email
import email.header
import os
import json
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Load env
load_dotenv("/opt/goliath/.env")

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "") or os.getenv("GOLIATH_GMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "") or os.getenv("GOLIATH_GMAIL_APP_PASSWORD", "")
IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")

# Output directory
OUT_DIR = Path("/opt/goliath/projects/constraints-inbox")
OUT_DIR.mkdir(parents=True, exist_ok=True)

ATTACHMENT_DIR = OUT_DIR / "attachments"
ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)


def decode_header_value(raw_val):
    """Decode RFC 2047 encoded header."""
    if not raw_val:
        return ""
    decoded_parts = email.header.decode_header(raw_val)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return " ".join(parts)


def extract_body(msg):
    """Extract plain text body, fallback to HTML."""
    if msg.is_multipart():
        plain = ""
        html = ""
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ct == "text/plain" and not plain:
                plain = text
            elif ct == "text/html" and not html:
                html = text
        return plain or html or ""
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        except Exception:
            pass
        return ""


def extract_attachments(msg):
    """Extract file attachments."""
    attachments = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        filename = part.get_filename()
        disp = str(part.get("Content-Disposition", ""))
        if not filename and "attachment" not in disp:
            continue
        if not filename:
            continue

        filename = decode_header_value(filename)
        try:
            data = part.get_payload(decode=True)
            if not data or len(data) < 100:
                continue
            attachments.append({
                "filename": filename,
                "data": data,
                "size": len(data),
            })
        except Exception:
            pass
    return attachments


def main():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: Gmail credentials not configured in .env")
        return

    print(f"Connecting to {IMAP_HOST} as {GMAIL_ADDRESS}...")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    # Search for emails with Hauger or Hogger in subject (last 14 days)
    since_date = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")

    all_ids = set()
    for term in ["hauger", "hogger", "Hauger", "Hogger"]:
        status, data = mail.search(None, f'(SINCE {since_date} SUBJECT "{term}")')
        if status == "OK" and data[0]:
            for mid in data[0].split():
                all_ids.add(mid)

    # Also try Gmail's X-GM-RAW for broader search
    try:
        for term in ["hauger", "hogger"]:
            status, data = mail.search(None, f'X-GM-RAW "from:{term} OR subject:{term}"')
            if status == "OK" and data[0]:
                for mid in data[0].split():
                    all_ids.add(mid)
    except Exception:
        pass  # X-GM-RAW may not be available

    if not all_ids:
        print("No emails found from Josh Hogger/Hauger in the last 14 days.")
        mail.logout()
        return

    print(f"Found {len(all_ids)} email(s). Fetching...")

    results = []
    for msg_id in sorted(all_ids):
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_header_value(msg.get("Subject", ""))
        from_addr = decode_header_value(msg.get("From", ""))
        date_str = msg.get("Date", "")
        body = extract_body(msg)
        attachments = extract_attachments(msg)

        # Save attachments to disk
        saved_files = []
        for att in attachments:
            safe_name = att["filename"].replace("/", "_").replace("\\", "_")
            att_path = ATTACHMENT_DIR / safe_name
            att_path.write_bytes(att["data"])
            saved_files.append(str(att_path))
            print(f"  Saved attachment: {safe_name} ({att['size']:,} bytes)")

        entry = {
            "subject": subject,
            "from": from_addr,
            "date": date_str,
            "body": body[:5000],  # Cap body at 5000 chars for summary
            "attachments": saved_files,
        }
        results.append(entry)
        print(f"  [{date_str}] {subject}")

    mail.logout()

    # Save summary JSON
    summary_path = OUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-hogger-constraints-emails.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved {len(results)} email(s) to {summary_path}")

    # Save readable text summary
    txt_path = OUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-hogger-constraints-summary.txt"
    with open(txt_path, "w") as f:
        for i, r in enumerate(results, 1):
            f.write(f"{'='*80}\n")
            f.write(f"EMAIL {i}\n")
            f.write(f"Date: {r['date']}\n")
            f.write(f"From: {r['from']}\n")
            f.write(f"Subject: {r['subject']}\n")
            if r['attachments']:
                f.write(f"Attachments: {', '.join(os.path.basename(a) for a in r['attachments'])}\n")
            f.write(f"{'='*80}\n\n")
            f.write(r['body'])
            f.write("\n\n")
    print(f"Saved readable summary to {txt_path}")


if __name__ == "__main__":
    main()
