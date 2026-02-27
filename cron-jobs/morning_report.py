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

import asyncio
import json
import os
import re
import sys
import sqlite3
import requests
from datetime import datetime, timedelta
from html import escape as html_escape_fn
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
CRON_REPORTS_DIR = REPO_ROOT / "cron-jobs" / "reports"
TODO_REPORTS_DIR = REPO_ROOT / "reports"
PROJECTS_DIR = REPO_ROOT / "projects"
MEMORY_DB_PATH = REPO_ROOT / "telegram-bot" / "data" / "memory.db"

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


def get_open_action_items() -> list[dict]:
    """Query the memory DB for all open (unresolved) action items."""
    if not MEMORY_DB_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(str(MEMORY_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT id, created_at, project_key, summary, detail "
            "FROM memories WHERE category = 'action_item' AND resolved = 0 "
            "ORDER BY created_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        print(f"Warning: could not query memory DB: {e}")
        return []


def build_todo_from_db() -> str | None:
    """Build the to-do list dynamically from open action items in the memory DB."""
    items = get_open_action_items()
    if not items:
        return None

    # Group items by project (None = general/system)
    by_project: dict[str | None, list[dict]] = {}
    for item in items:
        key = item.get("project_key") or None
        by_project.setdefault(key, []).append(item)

    lines = []

    # General / system items first
    general = by_project.pop(None, [])
    if general:
        lines.append("<b>General / System</b>")
        for item in general:
            date = item["created_at"][:10]
            lines.append(f"  - [{date}] {_html_escape(item['summary'])}")
        lines.append("")

    # Project-specific items
    for proj_key in sorted(by_project.keys()):
        proj_name = PROJECTS.get(proj_key, proj_key or "Unknown")
        if isinstance(proj_name, str):
            display_name = proj_name
        else:
            display_name = proj_name  # already a string from our PROJECTS dict
        lines.append(f"<b>{_html_escape(display_name)}</b>")
        for item in by_project[proj_key]:
            date = item["created_at"][:10]
            lines.append(f"  - [{date}] {_html_escape(item['summary'])}")
        lines.append("")

    return "\n".join(lines).strip() if lines else None


def _html_escape(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


# ------------------------------------------------------------------
# Trend analysis sections (constraint movement + follow-up queue)
# ------------------------------------------------------------------

SNAPSHOT_DIR = REPO_ROOT / "data" / "constraint_snapshots"
FOLLOWUP_DB_PATH = REPO_ROOT / "telegram-bot" / "data" / "followup.db"


def get_constraint_movement_24h() -> str:
    """Load heartbeat snapshots and build an HTML summary of constraint changes."""
    latest_path = SNAPSHOT_DIR / "latest.json"
    previous_path = SNAPSHOT_DIR / "previous.json"

    if not latest_path.exists():
        return "<i>No constraint snapshots available yet.</i>"

    try:
        latest = json.loads(latest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return "<i>Unable to read latest constraint snapshot.</i>"

    total = len(latest.get("constraints", []))

    # Check snapshot age
    age_str = ""
    try:
        ts = latest.get("timestamp", "")
        snapshot_dt = datetime.fromisoformat(ts)
        age_hours = (datetime.utcnow() - snapshot_dt).total_seconds() / 3600
        age_str = f" | Snapshot age: {age_hours:.1f}h"
    except (ValueError, TypeError):
        pass

    if not previous_path.exists():
        return (
            f"<i>{total} total constraints tracked{age_str}</i>\n"
            f"No previous snapshot for comparison — first run."
        )

    try:
        previous = json.loads(previous_path.read_text())
    except (json.JSONDecodeError, OSError):
        return (
            f"<i>{total} total constraints tracked{age_str}</i>\n"
            f"Unable to read previous snapshot for comparison."
        )

    old_constraints = previous.get("constraints", [])
    new_constraints = latest.get("constraints", [])

    old_by_id = {c.get("id"): c for c in old_constraints if c.get("id")}
    new_by_id = {c.get("id"): c for c in new_constraints if c.get("id")}

    new_items = []
    resolved_items = []
    status_items = []
    priority_items = []

    priority_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    for cid, constraint in new_by_id.items():
        priority = (constraint.get("priority") or "").upper()
        status = (constraint.get("status") or "").lower()
        project = constraint.get("project", "Unknown")
        desc = constraint.get("description", "")[:120]

        if cid not in old_by_id:
            if priority in ("HIGH", "MEDIUM"):
                new_items.append(f"  + [{priority}] {_html_escape(project)}: {_html_escape(desc)}")
            continue

        old_c = old_by_id[cid]
        old_status = (old_c.get("status") or "").lower()
        old_priority = (old_c.get("priority") or "").upper()

        if status != old_status:
            if status in ("resolved", "closed", "completed"):
                resolved_items.append(f"  - {_html_escape(project)}: {_html_escape(desc)}")
            else:
                status_items.append(
                    f"  ~ {_html_escape(project)}: {old_status} -> {status} — {_html_escape(desc)}"
                )

        new_rank = priority_rank.get(priority, -1)
        old_rank = priority_rank.get(old_priority, -1)
        if new_rank != old_rank:
            direction = "elevated" if new_rank > old_rank else "lowered"
            priority_items.append(
                f"  ^ {_html_escape(project)}: {old_priority} -> {priority} ({direction}) — {_html_escape(desc)}"
            )

    total_changes = len(new_items) + len(resolved_items) + len(status_items) + len(priority_items)
    lines = [f"<i>{total} total constraints tracked{age_str}</i>"]

    if total_changes == 0:
        lines.append("\nNo constraint changes in the last 24 hours.")
        return "\n".join(lines)

    lines.append(f"\n<b>{total_changes} change(s) detected:</b>")

    if new_items:
        lines.append(f"\n<b>New Constraints ({len(new_items)})</b>")
        lines.extend(new_items[:8])
    if resolved_items:
        lines.append(f"\n<b>Resolved ({len(resolved_items)})</b>")
        lines.extend(resolved_items[:8])
    if status_items:
        lines.append(f"\n<b>Status Changed ({len(status_items)})</b>")
        lines.extend(status_items[:8])
    if priority_items:
        lines.append(f"\n<b>Priority Changed ({len(priority_items)})</b>")
        lines.extend(priority_items[:8])

    return "\n".join(lines)


def get_followup_queue_summary() -> str:
    """Query the follow-up queue DB for due/overdue items and format as HTML."""
    if not FOLLOWUP_DB_PATH.exists():
        return "<i>Follow-up queue not yet initialized.</i>"

    try:
        conn = sqlite3.connect(str(FOLLOWUP_DB_PATH))
        conn.row_factory = sqlite3.Row
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Overdue items
        cursor = conn.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date < ? AND status = 'pending' "
            "ORDER BY follow_up_date ASC",
            (today,),
        )
        overdue = [dict(row) for row in cursor.fetchall()]

        # Due today
        cursor = conn.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date = ? AND status = 'pending' "
            "ORDER BY created_at ASC",
            (today,),
        )
        due_today = [dict(row) for row in cursor.fetchall()]

        conn.close()

        if not overdue and not due_today:
            return "<i>No follow-ups due or overdue today.</i>"

        lines = []

        if overdue:
            lines.append(f"<b>Overdue ({len(overdue)})</b>")
            for item in overdue[:10]:
                project = _html_escape(str(item.get("project_key", "?")))
                commitment = _html_escape(str(item.get("commitment", ""))[:120])
                due = item.get("follow_up_date", "?")
                owner = _html_escape(str(item.get("owner", "?")))
                lines.append(
                    f"  - [{project}] {commitment}\n"
                    f"    <i>Due: {due} | Owner: {owner}</i>"
                )

        if due_today:
            lines.append(f"<b>Due Today ({len(due_today)})</b>")
            for item in due_today[:10]:
                project = _html_escape(str(item.get("project_key", "?")))
                commitment = _html_escape(str(item.get("commitment", ""))[:120])
                owner = _html_escape(str(item.get("owner", "?")))
                lines.append(
                    f"  - [{project}] {commitment}\n"
                    f"    <i>Owner: {owner}</i>"
                )

        return "\n".join(lines)

    except Exception as e:
        print(f"Warning: could not query follow-up DB: {e}")
        return "<i>Follow-up queue unavailable.</i>"


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

    # Section 1: Open Action Items (dynamic from memory DB)
    todo_content = build_todo_from_db()
    if todo_content:
        sections.append(
            f"<b>Open Action Items</b>\n"
            f"<i>{len(get_open_action_items())} items pending</i>\n\n"
            f"{todo_content}"
        )
    else:
        sections.append(
            f"<b>Open Action Items</b>\n\n"
            f"<i>No open action items. Everything is resolved.</i>"
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

    # Section 4: Constraint Movement (24h)
    try:
        constraint_movement = get_constraint_movement_24h()
        sections.append(
            f"<b>Constraint Movement (24h)</b>\n"
            f"{constraint_movement}"
        )
    except Exception as e:
        print(f"Warning: constraint movement section failed: {e}")
        sections.append(
            f"<b>Constraint Movement (24h)</b>\n"
            f"<i>Unable to analyze constraint changes.</i>"
        )

    # Section 5: Follow-Up Queue
    try:
        followup_summary = get_followup_queue_summary()
        sections.append(
            f"<b>Follow-Up Queue</b>\n"
            f"{followup_summary}"
        )
    except Exception as e:
        print(f"Warning: follow-up queue section failed: {e}")
        sections.append(
            f"<b>Follow-Up Queue</b>\n"
            f"<i>Unable to load follow-up queue.</i>"
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
