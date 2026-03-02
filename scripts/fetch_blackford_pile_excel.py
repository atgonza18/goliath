#!/usr/bin/env python3
"""One-off script: Search Gmail IMAP for 'Blackford Solar Top of Pile' Excel attachments.

Searches for emails with "Blackford" and "pile" in the subject, downloads any
Excel attachments (.xlsx, .xls), and saves them to the Blackford engineering folder.

Usage:
    python /opt/goliath/scripts/fetch_blackford_pile_excel.py

Credentials are loaded from /opt/goliath/.env (GMAIL_ADDRESS, GMAIL_APP_PASSWORD).
"""

import imaplib
import email
import email.header
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# ── Configuration ────────────────────────────────────────────────────────
load_dotenv("/opt/goliath/.env")

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "") or os.getenv("GOLIATH_GMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "") or os.getenv("GOLIATH_GMAIL_APP_PASSWORD", "")
IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")

# Where to save Excel attachments
TARGET_DIR = Path("/opt/goliath/projects/blackford/project-details/engineering")
TARGET_DIR.mkdir(parents=True, exist_ok=True)

# How far back to search (days)
SEARCH_DAYS = 60

# Excel file extensions to download
EXCEL_EXTENSIONS = {".xlsx", ".xls"}


# ── Helpers ──────────────────────────────────────────────────────────────

def decode_header_value(raw_val: str) -> str:
    """Decode RFC 2047 encoded header value."""
    if not raw_val:
        return ""
    decoded_parts = email.header.decode_header(raw_val)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return " ".join(parts).strip()


def extract_excel_attachments(msg) -> list:
    """Extract Excel file attachments from a MIME email.

    Returns list of dicts: [{'filename': str, 'data': bytes, 'size': int}]
    """
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

        # Decode filename
        filename = decode_header_value(filename)
        ext = os.path.splitext(filename)[1].lower()

        if ext not in EXCEL_EXTENSIONS:
            continue

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


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: Gmail credentials not configured in .env")
        print("  Need: GMAIL_ADDRESS (or GOLIATH_GMAIL)")
        print("  Need: GMAIL_APP_PASSWORD (or GOLIATH_GMAIL_APP_PASSWORD)")
        return

    print(f"Connecting to {IMAP_HOST} as {GMAIL_ADDRESS}...")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    # Search for emails with "Blackford" in subject within the date range
    since_date = (datetime.now() - timedelta(days=SEARCH_DAYS)).strftime("%d-%b-%Y")
    print(f"Searching for emails since {since_date} with 'Blackford' in subject...")

    # Strategy 1: IMAP SUBJECT search for "Blackford" (catches PA-relayed and direct)
    all_ids = set()
    for term in ["Blackford", "blackford"]:
        status, data = mail.search(None, f'(SINCE {since_date} SUBJECT "{term}")')
        if status == "OK" and data[0]:
            for mid in data[0].split():
                all_ids.add(mid)

    # Strategy 2: Try Gmail X-GM-RAW for broader search (may not work on all accounts)
    try:
        status, data = mail.search(
            None, 'X-GM-RAW "subject:(blackford pile)"'
        )
        if status == "OK" and data[0]:
            for mid in data[0].split():
                all_ids.add(mid)
    except Exception:
        pass  # X-GM-RAW not available on all IMAP implementations

    if not all_ids:
        print(f"No emails found with 'Blackford' in subject in the last {SEARCH_DAYS} days.")
        mail.logout()
        return

    print(f"Found {len(all_ids)} email(s) with 'Blackford' in subject. Filtering for 'pile'...")

    # Fetch and filter for "pile" in subject, then look for Excel attachments
    matched_emails = []
    for msg_id in sorted(all_ids):
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = decode_header_value(msg.get("Subject", ""))
        subject_lower = subject.lower()

        # Filter: must contain "pile" (catches "Top of Pile", "pile", etc.)
        if "pile" not in subject_lower:
            continue

        from_addr = decode_header_value(msg.get("From", ""))
        date_str = msg.get("Date", "")

        print(f"\n{'='*70}")
        print(f"MATCH FOUND:")
        print(f"  Subject: {subject}")
        print(f"  From:    {from_addr}")
        print(f"  Date:    {date_str}")

        # Extract Excel attachments
        excel_attachments = extract_excel_attachments(msg)

        if not excel_attachments:
            print(f"  Attachments: (no Excel files found)")
            # List all attachments for debugging
            if msg.is_multipart():
                for part in msg.walk():
                    fn = part.get_filename()
                    if fn:
                        fn = decode_header_value(fn)
                        print(f"    - {fn} (not Excel)")
            continue

        # Save Excel attachments
        print(f"  Excel attachments: {len(excel_attachments)}")
        saved_files = []
        today = datetime.now().strftime("%Y-%m-%d")

        for att in excel_attachments:
            # Sanitize filename
            safe_name = re.sub(r'[^\w\-._() ]', '', att["filename"]).strip()
            if not safe_name:
                safe_name = f"blackford_top_of_pile.xlsx"

            # Add date prefix if not already dated
            if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}', safe_name):
                safe_name = f"{today}_{safe_name}"

            filepath = TARGET_DIR / safe_name

            # Avoid overwriting
            if filepath.exists():
                stem = filepath.stem
                suffix = filepath.suffix
                counter = 1
                while filepath.exists():
                    filepath = TARGET_DIR / f"{stem}_{counter}{suffix}"
                    counter += 1

            filepath.write_bytes(att["data"])
            saved_files.append(filepath)
            print(f"  SAVED: {filepath}")
            print(f"    Size: {att['size']:,} bytes")

        matched_emails.append({
            "subject": subject,
            "from": from_addr,
            "date": date_str,
            "files": [str(f) for f in saved_files],
        })

    mail.logout()

    # Summary
    print(f"\n{'='*70}")
    if matched_emails:
        print(f"DONE: Found {len(matched_emails)} email(s) matching 'Blackford' + 'pile'")
        total_files = sum(len(e["files"]) for e in matched_emails)
        print(f"      Saved {total_files} Excel file(s) to {TARGET_DIR}/")
    else:
        print("No emails found with both 'Blackford' and 'pile' in subject.")
        print("Tip: The email may use a different subject format. Check the Gmail")
        print("     inbox manually or broaden the search terms.")


if __name__ == "__main__":
    main()
