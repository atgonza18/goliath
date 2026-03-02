#!/usr/bin/env python3
"""Test script: Check Gmail for recent POD emails and attachments.

Connects to Gmail IMAP, searches for recent emails that:
  1. Have "POD" in the subject
  2. Have "[INBOX:" tag in the subject (Power Automate relay format)
  3. Any recent emails (last 3 days) to see if PA test emails arrived

For each match, reports on attachments (filename, size, content type).
If POD attachments are found, downloads and saves them to the project pod/ folder.
"""

import email
import email.header
import email.utils
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))

from bot.config import (
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST,
    PROJECTS, PROJECTS_DIR, match_project_key,
)

# Allowed attachment extensions
ALLOWED_EXTENSIONS = {
    '.pdf', '.xlsx', '.xls', '.csv', '.doc', '.docx',
    '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.zip', '.msg',
}

SEPARATOR = "=" * 70


def decode_subject(msg):
    """Decode the subject header from a MIME message."""
    raw_subject = msg.get("Subject", "")
    decoded_parts = email.header.decode_header(raw_subject)
    subject_parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            subject_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            subject_parts.append(part)
    return " ".join(subject_parts)


def extract_all_attachments(msg):
    """Extract ALL attachments from a MIME message (no extension filter)."""
    attachments = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()

        if not filename and "attachment" not in disposition:
            continue
        if not filename:
            # Has attachment disposition but no filename
            ct = part.get_content_type()
            attachments.append({
                "filename": f"(unnamed - {ct})",
                "data": None,
                "content_type": ct,
                "size": 0,
                "has_data": False,
            })
            continue

        # Decode filename
        decoded = email.header.decode_header(filename)
        name_parts = []
        for p, ch in decoded:
            if isinstance(p, bytes):
                name_parts.append(p.decode(ch or "utf-8", errors="replace"))
            else:
                name_parts.append(p)
        filename = "".join(name_parts)

        try:
            data = part.get_payload(decode=True)
            size = len(data) if data else 0
            ext = os.path.splitext(filename)[1].lower()
            attachments.append({
                "filename": filename,
                "data": data,
                "content_type": part.get_content_type(),
                "size": size,
                "has_data": bool(data and size > 0),
                "allowed_ext": ext in ALLOWED_EXTENSIONS,
            })
        except Exception as e:
            attachments.append({
                "filename": filename,
                "data": None,
                "content_type": part.get_content_type(),
                "size": 0,
                "has_data": False,
                "error": str(e),
            })

    return attachments


def save_attachment(data, target_dir, filename, date_str):
    """Save an attachment file to the target directory."""
    safe_name = re.sub(r'[^\w\-._() ]', '', filename).strip()
    if not safe_name:
        safe_name = "attachment.bin"
    if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}', safe_name):
        safe_name = f"{date_str}_{safe_name}"

    filepath = target_dir / safe_name
    if filepath.exists():
        stem = filepath.stem
        suffix = filepath.suffix
        counter = 1
        while filepath.exists():
            filepath = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    target_dir.mkdir(parents=True, exist_ok=True)
    filepath.write_bytes(data)
    return filepath


def describe_parts(msg, indent=0):
    """Recursively describe the MIME structure of a message."""
    prefix = "  " * indent
    ct = msg.get_content_type()
    disp = msg.get("Content-Disposition", "(none)")
    fn = msg.get_filename() or "(no filename)"
    payload = msg.get_payload(decode=True) if not msg.is_multipart() else None
    size = len(payload) if payload else 0
    print(f"{prefix}Part: {ct}  |  disposition: {disp}  |  filename: {fn}  |  size: {size}")
    if msg.is_multipart():
        for sub in msg.get_payload():
            describe_parts(sub, indent + 1)


def main():
    print(SEPARATOR)
    print("Gmail IMAP Attachment Test")
    print(f"Account: {GMAIL_ADDRESS}")
    print(f"IMAP Host: {GMAIL_IMAP_HOST}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEPARATOR)

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        sys.exit(1)

    print("\nConnecting to Gmail IMAP...")
    try:
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, 993, timeout=30)
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        print("  Login successful!")
    except Exception as e:
        print(f"  ERROR: Connection/login failed: {e}")
        sys.exit(1)

    mail.select("INBOX")

    # Get mailbox status
    status, count_data = mail.search(None, "ALL")
    total_msgs = len(count_data[0].split()) if count_data and count_data[0] else 0
    print(f"  Total emails in INBOX: {total_msgs}")

    since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
    saved_files = []

    # ======================================================================
    # Search 1: All emails from last 3 days (to see what's arriving)
    # ======================================================================
    print(f"\n{SEPARATOR}")
    print(f"SEARCH 1: All emails from last 3 days (since {since_date})")
    print(SEPARATOR)

    status, data = mail.search(None, f'(SINCE {since_date})')
    if status == "OK" and data and data[0]:
        recent_ids = data[0].split()
        print(f"Found {len(recent_ids)} email(s) in last 3 days\n")

        for i, msg_id in enumerate(recent_ids):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_subject(msg)
            from_addr = msg.get("From", "unknown")
            date_str = msg.get("Date", "unknown")

            # Quick attachment count
            atts = extract_all_attachments(msg)
            att_summary = f" | {len(atts)} attachment(s)" if atts else " | no attachments"

            print(f"  [{i+1}] Date: {date_str}")
            print(f"      From: {from_addr}")
            print(f"      Subject: {subject}")
            print(f"      {att_summary}")
            if atts:
                for a in atts:
                    size_kb = a['size'] / 1024 if a['size'] else 0
                    print(f"        -> {a['filename']}  ({a['content_type']}, {size_kb:.1f} KB)")
            print()
    else:
        print("No emails found in last 3 days.\n")

    # ======================================================================
    # Search 2: Emails with [INBOX: in subject (PA relay format) — last 3 days
    # ======================================================================
    print(f"{SEPARATOR}")
    print(f"SEARCH 2: Emails with [INBOX: tag in subject (last 3 days)")
    print(SEPARATOR)

    status, data = mail.search(None, f'(SUBJECT "[INBOX:" SINCE {since_date})')
    inbox_ids = []
    if status == "OK" and data and data[0]:
        inbox_ids = data[0].split()
        print(f"Found {len(inbox_ids)} [INBOX:] email(s)\n")

        for i, msg_id in enumerate(inbox_ids):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_subject(msg)
            from_addr = msg.get("From", "unknown")
            date_str = msg.get("Date", "unknown")

            # Parse [INBOX: sender] tag
            inbox_match = re.match(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", subject, re.IGNORECASE)
            if inbox_match:
                orig_sender = inbox_match.group(1).strip()
                orig_subject = inbox_match.group(2).strip()
            else:
                orig_sender = "(could not parse)"
                orig_subject = subject

            atts = extract_all_attachments(msg)

            print(f"  [{i+1}] Date: {date_str}")
            print(f"      Gmail From: {from_addr}")
            print(f"      Full Subject: {subject}")
            print(f"      Original Sender: {orig_sender}")
            print(f"      Original Subject: {orig_subject}")
            print(f"      Attachments: {len(atts)}")

            # Show MIME structure for debugging
            print(f"      MIME structure:")
            describe_parts(msg, indent=4)

            if atts:
                for a in atts:
                    size_kb = a['size'] / 1024 if a['size'] else 0
                    allowed = a.get('allowed_ext', False)
                    print(f"        -> {a['filename']}  ({a['content_type']}, {size_kb:.1f} KB, allowed={allowed})")
            else:
                print("        (no attachments found)")
            print()
    else:
        print("No [INBOX:] emails found in last 3 days.\n")

    # ======================================================================
    # Search 3: Emails with "POD" in subject — last 3 days
    # ======================================================================
    print(f"{SEPARATOR}")
    print(f"SEARCH 3: Emails with 'POD' in subject (last 3 days)")
    print(SEPARATOR)

    status, data = mail.search(None, f'(SUBJECT "POD" SINCE {since_date})')
    pod_ids = []
    if status == "OK" and data and data[0]:
        pod_ids = data[0].split()
        print(f"Found {len(pod_ids)} POD email(s)\n")

        for i, msg_id in enumerate(pod_ids):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_subject(msg)
            from_addr = msg.get("From", "unknown")
            date_str = msg.get("Date", "unknown")

            atts = extract_all_attachments(msg)

            print(f"  [{i+1}] Date: {date_str}")
            print(f"      From: {from_addr}")
            print(f"      Subject: {subject}")
            print(f"      Attachments: {len(atts)}")

            # Show MIME structure
            print(f"      MIME structure:")
            describe_parts(msg, indent=4)

            if atts:
                for a in atts:
                    size_kb = a['size'] / 1024 if a['size'] else 0
                    print(f"        -> {a['filename']}  ({a['content_type']}, {size_kb:.1f} KB)")
            else:
                print("        (no attachments found)")
            print()
    else:
        print("No POD emails found in last 3 days.\n")

    # ======================================================================
    # Search 4: ALL unread emails (to catch any test emails just sent)
    # ======================================================================
    print(f"{SEPARATOR}")
    print(f"SEARCH 4: All UNREAD emails")
    print(SEPARATOR)

    status, data = mail.search(None, '(UNSEEN)')
    if status == "OK" and data and data[0]:
        unread_ids = data[0].split()
        print(f"Found {len(unread_ids)} unread email(s)\n")

        for i, msg_id in enumerate(unread_ids):
            # Peek without marking as read
            status, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_subject(msg)
            from_addr = msg.get("From", "unknown")
            date_str = msg.get("Date", "unknown")

            atts = extract_all_attachments(msg)

            print(f"  [{i+1}] Date: {date_str}")
            print(f"      From: {from_addr}")
            print(f"      Subject: {subject}")
            print(f"      Attachments: {len(atts)}")
            if atts:
                for a in atts:
                    size_kb = a['size'] / 1024 if a['size'] else 0
                    print(f"        -> {a['filename']}  ({a['content_type']}, {size_kb:.1f} KB)")
            print()
    else:
        print("No unread emails found.\n")

    # ======================================================================
    # Download & file POD attachments from [INBOX:] emails
    # ======================================================================
    print(f"{SEPARATOR}")
    print("DOWNLOAD: Filing POD attachments to project folders")
    print(SEPARATOR)

    # Re-fetch [INBOX:] emails that have POD in the subject and attachments
    # Use a broader window (last 7 days) to catch anything recent
    since_7d = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    status, data = mail.search(None, f'(SUBJECT "[INBOX:" SINCE {since_7d})')
    if status == "OK" and data and data[0]:
        all_inbox_ids = data[0].split()
        print(f"Scanning {len(all_inbox_ids)} [INBOX:] emails from last 7 days for POD attachments...\n")

        for msg_id in all_inbox_ids:
            status, msg_data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_subject(msg)

            # Parse [INBOX:] tag
            inbox_match = re.match(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", subject, re.IGNORECASE)
            if not inbox_match:
                continue
            orig_sender = inbox_match.group(1).strip()
            orig_subject = inbox_match.group(2).strip()

            # Is this a POD email?
            if not re.search(r'\bpod\b', orig_subject.lower()):
                continue

            # Match to a project
            project_key = match_project_key(orig_subject)

            atts = extract_all_attachments(msg)
            saveable = [a for a in atts if a.get('has_data') and a.get('allowed_ext', False) and a['size'] >= 100]

            # Get email date for filename
            date_header = msg.get("Date", "")
            try:
                email_date = email.utils.parsedate_to_datetime(date_header).strftime("%Y-%m-%d")
            except Exception:
                email_date = datetime.now().strftime("%Y-%m-%d")

            print(f"  POD email: {orig_subject}")
            print(f"    From: {orig_sender}")
            print(f"    Date: {date_header}")
            print(f"    Project match: {project_key or 'NONE (non-portfolio)'}")
            print(f"    Saveable attachments: {len(saveable)}")

            if project_key and saveable:
                target_dir = PROJECTS_DIR / project_key / "pod"
                for att in saveable:
                    filepath = save_attachment(att['data'], target_dir, att['filename'], email_date)
                    saved_files.append(filepath)
                    size_kb = att['size'] / 1024
                    print(f"      SAVED: {filepath}  ({size_kb:.1f} KB)")
            elif not project_key:
                print(f"      SKIPPED: No portfolio project match for subject")
            elif not saveable:
                print(f"      SKIPPED: No saveable attachments (may be stripped by PA)")
            print()
    else:
        print("No [INBOX:] emails found in last 7 days.\n")

    mail.logout()

    # ======================================================================
    # Summary
    # ======================================================================
    print(f"\n{SEPARATOR}")
    print("SUMMARY")
    print(SEPARATOR)
    print(f"Total POD files downloaded and saved: {len(saved_files)}")
    if saved_files:
        for f in saved_files:
            print(f"  -> {f}")
    else:
        print("  No POD attachments were downloaded.")
        print("  Possible reasons:")
        print("    1. Power Automate is stripping attachments before forwarding")
        print("    2. No POD emails have been sent recently")
        print("    3. POD emails exist but don't match any portfolio project name")
    print(f"\nDone at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
