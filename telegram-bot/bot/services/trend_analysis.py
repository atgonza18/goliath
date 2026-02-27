"""
Daily Trend Analysis — Constraint movement and follow-up queue summary for morning report.

Generates two sections for the morning report:
  1. "Constraint Movement (24h)" — what changed overnight (new, resolved, status changes,
     priority changes) based on heartbeat snapshot comparison.
  2. "Follow-Up Queue" — what's due/overdue today from the follow-up queue.

Can also be run standalone via the scheduler or called directly by the morning report.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Optional

from bot.config import REPO_ROOT, FOLLOWUP_DB_PATH, HEARTBEAT_SNAPSHOT_DIR

logger = logging.getLogger(__name__)

# Snapshot paths (mirrors heartbeat.py)
SNAPSHOT_DIR = HEARTBEAT_SNAPSHOT_DIR
LATEST_SNAPSHOT_PATH = SNAPSHOT_DIR / "latest.json"
PREVIOUS_SNAPSHOT_PATH = SNAPSHOT_DIR / "previous.json"


class TrendAnalyzer:
    """Generates trend analysis sections for the morning report."""

    # ------------------------------------------------------------------
    # Constraint movement (24h) — heartbeat snapshot comparison
    # ------------------------------------------------------------------

    def get_constraint_changes_24h(self) -> dict:
        """Load heartbeat snapshots and summarize changes from the last 24 hours.

        Returns a dict with keys:
          - new: list of new constraints (HIGH/MEDIUM)
          - resolved: list of constraints that changed to resolved/closed
          - status_changed: list of other status changes
          - priority_changed: list of priority elevations
          - total_current: total constraint count in latest snapshot
          - snapshot_age_hours: how old the latest snapshot is (for staleness check)
        """
        result = {
            "new": [],
            "resolved": [],
            "status_changed": [],
            "priority_changed": [],
            "total_current": 0,
            "snapshot_age_hours": None,
        }

        # Load latest snapshot
        latest = self._load_snapshot(LATEST_SNAPSHOT_PATH)
        if latest is None:
            logger.info("Trend analysis: no latest snapshot available")
            return result

        result["total_current"] = len(latest.get("constraints", []))

        # Check snapshot age
        try:
            ts = latest.get("timestamp", "")
            snapshot_dt = datetime.fromisoformat(ts)
            age_hours = (datetime.utcnow() - snapshot_dt).total_seconds() / 3600
            result["snapshot_age_hours"] = round(age_hours, 1)
        except (ValueError, TypeError):
            pass

        # Load previous snapshot
        previous = self._load_snapshot(PREVIOUS_SNAPSHOT_PATH)
        if previous is None:
            logger.info("Trend analysis: no previous snapshot for comparison")
            return result

        # Compare
        old_constraints = previous.get("constraints", [])
        new_constraints = latest.get("constraints", [])

        old_by_id = {}
        for c in old_constraints:
            cid = c.get("id")
            if cid:
                old_by_id[cid] = c

        new_by_id = {}
        for c in new_constraints:
            cid = c.get("id")
            if cid:
                new_by_id[cid] = c

        now_str = datetime.utcnow().strftime("%Y-%m-%d")

        # Check for new constraints and changes
        for cid, constraint in new_by_id.items():
            priority = (constraint.get("priority") or "").upper()
            status = (constraint.get("status") or "").lower()
            project = constraint.get("project", "Unknown")
            desc = constraint.get("description", "")[:120]

            if cid not in old_by_id:
                # New constraint
                if priority in ("HIGH", "MEDIUM"):
                    result["new"].append({
                        "project": project,
                        "description": desc,
                        "priority": priority,
                        "owner": constraint.get("owner", "?"),
                    })
                continue

            old_c = old_by_id[cid]
            old_status = (old_c.get("status") or "").lower()
            old_priority = (old_c.get("priority") or "").upper()

            # Status changed
            if status != old_status:
                if status in ("resolved", "closed", "completed"):
                    result["resolved"].append({
                        "project": project,
                        "description": desc,
                        "old_status": old_status,
                        "new_status": status,
                    })
                else:
                    result["status_changed"].append({
                        "project": project,
                        "description": desc,
                        "old_status": old_status,
                        "new_status": status,
                    })

            # Priority elevated
            priority_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
            new_rank = priority_rank.get(priority, -1)
            old_rank = priority_rank.get(old_priority, -1)
            if new_rank != old_rank:
                result["priority_changed"].append({
                    "project": project,
                    "description": desc,
                    "old_priority": old_priority,
                    "new_priority": priority,
                    "direction": "elevated" if new_rank > old_rank else "lowered",
                })

        return result

    @staticmethod
    def _load_snapshot(path: Path) -> Optional[dict]:
        """Load a heartbeat snapshot JSON file."""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load snapshot {path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Follow-up queue summary
    # ------------------------------------------------------------------

    async def get_follow_up_summary(self) -> str:
        """Query FollowUpQueue for today's due + overdue items.

        Returns HTML-formatted summary string.
        """
        try:
            from bot.services.followup import FollowUpQueue

            queue = FollowUpQueue(FOLLOWUP_DB_PATH)
            await queue.initialize()
            summary = await queue.get_overdue_summary()
            await queue.close()
            return summary
        except Exception:
            logger.exception("Failed to get follow-up summary")
            return "<i>Follow-up queue unavailable.</i>"

    # ------------------------------------------------------------------
    # Generate combined trend section for morning report
    # ------------------------------------------------------------------

    async def generate_trend_section(self) -> str:
        """Return formatted HTML for both trend sections (constraint movement + follow-ups).

        Returns a single HTML string with two clearly labeled sections,
        ready to be inserted into the morning report.
        """
        sections = []

        # --- Section 1: Constraint Movement (24h) ---
        try:
            changes = self.get_constraint_changes_24h()
            sections.append(self._format_constraint_movement(changes))
        except Exception:
            logger.exception("Error generating constraint movement section")
            sections.append(
                "<b>Constraint Movement (24h)</b>\n"
                "<i>Unable to analyze constraint changes at this time.</i>"
            )

        # --- Section 2: Follow-Up Queue ---
        try:
            followup_summary = await self.get_follow_up_summary()
            sections.append(
                f"<b>Follow-Up Queue</b>\n"
                f"{followup_summary}"
            )
        except Exception:
            logger.exception("Error generating follow-up queue section")
            sections.append(
                "<b>Follow-Up Queue</b>\n"
                "<i>Unable to load follow-up queue at this time.</i>"
            )

        return "\n\n---\n\n".join(sections)

    def _format_constraint_movement(self, changes: dict) -> str:
        """Format the constraint changes dict as HTML."""
        lines = ["<b>Constraint Movement (24h)</b>"]

        total = changes.get("total_current", 0)
        age = changes.get("snapshot_age_hours")

        if age is not None:
            lines.append(
                f"<i>{total} total constraints tracked | "
                f"Snapshot age: {age}h</i>"
            )
        elif total > 0:
            lines.append(f"<i>{total} total constraints tracked</i>")

        new = changes.get("new", [])
        resolved = changes.get("resolved", [])
        status_changed = changes.get("status_changed", [])
        priority_changed = changes.get("priority_changed", [])

        total_changes = len(new) + len(resolved) + len(status_changed) + len(priority_changed)

        if total_changes == 0:
            lines.append("\nNo constraint changes in the last 24 hours.")
            return "\n".join(lines)

        lines.append(f"\n<b>{total_changes} change(s) detected:</b>")

        if new:
            lines.append(f"\n<b>New Constraints ({len(new)})</b>")
            for item in new[:8]:
                project = escape(item.get("project", "?"))
                desc = escape(item.get("description", "?"))
                priority = item.get("priority", "?")
                lines.append(f"  + [{priority}] {project}: {desc}")

        if resolved:
            lines.append(f"\n<b>Resolved ({len(resolved)})</b>")
            for item in resolved[:8]:
                project = escape(item.get("project", "?"))
                desc = escape(item.get("description", "?"))
                lines.append(f"  - {project}: {desc}")

        if status_changed:
            lines.append(f"\n<b>Status Changed ({len(status_changed)})</b>")
            for item in status_changed[:8]:
                project = escape(item.get("project", "?"))
                desc = escape(item.get("description", "?"))
                old_s = item.get("old_status", "?")
                new_s = item.get("new_status", "?")
                lines.append(f"  ~ {project}: {old_s} -> {new_s} — {desc}")

        if priority_changed:
            lines.append(f"\n<b>Priority Changed ({len(priority_changed)})</b>")
            for item in priority_changed[:8]:
                project = escape(item.get("project", "?"))
                desc = escape(item.get("description", "?"))
                old_p = item.get("old_priority", "?")
                new_p = item.get("new_priority", "?")
                direction = item.get("direction", "changed")
                lines.append(f"  ^ {project}: {old_p} -> {new_p} ({direction}) — {desc}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Standalone runner (also callable from scheduler)
    # ------------------------------------------------------------------

    async def run_trend_analysis(self, bot, chat_id: int) -> None:
        """Run trend analysis and send the results to Telegram.

        This can be called standalone or as part of the morning report flow.
        """
        logger.info("Trend analysis starting...")
        start = time.monotonic()

        try:
            trend_html = await self.generate_trend_section()

            if bot and chat_id:
                from bot.utils.formatting import chunk_message

                for chunk in chunk_message(trend_html, max_len=4000):
                    try:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception:
                        try:
                            await bot.send_message(chat_id=chat_id, text=chunk)
                        except Exception:
                            logger.exception("Failed to send trend analysis to Telegram")

            duration = time.monotonic() - start
            logger.info(f"Trend analysis complete in {duration:.1f}s")

        except Exception:
            logger.exception("Trend analysis failed")
