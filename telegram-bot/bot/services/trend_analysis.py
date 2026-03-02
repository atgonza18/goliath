"""
Daily Trend Analysis — Constraint movement and follow-up queue summary for morning report.

Generates two sections for the morning report:
  1. "Constraint Movement (24h)" — placeholder (constraint snapshot functionality removed;
     constraint data is now accessed live via Convex API / MCP server).
  2. "Follow-Up Queue" — what's due/overdue today from the follow-up queue.

Can also be run standalone via the scheduler or called directly by the morning report.
"""

import logging
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

from bot.config import REPO_ROOT, FOLLOWUP_DB_PATH

logger = logging.getLogger(__name__)


class TrendAnalyzer:
    """Generates trend analysis sections for the morning report."""

    # ------------------------------------------------------------------
    # Constraint movement (24h)
    # ------------------------------------------------------------------

    def get_constraint_changes_24h(self) -> dict:
        """Return constraint change data for the last 24 hours.

        NOTE: The local constraint snapshot functionality has been removed.
        Constraint data is now accessed live via the Convex API / MCP server.
        This method returns an empty structure for backward compatibility.
        """
        return {
            "new": [],
            "resolved": [],
            "status_changed": [],
            "priority_changed": [],
            "total_current": 0,
        }

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
        if total > 0:
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
