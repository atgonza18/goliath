"""
Email Reply Log — Persistent record of email replies matched to constraints.

This module provides a shared reply log that bridges two systems:

  1. email_reply_monitor.py — WRITES to the log when it detects an email reply
     that matches (or likely relates to) a constraint.

  2. proactive_followup.py / morning_report.py / generate_followup_pdf.py —
     READ from the log before generating follow-up drafts. If a reply was
     received recently for a constraint, the follow-up draft is annotated
     with a "Reply received" banner instead of being generated blindly.

The log is stored as a JSON file at DATA_DIR/email_reply_log.json. Entries
older than REPLY_LOG_RETENTION_HOURS are auto-pruned on every write.

Each entry:
    {
        "timestamp": "2026-03-02T16:30:00",
        "sender": "patrick.root@example.com",
        "sender_name": "Patrick Root",
        "project_key": "tehuacana",
        "project_name": "Tehuacana",
        "constraint_id": "abc123",
        "constraint_desc": "PD-10 GPS install status",
        "signal_type": "delivery_confirmed",
        "reply_summary": "RDO arrived on site, 4 of 6 units GPS-operational...",
        "confidence": 0.85,
        "subject": "Re: Tehuacana — PD-10 GPS Install Status"
    }
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import (
    REPLY_LOG_PATH,
    REPLY_LOG_RETENTION_HOURS,
    REPLY_LOG_AWARENESS_HOURS,
    PROJECTS,
)

CT = ZoneInfo("America/Chicago")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Write side — called by email_reply_monitor
# ---------------------------------------------------------------------------

def log_reply(
    *,
    sender: str,
    sender_name: str = "",
    project_key: str = "",
    project_name: str = "",
    constraint_id: str = "",
    constraint_desc: str = "",
    signal_type: str = "",
    reply_summary: str = "",
    confidence: float = 0.0,
    subject: str = "",
) -> None:
    """Append a detected email reply to the persistent reply log.

    This is called from email_reply_monitor.process_reply() whenever
    a reply is matched (at any confidence level) to a constraint.

    Auto-prunes entries older than REPLY_LOG_RETENTION_HOURS on each write.
    """
    now = datetime.now(CT)
    entry = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "sender": sender,
        "sender_name": sender_name,
        "project_key": project_key,
        "project_name": project_name or _project_display_name(project_key),
        "constraint_id": constraint_id,
        "constraint_desc": constraint_desc[:200] if constraint_desc else "",
        "signal_type": signal_type,
        "reply_summary": reply_summary[:500] if reply_summary else "",
        "confidence": round(confidence, 3),
        "subject": subject[:200] if subject else "",
    }

    entries = _read_log()
    entries.append(entry)

    # Prune old entries
    cutoff = now - timedelta(hours=REPLY_LOG_RETENTION_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    entries = [e for e in entries if e.get("timestamp", "") >= cutoff_str]

    _write_log(entries)
    logger.info(
        f"Reply logged: project={project_key}, constraint={constraint_id[:8] if constraint_id else '?'}, "
        f"sender={sender}, signal={signal_type}"
    )


# ---------------------------------------------------------------------------
# Read side — called by follow-up generators
# ---------------------------------------------------------------------------

def get_recent_replies(
    hours: Optional[int] = None,
) -> list[dict]:
    """Return all reply log entries within the awareness window.

    Args:
        hours: Override the default awareness window (REPLY_LOG_AWARENESS_HOURS).

    Returns:
        List of reply log entries, most recent first.
    """
    window = hours if hours is not None else REPLY_LOG_AWARENESS_HOURS
    cutoff = datetime.now(CT) - timedelta(hours=window)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

    entries = _read_log()
    recent = [e for e in entries if e.get("timestamp", "") >= cutoff_str]
    recent.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return recent


def get_replies_for_constraint(constraint_id: str, hours: Optional[int] = None) -> list[dict]:
    """Return recent replies that match a specific constraint ID.

    Args:
        constraint_id: The constraint ID to look up.
        hours: Override the awareness window.

    Returns:
        List of matching reply entries, most recent first.
    """
    if not constraint_id:
        return []
    recent = get_recent_replies(hours=hours)
    return [e for e in recent if e.get("constraint_id") == constraint_id]


def get_replies_for_project(project_key: str, hours: Optional[int] = None) -> list[dict]:
    """Return recent replies that match a specific project key.

    Args:
        project_key: The project key to look up (e.g., 'tehuacana').
        hours: Override the awareness window.

    Returns:
        List of matching reply entries, most recent first.
    """
    if not project_key:
        return []
    recent = get_recent_replies(hours=hours)
    return [e for e in recent if e.get("project_key") == project_key]


def build_reply_lookup(hours: Optional[int] = None) -> dict[str, list[dict]]:
    """Build a dict mapping constraint_id -> list of recent replies.

    This is the efficient way for batch consumers (like proactive_followup.py)
    to check reply status for many constraints at once — call this ONCE, then
    do O(1) lookups per constraint.

    Returns:
        Dict mapping constraint_id -> list of reply entries (most recent first).
    """
    recent = get_recent_replies(hours=hours)
    lookup: dict[str, list[dict]] = {}
    for entry in recent:
        cid = entry.get("constraint_id", "")
        if cid:
            lookup.setdefault(cid, []).append(entry)
    return lookup


def format_reply_banner(reply: dict) -> str:
    """Format a single reply entry as a human-readable banner line.

    Used by PDF/Markdown generators to annotate follow-up drafts.

    Returns something like:
        "Reply received 2026-03-02 from Patrick Root — RDO arrived on site, 4 of 6 units GPS-operational"
    """
    ts = reply.get("timestamp", "")
    date_str = ts[:10] if ts else "unknown date"
    sender = reply.get("sender_name") or reply.get("sender", "unknown")
    summary = reply.get("reply_summary", "")

    parts = [f"Reply received {date_str} from {sender}"]
    if summary:
        # Truncate for display but include the gist
        parts.append(f"-- {summary[:150]}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Internal file I/O
# ---------------------------------------------------------------------------

def _read_log() -> list[dict]:
    """Read the reply log JSON file. Returns empty list if missing/corrupt."""
    try:
        if REPLY_LOG_PATH.exists():
            text = REPLY_LOG_PATH.read_text(encoding="utf-8")
            if text.strip():
                data = json.loads(text)
                if isinstance(data, list):
                    return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read reply log ({REPLY_LOG_PATH}): {e}")
    return []


def _write_log(entries: list[dict]) -> None:
    """Write the reply log JSON file atomically."""
    try:
        REPLY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = REPLY_LOG_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(REPLY_LOG_PATH)
    except OSError as e:
        logger.error(f"Could not write reply log ({REPLY_LOG_PATH}): {e}")


def _project_display_name(project_key: str) -> str:
    """Look up the display name for a project key."""
    info = PROJECTS.get(project_key)
    if info and isinstance(info, dict):
        return info.get("name", project_key)
    return project_key
