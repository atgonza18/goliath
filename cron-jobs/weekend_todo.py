#!/usr/bin/env python3
"""
Weekend To-Do List — One-time Saturday 6 AM CT delivery.

Sends a pre-written weekend to-do list to Telegram.
Scheduled via crontab: 0 6 28 2 * /usr/bin/python3 /opt/goliath/cron-jobs/weekend_todo.py
"""

import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "") or os.environ.get("ALLOWED_CHAT_IDS", "")

MAX_MSG_LEN = 4000

WEEKEND_TODO = """\
<b>\U0001f6e0\ufe0f Weekend System To-Do List \u2014 Saturday Feb 28</b>

<b>GOLIATH SYSTEM IMPROVEMENTS:</b>

1. <b>Build OAuth callback endpoint for Recall.ai</b> \u2014 Calendar auto-join is connected in the Recall.ai dashboard but NOT through our API user. Need a small OAuth flow on the webhook server so meetings auto-record without manual /join commands. This is the last piece for full automation.

2. <b>Test transcript \u2192 ConstraintsPro auto-pipeline</b> \u2014 Pipeline is built and committed but hasn\u2019t been tested end-to-end through the normal webhook flow. Monday\u2019s first call will be the real test. Review the pipeline code for any edge cases.

3. <b>Clean up stale open action items</b> \u2014 34 open items in memory, ~8-10 are already completed (bot restart, Recall.ai commit, Scioto/Delta Bobcat syncs, Blackford report). Need to resolve completed items so the morning report to-do list stays clean and accurate.

4. <b>Fix PID file management</b> \u2014 start.sh isn\u2019t writing the correct PID on restart (showed 88569 when actual was 210396). Minor but annoying for health checks.

<b>MONDAY PREP \u2014 PROJECT ITEMS TO TRACK:</b>

\u2022 <b>Delta Bobcat:</b> James Kelly owes Shoals material reconciliation (Mon/Tue 3/2-3/3). Pile caps Kyan expedite still no response (~4 weeks). Ary gathering substation/JFE details. $5M tariff escalation to Mike Flynn tracking.

\u2022 <b>Scioto Ridge:</b> Luis emailing Gomez at Stantec re: DMC connectors (HIGH priority). Substation secondary power RFI blocked \u2014 RWE owes studies to Ohio Mid (~2 month delay). DC electrical starting Mon 3/2.

\u2022 <b>Blackford:</b> Probing questions report was generated \u2014 check if the meeting happened and if follow-up emails were sent.

<i>Today is Friday Feb 27, 2026. Happy weekend boss. \U0001f3af</i>\
"""


def chunk_message(text, max_len=MAX_MSG_LEN):
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


def send_telegram_message(chat_id, text):
    """Send a message via the Telegram Bot API using HTML parse mode."""
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


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not set in .env")
        return 1

    if not TELEGRAM_CHAT_ID:
        print("Error: REPORT_CHAT_ID (or ALLOWED_CHAT_IDS) not set in .env")
        return 1

    chat_id = TELEGRAM_CHAT_ID.split(",")[0].strip()

    print(f"Sending weekend to-do list to chat_id={chat_id}...")

    success = send_telegram_message(chat_id, WEEKEND_TODO)

    if success:
        print("Weekend to-do list delivered successfully.")
        return 0
    else:
        print("Failed to send weekend to-do list.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
