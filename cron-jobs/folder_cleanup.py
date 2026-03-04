#!/usr/bin/env python3
"""
Folder Cleanup Scan — 7 PM CT daily (01:00 UTC)

Runs the folder_organizer agent to scan the workspace for:
  - Duplicate files (by MD5 checksum)
  - Misplaced files (wrong project folder)
  - Scripts mixed in with report output
  - Stray files at root or unexpected locations
  - Empty project folders (no real data)
  - Oversized files

Sends a summary to Telegram. Never deletes or moves files — report only.
"""

import os
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "") or os.environ.get("ALLOWED_CHAT_IDS", "")

MAX_MSG_LEN = 4000  # Telegram limit is 4096, leave buffer

# The prompt sent to Claude CLI to act as the folder_organizer agent
SCAN_PROMPT = """\
You are the Folder Organizer agent for GOLIATH. Scan the workspace for file organization issues.

## What to Scan

1. /opt/goliath/projects/ — all 12 project folders recursively
   Projects: union-ridge, duff, salt-branch, blackford, delta-bobcat, tehuacana, \
three-rivers, scioto-ridge, mayes, graceland, pecan-prairie, duffy-bess

2. /opt/goliath/reports/ — check for scripts mixed with report output
3. /opt/goliath/dsc-constraints-production-reports/ — check for scripts mixed with output
4. /opt/goliath/ root level — check for stray data files

## Detection Methods

### DUPLICATES
Run: find /opt/goliath/projects/ -type f ! -name '.gitkeep' -exec md5sum {} + | sort | uniq -D -w32
Group by MD5 hash. Only report files with matching hashes.

### MISPLACED_FILES
Check if filenames reference a different project than their parent folder.
E.g., "blackford_schedule.pdf" in scioto-ridge/ is misplaced.

### SCRIPTS_IN_WRONG_PLACE
Look for .py, .sh files in /opt/goliath/reports/, /opt/goliath/dsc-constraints-production-reports/, \
or /opt/goliath/projects/*/ — scripts belong in /opt/goliath/scripts/ or /opt/goliath/cron-jobs/.

### STRAY_FILES
Check /opt/goliath/ root for unexpected data files (PDFs, Excel, etc.).

### EMPTY_PROJECT_FOLDERS
List projects with no real data files (only .gitkeep or empty).

### OVERSIZED_FILES
Flag files over 50 MB.

## CRITICAL: ACTIONABLE ITEMS ONLY
Only report items that actually need human action. IGNORE and DO NOT REPORT:
- Small image files (PNG, JPG, GIF) that repeat across dated folders — these are email \
header/logo images from POD processing and are harmless. Not actionable.
- .gitkeep files — these are intentional placeholders.
- Any file under 100 KB that appears duplicated across date-stamped subfolders — \
these are almost certainly email artifacts (logos, signatures, etc.)

Focus on REAL issues only:
- Duplicate spreadsheets, PDFs, or large data files (same content, wasted space)
- Files genuinely in the wrong project folder
- Scripts mixed into data/report folders
- Oversized files that shouldn't be here
- Project folders with zero data that should have data

## Output Format — PLAIN ENGLISH
Write the report in plain, human-readable English. No technical jargon. \
Describe each issue like you're telling a coworker what needs fixing.

Use EXACTLY this structure:

=== FOLDER CLEANUP REPORT ===
Scan date: <today>

--- ACTION ITEMS ---
<Numbered list of items that need attention. For each one, write:
  1. WHAT the issue is (plain English)
  2. WHERE (file paths)
  3. RECOMMENDATION (what to do about it)

If nothing needs attention, write: "All clean — no action needed."
>

--- SUMMARY ---
Action items: <count>

CRITICAL: Only report what your tools actually find. Never fabricate results. \
Only include items that genuinely need human attention.
"""


def run_folder_scan() -> str:
    """Run Claude CLI to perform the folder organization scan."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--output-format", "text",
        SCAN_PROMPT,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
        env=env,
    )

    if result.returncode != 0:
        return f"SCAN_FAILED\n{result.stderr[:2000]}"

    return result.stdout.strip() if result.stdout.strip() else "SCAN_EMPTY"


def parse_summary(report: str) -> dict:
    """Extract the SUMMARY section counts from the report."""
    counts = {"total": 0}

    total_match = re.search(r"Action items:\s*(\d+)", report)
    if total_match:
        counts["total"] = int(total_match.group(1))
    else:
        # Fallback to old format
        total_match = re.search(r"Total issues found:\s*(\d+)", report)
        if total_match:
            counts["total"] = int(total_match.group(1))

    return counts


def extract_action_items(report: str) -> str:
    """Extract the ACTION ITEMS section from the report."""
    pattern = r"---\s*ACTION ITEMS\s*---\s*\n(.*?)(?=---|\Z)"
    match = re.search(pattern, report, re.DOTALL)
    if match:
        content = match.group(1).strip()
        return content if content else "All clean — no action needed."
    # Fallback: try to extract everything between report header and summary
    return "All clean — no action needed."


def format_telegram_message(report: str, counts: dict) -> str:
    """Format the scan results as a plain-English HTML Telegram message."""
    now = datetime.now(CT)
    timestamp = now.strftime("%Y-%m-%d %H:%M CT")

    total = counts.get("total", 0)

    # If no issues found, send a short clean message
    if total == 0:
        return (
            f"<b>🧹 Goliath Cleanup Report</b>\n"
            f"<i>{timestamp}</i>\n\n"
            f"All clean — nothing needs attention.\n\n"
            f"<i>Next scan: tomorrow 7 PM CT</i>"
        )

    # Extract the plain-English action items
    action_items = extract_action_items(report)

    # Truncate if extremely long, but these should be short now
    if len(action_items) > 3000:
        action_items = action_items[:2900] + "\n\n(truncated — reply for full details)"

    msg = (
        f"<b>🧹 Goliath Cleanup Report</b>\n"
        f"<i>{timestamp}</i>\n\n"
        f"<b>{total} item{'s' if total != 1 else ''} need{'s' if total == 1 else ''} attention:</b>\n\n"
        f"{action_items}\n\n"
        f"<i>Reply to Nimrod if you want any of these cleaned up.\n"
        f"Next scan: tomorrow 7 PM CT</i>"
    )

    return msg


# ------------------------------------------------------------------
# Telegram sending (follows morning_report.py pattern)
# ------------------------------------------------------------------

def chunk_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Split message into Telegram-safe chunks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        split_idx = text.rfind("\n", 0, max_len)
        if split_idx == -1 or split_idx < max_len // 2:
            split_idx = max_len

        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip("\n")

    return chunks


def send_telegram_message(chat_id: str, text: str) -> bool:
    """Send a message via the Telegram Bot API using HTML parse mode."""
    import requests

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    chunks = chunk_message(text)
    success = True

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=30)

        if resp.status_code != 200:
            # Retry without parse mode (in case of formatting issues)
            payload.pop("parse_mode")
            resp = requests.post(url, json=payload, timeout=30)

        if resp.status_code != 200:
            print(f"Failed to send chunk {i+1}/{len(chunks)}: {resp.status_code} {resp.text}")
            success = False
        else:
            print(f"Sent chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")

    return success


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        return 1

    if not TELEGRAM_CHAT_ID:
        print("Error: REPORT_CHAT_ID (or ALLOWED_CHAT_IDS) not set in .env")
        print("Add REPORT_CHAT_ID=<your-chat-id> to .env in the repo root")
        return 1

    # Use the first chat ID if multiple are configured
    chat_id = TELEGRAM_CHAT_ID.split(",")[0].strip()

    print(f"[{datetime.now(CT).isoformat()}] Starting folder cleanup scan...")

    # Run the scan
    report = run_folder_scan()

    if report == "SCAN_EMPTY":
        print("Scan returned empty response")
        msg = (
            "<b>GOLIATH Folder Cleanup Report</b>\n"
            f"<i>{datetime.now(CT).strftime('%Y-%m-%d %H:%M CT')}</i>\n\n"
            "Scan returned empty response. Claude CLI may be unavailable."
        )
        send_telegram_message(chat_id, msg)
        return 1

    if report.startswith("SCAN_FAILED"):
        print(f"Scan failed: {report}")
        error_detail = report.replace("SCAN_FAILED\n", "")[:500]
        msg = (
            "<b>GOLIATH Folder Cleanup Report</b>\n"
            f"<i>{datetime.now(CT).strftime('%Y-%m-%d %H:%M CT')}</i>\n\n"
            f"<b>Scan failed</b>\n"
            f"<pre>{error_detail}</pre>"
        )
        send_telegram_message(chat_id, msg)
        return 1

    # Save raw report to file
    reports_dir = REPO_ROOT / "cron-jobs" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(CT).strftime("%Y-%m-%d")
    report_path = reports_dir / f"{today}_folder_cleanup.txt"
    report_path.write_text(report)
    print(f"Raw report saved: {report_path}")

    # Parse and format
    counts = parse_summary(report)
    print(f"Issues found: {counts}")

    telegram_msg = format_telegram_message(report, counts)
    print(f"Telegram message: {len(telegram_msg)} chars")

    # Send to Telegram
    success = send_telegram_message(chat_id, telegram_msg)

    if success:
        print("Folder cleanup report delivered successfully.")
        return 0
    else:
        print("Some chunks failed to send.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
