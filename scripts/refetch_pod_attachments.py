#!/usr/bin/env python3
"""
Re-fetch clean POD attachments from Gmail IMAP.

Connects to Gmail, finds all POD emails from the last N days, re-downloads
the attachments with proper binary handling, validates them, and replaces
corrupted files in projects/*/pod/.

Usage:
    python3 scripts/refetch_pod_attachments.py              # last 30 days
    python3 scripts/refetch_pod_attachments.py --days 7     # last 7 days
    python3 scripts/refetch_pod_attachments.py --dry-run    # preview only
"""

import argparse
import email
import email.header
import email.policy
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))

from bot.config import (
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST,
    PROJECTS, PROJECTS_DIR, match_project_key,
)

ALLOWED_EXTENSIONS = {'.pdf', '.xlsx', '.xls', '.csv', '.doc', '.docx'}
INBOX_TAG = re.compile(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", re.IGNORECASE)
POD_PATTERNS = (r'\bpod\b', r'plan\s+of\s+the\s+day', r'plan\s+of\s+day',
                r'daily\s+pod', r'daily\s+plan')


def is_pod_email(subject: str, attachments: list[dict]) -> bool:
    subj = subject.lower()
    if any(re.search(p, subj) for p in POD_PATTERNS):
        return True
    for att in attachments:
        if any(re.search(p, att["filename"].lower()) for p in POD_PATTERNS):
            return True
    return False


def validate_pdf(data: bytes) -> tuple[bytes, str]:
    """Validate PDF data. Returns (clean_data, status).
    status: 'clean' | 'repaired' | 'corrupted'
    """
    if not data:
        return data, "corrupted"

    # Strip null prefix if present
    if data[:4] == b"null":
        data = data[4:]

    # Check for valid PDF magic
    if data[:5] == b"%PDF-":
        replacement_count = data.count(b"\xef\xbf\xbd")
        if replacement_count > 50:
            return data, "corrupted"
        return data, "clean"

    # Not a valid PDF
    replacement_count = data.count(b"\xef\xbf\xbd")
    if replacement_count > 50:
        return data, "corrupted"

    return data, "clean"


def extract_attachments(msg) -> list[dict]:
    """Extract attachments from MIME message."""
    attachments = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue

        decoded = email.header.decode_header(filename)
        parts = []
        for p, ch in decoded:
            if isinstance(p, bytes):
                parts.append(p.decode(ch or "utf-8", errors="replace"))
            else:
                parts.append(p)
        filename = "".join(parts)

        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        data = part.get_payload(decode=True)
        if data and len(data) >= 100:
            attachments.append({"filename": filename, "data": data})

    return attachments


def find_existing_corrupted(project_key: str) -> list[Path]:
    """Find corrupted POD files for a project."""
    pod_dir = PROJECTS_DIR / project_key / "pod"
    if not pod_dir.is_dir():
        return []

    corrupted = []
    for f in pod_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix == ".corrupted":
            corrupted.append(f)
            continue
        if f.suffix.lower() == ".pdf":
            try:
                header = f.read_bytes()[:20]
                if header[:4] == b"null" or header.count(b"\xef\xbf\xbd") > 3:
                    corrupted.append(f)
            except Exception:
                pass

    return corrupted


def main():
    parser = argparse.ArgumentParser(description="Re-fetch clean POD attachments from Gmail")
    parser.add_argument("--days", type=int, default=30, help="Look back N days (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD required")
        sys.exit(1)

    # First, catalog existing corrupted files
    print("Scanning for corrupted on-disk POD files...")
    corrupted_by_project: dict[str, list[Path]] = {}
    total_corrupted = 0
    for key in PROJECTS:
        files = find_existing_corrupted(key)
        if files:
            corrupted_by_project[key] = files
            total_corrupted += len(files)
            print(f"  {key}: {len(files)} corrupted files")

    if total_corrupted == 0:
        print("  No corrupted files found on disk.")
    else:
        print(f"  Total corrupted: {total_corrupted}")

    # Connect to Gmail
    print(f"\nConnecting to Gmail IMAP ({GMAIL_IMAP_HOST})...")
    mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, 993, timeout=30)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    mail.select("INBOX", readonly=True)

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

    stats = {"replaced": 0, "new_saved": 0, "skipped_clean": 0, "still_corrupted": 0}

    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue

        msg = email.message_from_bytes(msg_data[0][1], policy=email.policy.default)

        full_subject = msg["subject"] or ""
        inbox_match = INBOX_TAG.match(full_subject)
        if not inbox_match:
            continue

        sender = inbox_match.group(1).strip()
        subject = inbox_match.group(2).strip()

        attachments = extract_attachments(msg)
        if not attachments:
            continue

        if not is_pod_email(subject, attachments):
            continue

        # Match project
        project_key = match_project_key(subject)
        if not project_key:
            for att in attachments:
                project_key = match_project_key(att["filename"])
                if project_key:
                    break
        if not project_key:
            continue

        # Get email date
        date_header = msg["date"] or ""
        try:
            from email.utils import parsedate_to_datetime
            email_date = parsedate_to_datetime(date_header).strftime("%Y-%m-%d")
        except Exception:
            email_date = datetime.now().strftime("%Y-%m-%d")

        for att in attachments:
            filename = att["filename"]
            data_bytes = att["data"]

            clean_data, status_str = validate_pdf(data_bytes)

            if status_str == "corrupted":
                print(f"  STILL CORRUPTED in email: {filename} ({project_key})")
                stats["still_corrupted"] += 1
                continue

            # Build the expected on-disk filename
            safe_name = re.sub(r'[^\w\-._() ]', '', filename).strip()
            if not safe_name:
                safe_name = "attachment.pdf"
            if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}', safe_name):
                safe_name = f"{email_date}_{safe_name}"

            target_dir = PROJECTS_DIR / project_key / "pod"
            target_path = target_dir / safe_name

            # Check if there's a corrupted version to replace
            existing_corrupted = None
            if project_key in corrupted_by_project:
                for cp in corrupted_by_project[project_key]:
                    cp_name = cp.name.replace(".corrupted", "")
                    if cp_name == safe_name or safe_name in cp_name:
                        existing_corrupted = cp
                        break

            if existing_corrupted:
                if args.dry_run:
                    print(f"  [DRY RUN] Would replace: {existing_corrupted} -> clean version")
                else:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    # Remove corrupted file
                    existing_corrupted.unlink()
                    # Save clean version
                    clean_path = target_dir / safe_name
                    clean_path.write_bytes(clean_data)
                    print(f"  REPLACED: {existing_corrupted.name} -> {clean_path.name} ({len(clean_data):,} bytes)")
                stats["replaced"] += 1
                # Remove from tracking list
                corrupted_by_project[project_key].remove(existing_corrupted)
            elif target_path.exists():
                # Check if existing file is already clean
                existing_header = target_path.read_bytes()[:5]
                if existing_header == b"%PDF-":
                    stats["skipped_clean"] += 1
                    continue
                # Existing file is corrupted but didn't match by name — overwrite
                if args.dry_run:
                    print(f"  [DRY RUN] Would overwrite corrupted: {target_path}")
                else:
                    target_path.write_bytes(clean_data)
                    print(f"  OVERWRITTEN: {target_path.name} ({len(clean_data):,} bytes)")
                stats["replaced"] += 1
            else:
                # New file
                if args.dry_run:
                    print(f"  [DRY RUN] Would save new: {target_path}")
                else:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target_path.write_bytes(clean_data)
                    print(f"  NEW: {target_path.name} ({len(clean_data):,} bytes)")
                stats["new_saved"] += 1

    mail.logout()

    # Clean up leftover .corrupted files that weren't replaced
    leftover = 0
    for key, files in corrupted_by_project.items():
        for f in files:
            if f.suffix == ".corrupted":
                leftover += 1

    print("\n" + "=" * 60)
    print(f"Replaced corrupted:  {stats['replaced']}")
    print(f"New files saved:     {stats['new_saved']}")
    print(f"Skipped (clean):     {stats['skipped_clean']}")
    print(f"Still corrupted:     {stats['still_corrupted']}")
    if leftover:
        print(f"Leftover .corrupted: {leftover} (no clean source found in email)")
    print("Done.")


if __name__ == "__main__":
    main()
