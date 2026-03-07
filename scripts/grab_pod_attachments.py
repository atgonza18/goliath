#!/usr/bin/env python3
"""One-time utility: grab POD email attachments from Gmail and file them.

Connects to Gmail IMAP, searches for recent POD emails (last N days),
downloads attachments, and saves them to the correct project pod/ folders.

Only saves for portfolio projects defined in config.PROJECTS.

Usage:
    python scripts/grab_pod_attachments.py              # last 7 days
    python scripts/grab_pod_attachments.py --days 30    # last 30 days
    python scripts/grab_pod_attachments.py --dry-run    # show what would be saved

Environment:
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD must be set in .env
"""

import argparse
import email
import email.header
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
    PROJECTS, PROJECTS_DIR, CONSTRAINTS_REPORTS_DIR,
    match_project_key,
)

# Allowed attachment extensions
ALLOWED_EXTENSIONS = {
    '.pdf', '.xlsx', '.xls', '.csv', '.doc', '.docx',
    '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.zip', '.msg',
}

# Known constraints senders
CONSTRAINTS_SENDERS = ['hauger', 'hogger']


def classify_email(subject: str, sender: str, attachments: list | None = None) -> str:
    """Classify an email as pod, constraints, or normal."""
    subj = subject.lower()
    sndr = sender.lower()
    pod_patterns = (r'\bpod\b', r'plan\s+of\s+the\s+day', r'plan\s+of\s+day',
                    r'daily\s+pod', r'daily\s+plan')
    if any(re.search(p, subj) for p in pod_patterns):
        return "pod"
    # Check attachment filenames for POD indicators
    for att in (attachments or []):
        att_name = (att.get("filename") or "").lower()
        if any(re.search(p, att_name) for p in pod_patterns):
            return "pod"
    for name in CONSTRAINTS_SENDERS:
        if name in sndr:
            return "constraints"
    if "constraint" in subj:
        return "constraints"
    return "normal"


def extract_attachments(msg) -> list[dict]:
    """Extract file attachments from a MIME email."""
    attachments = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        filename = part.get_filename()
        if not filename and "attachment" not in disposition:
            continue
        if not filename:
            continue

        decoded = email.header.decode_header(filename)
        name_parts = []
        for p, ch in decoded:
            if isinstance(p, bytes):
                name_parts.append(p.decode(ch or "utf-8", errors="replace"))
            else:
                name_parts.append(p)
        filename = "".join(name_parts)

        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        try:
            data = part.get_payload(decode=True)
            if data and len(data) >= 100:
                attachments.append({"filename": filename, "data": data})
        except Exception:
            pass

    return attachments


def save_file(data: bytes, target_dir: Path, filename: str, today: str, dry_run: bool) -> Path | None:
    """Save a file to target_dir with date prefix. Returns saved path or None."""
    safe_name = re.sub(r'[^\w\-._() ]', '', filename).strip()
    if not safe_name:
        safe_name = "attachment.bin"
    if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}', safe_name):
        safe_name = f"{today}_{safe_name}"

    filepath = target_dir / safe_name
    if filepath.exists():
        stem = filepath.stem
        suffix = filepath.suffix
        counter = 1
        while filepath.exists():
            filepath = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    if dry_run:
        print(f"  [DRY RUN] Would save: {filepath}")
        return filepath

    target_dir.mkdir(parents=True, exist_ok=True)
    filepath.write_bytes(data)
    print(f"  Saved: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Grab POD/constraints attachments from Gmail")
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default: 7)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be saved without saving")
    args = parser.parse_args()

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        sys.exit(1)

    print(f"Connecting to Gmail IMAP ({GMAIL_IMAP_HOST})...")
    mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, 993, timeout=30)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("INBOX")

    # Search for emails with [INBOX: in subject from last N days
    since_date = (datetime.now() - timedelta(days=args.days)).strftime("%d-%b-%Y")
    search_query = f'(SUBJECT "[INBOX:" SINCE {since_date})'
    print(f"Searching: {search_query}")

    status, data = mail.search(None, search_query)
    if status != "OK" or not data or not data[0]:
        print("No matching emails found.")
        mail.logout()
        return

    msg_ids = data[0].split()
    print(f"Found {len(msg_ids)} emails to scan.\n")

    stats = {"pod_saved": 0, "constraints_saved": 0, "skipped_no_project": 0, "skipped_normal": 0}

    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        msg = email.message_from_bytes(msg_data[0][1])

        # Decode subject
        raw_subject = msg.get("Subject", "")
        decoded_parts = email.header.decode_header(raw_subject)
        subject_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                subject_parts.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                subject_parts.append(part)
        full_subject = " ".join(subject_parts)

        # Parse [INBOX: sender] tag
        inbox_match = re.match(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", full_subject, re.IGNORECASE)
        if not inbox_match:
            continue

        sender = inbox_match.group(1).strip()
        subject = inbox_match.group(2).strip()

        classification = classify_email(subject, sender)
        if classification == "normal":
            stats["skipped_normal"] += 1
            continue

        attachments = extract_attachments(msg)
        if not attachments:
            continue

        # Get the email date for file naming
        date_header = msg.get("Date", "")
        try:
            from email.utils import parsedate_to_datetime
            email_date = parsedate_to_datetime(date_header).strftime("%Y-%m-%d")
        except Exception:
            email_date = datetime.now().strftime("%Y-%m-%d")

        print(f"[{classification.upper()}] From: {sender}")
        print(f"  Subject: {subject}")
        print(f"  Attachments: {len(attachments)}")

        if classification == "pod":
            project_key = match_project_key(subject)
            if not project_key:
                print(f"  SKIPPED — no portfolio project match")
                stats["skipped_no_project"] += 1
                continue

            target_dir = PROJECTS_DIR / project_key / "pod"
            for att in attachments:
                path = save_file(att["data"], target_dir, att["filename"], email_date, args.dry_run)
                if path:
                    stats["pod_saved"] += 1

        elif classification == "constraints":
            central_dir = CONSTRAINTS_REPORTS_DIR / email_date
            for att in attachments:
                save_file(att["data"], central_dir, att["filename"], email_date, args.dry_run)
                stats["constraints_saved"] += 1

                # Also try per-project
                pk = match_project_key(att["filename"]) or match_project_key(subject)
                if pk:
                    proj_dir = PROJECTS_DIR / pk / "constraints"
                    save_file(att["data"], proj_dir, att["filename"], email_date, args.dry_run)

        print()

    mail.logout()

    print("=" * 50)
    print(f"POD files saved:          {stats['pod_saved']}")
    print(f"Constraints files saved:  {stats['constraints_saved']}")
    print(f"Skipped (no project):     {stats['skipped_no_project']}")
    print(f"Skipped (normal email):   {stats['skipped_normal']}")
    print("Done.")


if __name__ == "__main__":
    main()
