#!/usr/bin/env python3
"""
Morning Report Delivery — 8 AM CT
Crontab: 0 8 * * * cd /workspaces/goliath && /workspaces/goliath/cron-jobs/.venv/bin/python cron-jobs/morning_report.py

Finds the latest daily scan report and sends it to the DSC analyst via Telegram.
"""

import os
import sys
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "cron-jobs" / "reports"

load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "") or os.environ.get("ALLOWED_CHAT_IDS", "")

MAX_MSG_LEN = 4000  # Telegram limit is 4096, leave buffer


def get_latest_report() -> tuple[Path | None, str | None]:
    """Find the most recent daily scan report."""
    if not REPORTS_DIR.exists():
        return None, None

    reports = sorted(
        REPORTS_DIR.glob("*_daily_scan.md"),
        reverse=True,
    )

    if not reports:
        return None, None

    report_path = reports[0]
    return report_path, report_path.read_text()


def chunk_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Split message into Telegram-safe chunks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Try to split on a newline
        split_idx = text.rfind("\n", 0, max_len)
        if split_idx == -1:
            split_idx = max_len

        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip("\n")

    return chunks


def send_telegram_message(chat_id: str, text: str) -> bool:
    """Send a message via the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    chunks = chunk_message(text)
    success = True

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=30)

        if resp.status_code != 200:
            # Retry without Markdown parse mode (in case of formatting issues)
            payload.pop("parse_mode")
            resp = requests.post(url, json=payload, timeout=30)

        if resp.status_code != 200:
            print(f"Failed to send chunk {i+1}/{len(chunks)}: {resp.status_code} {resp.text}")
            success = False
        else:
            print(f"Sent chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")

    return success


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        return 1

    if not TELEGRAM_CHAT_ID:
        print("Error: REPORT_CHAT_ID (or ALLOWED_CHAT_IDS) not set in .env")
        print("Add REPORT_CHAT_ID=<your-chat-id> to /workspaces/goliath/.env")
        return 1

    # Use the first chat ID if multiple are configured
    chat_id = TELEGRAM_CHAT_ID.split(",")[0].strip()

    report_path, report_content = get_latest_report()

    if not report_path or not report_content:
        msg = "No daily scan reports found. The 6 PM scan may not have run yet."
        print(msg)
        send_telegram_message(chat_id, f"*GOLIATH Morning Report*\n\n{msg}")
        return 1

    report_date = report_path.stem.replace("_daily_scan", "")
    header = f"*GOLIATH Daily Scan — {report_date}*\n\n"

    print(f"[{datetime.now().isoformat()}] Sending report: {report_path.name}")
    print(f"Report size: {len(report_content)} chars")

    success = send_telegram_message(chat_id, header + report_content)

    if success:
        print("Report delivered successfully.")
        return 0
    else:
        print("Some chunks failed to send.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
