#!/usr/bin/env python3
"""
Morning Report Delivery — 6 AM CT
Can be run standalone or via the bot's internal scheduler.

Sends an enhanced morning report to Telegram including:
  1. Daily to-do list (from /opt/goliath/reports/)
  2. Project health summary (file counts per POD/Schedule/Constraints)
  3. Latest daily scan report (converted from Markdown to HTML)

Uses HTML formatting (NOT Markdown) for Telegram parse_mode.
"""

import os
import re
import sys
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
CRON_REPORTS_DIR = REPO_ROOT / "cron-jobs" / "reports"
TODO_REPORTS_DIR = REPO_ROOT / "reports"
PROJECTS_DIR = REPO_ROOT / "projects"

load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "") or os.environ.get("ALLOWED_CHAT_IDS", "")

MAX_MSG_LEN = 4000  # Telegram limit is 4096, leave buffer

# Project registry (mirrors bot/config.py)
PROJECTS = {
    "union-ridge":   "Union Ridge",
    "duff":          "Duff",
    "salt-branch":   "Salt Branch",
    "blackford":     "Blackford",
    "delta-bobcat":  "Delta Bobcat",
    "tehuacana":     "Tehuacana",
    "three-rivers":  "Three Rivers",
    "scioto-ridge":  "Scioto Ridge",
    "mayes":         "Mayes",
    "graceland":     "Graceland",
    "pecan-prairie": "Pecan Prairie",
    "duffy-bess":    "Duffy BESS",
}


# ------------------------------------------------------------------
# Markdown -> HTML conversion
# ------------------------------------------------------------------

def markdown_to_html(text: str) -> str:
    """Convert basic Markdown to Telegram-compatible HTML.

    Handles headings, bold, italic, inline code, and code blocks.
    Intentionally simple -- not a full parser.
    """
    # Code blocks: ```lang\n...\n``` -> <pre>...</pre>
    text = re.sub(r"```(?:\w+)?\n(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)

    # Inline code: `text` -> <code>text</code>
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Headings: # Title -> <b>Title</b>
    text = re.sub(r"^#{1,4}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Bold: **text** -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Italic: *text* -> <i>text</i>
    text = re.sub(r"(?<![</>])\*(?!\*)(.+?)(?<!\*)\*(?![*</>])", r"<i>\1</i>", text)

    # Links: [text](url) -> text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)

    return text


# ------------------------------------------------------------------
# Report sections
# ------------------------------------------------------------------

def get_latest_scan_report() -> tuple[Path | None, str | None]:
    """Find the most recent daily scan report."""
    if not CRON_REPORTS_DIR.exists():
        return None, None

    reports = sorted(CRON_REPORTS_DIR.glob("*_daily_scan.md"), reverse=True)
    if not reports:
        return None, None

    report_path = reports[0]
    try:
        return report_path, report_path.read_text(errors="replace")
    except Exception:
        return None, None


def get_latest_todo() -> str | None:
    """Find the most recent daily todo file from /opt/goliath/reports/."""
    if not TODO_REPORTS_DIR.exists():
        return None

    todo_files = []
    for pattern in ["*todo*", "*to-do*", "*TODO*"]:
        todo_files.extend(TODO_REPORTS_DIR.glob(pattern))

    if not todo_files:
        return None

    # Sort by modification time, most recent first
    todo_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    try:
        content = todo_files[0].read_text(errors="replace").strip()
        return content if content else None
    except Exception:
        return None


def build_project_health_summary() -> str:
    """Build a quick health summary by scanning project data directories."""
    lines = []

    for slug, name in PROJECTS.items():
        project_path = PROJECTS_DIR / slug
        if not project_path.exists():
            lines.append(f"  <code>{name}</code> -- <i>no folder</i>")
            continue

        status_parts = []
        for folder in ["pod", "schedule", "constraints"]:
            folder_path = project_path / folder
            if folder_path.exists():
                files = [
                    f for f in folder_path.rglob("*")
                    if f.is_file() and f.name != ".gitkeep"
                ]
                if files:
                    status_parts.append(f"{folder}: {len(files)}")

        if status_parts:
            lines.append(f"  <code>{name}</code> -- {', '.join(status_parts)}")
        else:
            lines.append(f"  <code>{name}</code> -- <i>awaiting data</i>")

    return "\n".join(lines)


def build_morning_report() -> str:
    """Assemble the full morning report in HTML format."""
    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")

    sections = []

    # Header
    sections.append(
        f"<b>GOLIATH Morning Report</b>\n"
        f"<i>{date_str}</i>"
    )

    # Section 1: Daily To-Do List
    todo_content = get_latest_todo()
    if todo_content:
        todo_html = markdown_to_html(todo_content)
        sections.append(
            f"<b>Daily To-Do List</b>\n\n"
            f"{todo_html}"
        )
    else:
        sections.append(
            f"<b>Daily To-Do List</b>\n\n"
            f"<i>No todo file found in reports/. Ask Nimrod to generate one.</i>"
        )

    # Section 2: Project Health Summary
    health = build_project_health_summary()
    sections.append(
        f"<b>Portfolio Health Summary</b>\n"
        f"<i>{len(PROJECTS)} projects tracked</i>\n\n"
        f"{health}"
    )

    # Section 3: Latest Daily Scan
    report_path, scan_content = get_latest_scan_report()
    if scan_content:
        scan_html = markdown_to_html(scan_content)
        # Truncate if very long to stay within Telegram limits
        if len(scan_html) > 3000:
            scan_html = scan_html[:2900] + (
                "\n\n<i>... (truncated -- full report in cron-jobs/reports/)</i>"
            )
        report_date = report_path.stem.replace("_daily_scan", "") if report_path else "unknown"
        sections.append(
            f"<b>Latest Daily Scan ({report_date})</b>\n\n"
            f"{scan_html}"
        )
    else:
        sections.append(
            f"<b>Latest Daily Scan</b>\n\n"
            f"<i>No scan reports found yet. The nightly scan may not have run.</i>"
        )

    # Footer
    sections.append(
        "<i>Generated by GOLIATH Scheduler</i>"
    )

    return "\n\n---\n\n".join(sections)


# ------------------------------------------------------------------
# Telegram sending
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

    print(f"[{datetime.now().isoformat()}] Building morning report...")

    report = build_morning_report()

    print(f"Report size: {len(report)} chars")

    success = send_telegram_message(chat_id, report)

    if success:
        print("Morning report delivered successfully.")
        return 0
    else:
        print("Some chunks failed to send.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
