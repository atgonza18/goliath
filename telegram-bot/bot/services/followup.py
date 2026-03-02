"""
Auto Follow-Up Queue — Tracks commitments and approaching constraint deadlines.

SCHEDULED SENDS DISABLED (2026-03-01):
  The scheduled scans (10 AM and 4 PM CT) that called run_follow_up_scan() and
  sent INDIVIDUAL Telegram messages via send_for_approval() have been disabled
  in scheduler.py. This was sending 30+ separate messages per run — pure spam.

  The follow-up system is now consolidated into ONE end-of-day PDF report via
  proactive_followup.py (fires at 5 PM CT Mon-Fri, 4:15 PM Sunday). That PDF
  includes both constraint-based follow-ups AND commitment-based items from
  this module's database.

WHAT'S STILL ACTIVE IN THIS MODULE:
  - The SQLite schema and DB (follow_ups table) — still used for tracking
  - get_overdue_summary() — still called by the morning report
  - scan_for_follow_ups() — called by proactive_followup.py to pull commitment items
  - create_follow_up() / check_due_follow_ups() — data layer still works
  - mark_completed() / mark_rejected() — still available for manual use

WHAT'S DISABLED:
  - run_follow_up_scan() — no longer called by the scheduler (was the spam source)
  - send_for_approval() — no longer called (sent individual Telegram messages)
  - generate_follow_up_draft() — no longer called (drafts now in the PDF)

If we want to repurpose this module later for commitment tracking, the data
layer is intact. Just don't re-enable the scheduled scans without removing
the individual Telegram message sends.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

import aiosqlite

from bot.config import REPO_ROOT, MEMORY_DB_PATH, FOLLOWUP_HORIZON_HOURS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite schema for follow-up queue
# ---------------------------------------------------------------------------

FOLLOWUP_SCHEMA = """
CREATE TABLE IF NOT EXISTS follow_ups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    constraint_id   TEXT,
    project_key     TEXT,
    owner           TEXT,
    commitment      TEXT,
    committed_date  TEXT,
    follow_up_date  TEXT,
    status          TEXT DEFAULT 'pending',
    reminder_sent   INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_followup_status ON follow_ups(status);
CREATE INDEX IF NOT EXISTS idx_followup_date ON follow_ups(follow_up_date);
CREATE INDEX IF NOT EXISTS idx_followup_constraint ON follow_ups(constraint_id);
"""


# ---------------------------------------------------------------------------
# Follow-up message draft prompt
# ---------------------------------------------------------------------------

FOLLOWUP_DRAFT_PROMPT = """\
Draft a short, professional follow-up message for a construction project commitment.

Details:
- Project: {project}
- Owner/Responsible: {owner}
- Commitment: {commitment}
- Committed date: {committed_date}
- Follow-up date: {follow_up_date}
- Days since commitment: {days_since}
- Status: {status_label}

Tone: Professional but friendly. This is a check-in, not a confrontation.
Style: "Hi [name], checking in on [commitment] from [date]..." /
"Wanted to follow up on [commitment] — are we on track?"

Write ONLY the message body. Start with a greeting (Hi [name],) and end with a signoff
(Thanks, Aaron). Use plain text, no HTML/markdown. Keep it concise — 3-5 sentences.
Be specific about the commitment and mention the project name.
"""


# ---------------------------------------------------------------------------
# FollowUpQueue — core service
# ---------------------------------------------------------------------------

class FollowUpQueue:
    """Manages follow-up queue in SQLite and drives the follow-up workflow."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open the SQLite DB and create the follow_ups table if needed."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(FOLLOWUP_SCHEMA)
        await self._db.commit()
        logger.info(f"Follow-up queue initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Scan memory for recent action items that need follow-up
    # ------------------------------------------------------------------

    async def scan_for_follow_ups(self) -> list[dict]:
        """Query memory for recent action_items and constraints approaching need-by dates.

        Returns a list of items that should get follow-ups queued.
        """
        items = []

        # --- Part 1: Unresolved action items from memory ---
        try:
            items.extend(await self._scan_action_items())
        except Exception:
            logger.exception("Error scanning action items from memory")

        # --- Part 2: Constraints approaching need-by dates ---
        try:
            items.extend(await self._scan_approaching_constraints())
        except Exception:
            logger.exception("Error scanning approaching constraints")

        return items

    async def _scan_action_items(self) -> list[dict]:
        """Pull unresolved action items from memory that don't already have follow-ups."""
        from bot.memory.store import MemoryStore

        memory = MemoryStore(MEMORY_DB_PATH)
        await memory.initialize()

        action_items = await memory.get_action_items(resolved=False)
        await memory.close()

        if hasattr(action_items, "success") and not action_items.success:
            logger.warning(f"Action items scan degraded: {action_items.error}")

        results = []
        for item in action_items:
            # Skip if we already have a pending follow-up for this
            existing = await self._db.execute(
                "SELECT id FROM follow_ups WHERE commitment = ? AND status = 'pending'",
                (item.summary,),
            )
            if await existing.fetchone():
                continue

            # Determine follow-up date: 2 days after the action item was created
            try:
                created = datetime.fromisoformat(item.created_at[:19])
                follow_up_date = (created + timedelta(days=2)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                follow_up_date = (datetime.now(CT) + timedelta(days=2)).strftime("%Y-%m-%d")

            results.append({
                "type": "action_item",
                "constraint_id": None,
                "project_key": item.project_key,
                "owner": "Unknown",
                "commitment": item.summary,
                "committed_date": item.created_at[:10] if item.created_at else "",
                "follow_up_date": follow_up_date,
            })

        return results

    async def _scan_approaching_constraints(self) -> list[dict]:
        """Pull constraints with need-by dates within the horizon window."""
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import SubagentRunner

        horizon_date = (datetime.now(CT) + timedelta(hours=FOLLOWUP_HORIZON_HOURS)).strftime("%Y-%m-%d")
        today = datetime.now(CT).strftime("%Y-%m-%d")

        prompt = (
            f"Pull all OPEN constraints from ConstraintsPro that have a need-by date "
            f"between today ({today}) and {horizon_date} (inclusive).\n\n"
            "Steps:\n"
            "1. Call projects_list to get all project IDs\n"
            "2. For each project, call constraints_list_by_project\n"
            "3. Filter to only OPEN constraints with need-by dates in the window above\n\n"
            "Return the results as a JSON array. Each object must have these exact fields:\n"
            '  {"id": "...", "project": "Project Name", "description": "...", '
            '"owner": "...", "priority": "HIGH|MEDIUM|LOW", "status": "open", '
            '"need_by_date": "YYYY-MM-DD"}\n\n'
            "Output ONLY the JSON array, no other text. If no constraints match, output [].\n"
            "Wrap the JSON in ```json ... ``` code fences."
        )

        runner = SubagentRunner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=300,
        )

        if not result.success or not result.output:
            logger.error(f"Follow-up constraint scan failed: {result.error}")
            return []

        constraints = self._parse_constraint_json(result.output)
        results = []

        for c in constraints:
            cid = c.get("id", "")
            if not cid:
                continue

            # Skip if we already have a pending follow-up for this constraint
            existing = await self._db.execute(
                "SELECT id FROM follow_ups WHERE constraint_id = ? AND status = 'pending'",
                (cid,),
            )
            if await existing.fetchone():
                continue

            results.append({
                "type": "constraint_approaching",
                "constraint_id": cid,
                "project_key": c.get("project", ""),
                "owner": c.get("owner", "Unknown"),
                "commitment": c.get("description", ""),
                "committed_date": today,
                "follow_up_date": c.get("need_by_date", today),
            })

        return results

    @staticmethod
    def _parse_constraint_json(raw: str) -> list[dict]:
        """Extract a JSON array from Claude's output (may be wrapped in code fences)."""
        import re

        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else raw.strip()

        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            logger.warning("No JSON array found in follow-up constraint output")
            return []

        try:
            data = json.loads(text[start:end + 1])
            if not isinstance(data, list):
                return []
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse follow-up constraint JSON: {e}")
            return []

    # ------------------------------------------------------------------
    # Create a follow-up entry
    # ------------------------------------------------------------------

    async def create_follow_up(
        self,
        constraint_id: Optional[str],
        project_key: str,
        owner: str,
        commitment: str,
        committed_date: str,
        follow_up_date: str,
    ) -> int:
        """Add a new follow-up item to the queue. Returns the row ID."""
        cursor = await self._db.execute(
            """
            INSERT INTO follow_ups
                (constraint_id, project_key, owner, commitment,
                 committed_date, follow_up_date, status, reminder_sent)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 0)
            """,
            (constraint_id, project_key, owner, commitment,
             committed_date, follow_up_date),
        )
        await self._db.commit()
        row_id = cursor.lastrowid
        logger.info(
            f"Follow-up created (id={row_id}): {commitment[:80]} "
            f"due {follow_up_date}"
        )
        return row_id

    # ------------------------------------------------------------------
    # Check for due follow-ups
    # ------------------------------------------------------------------

    async def check_due_follow_ups(self) -> list[dict]:
        """Find items where follow_up_date <= today and status is 'pending'."""
        today = datetime.now(CT).strftime("%Y-%m-%d")
        cursor = await self._db.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date <= ? AND status = 'pending'",
            (today,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Generate follow-up draft via Claude
    # ------------------------------------------------------------------

    async def generate_follow_up_draft(self, item: dict) -> Optional[str]:
        """Use Claude to draft a follow-up message for a queued item."""
        from bot.agents.definitions import NIMROD
        from bot.agents.runner import SubagentRunner

        committed_date = item.get("committed_date", "")
        follow_up_date = item.get("follow_up_date", "")
        today = datetime.now(CT).strftime("%Y-%m-%d")

        # Calculate days since commitment
        try:
            committed_dt = datetime.strptime(committed_date, "%Y-%m-%d")
            days_since = (datetime.now(CT) - committed_dt).days
        except (ValueError, TypeError):
            days_since = "?"

        # Determine status label
        if follow_up_date and follow_up_date < today:
            status_label = f"OVERDUE (was due {follow_up_date})"
        else:
            status_label = f"Due today ({follow_up_date})"

        ctx = {
            "project": item.get("project_key", "Unknown Project"),
            "owner": item.get("owner", "Unassigned"),
            "commitment": item.get("commitment", "No description"),
            "committed_date": committed_date or "Unknown",
            "follow_up_date": follow_up_date or "Not specified",
            "days_since": days_since,
            "status_label": status_label,
        }

        prompt = FOLLOWUP_DRAFT_PROMPT.format(**ctx)

        runner = SubagentRunner()
        result = await runner.run(
            agent=NIMROD,
            task_prompt=prompt,
            timeout=120,
            no_tools=True,
        )

        if result.success and result.output:
            import re
            text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", result.output, flags=re.DOTALL)
            return text.strip()

        logger.error(f"Failed to draft follow-up: {result.error}")
        return None

    # ------------------------------------------------------------------
    # Send follow-up reminder to Telegram (no approve/reject buttons)
    # ------------------------------------------------------------------

    async def send_for_approval(
        self, bot, chat_id: int, item: dict, draft: str
    ) -> None:
        """Send a follow-up reminder to Telegram.

        DEPRECATED (2026-03-01): This method is no longer called by the scheduler.
        It sent individual Telegram messages per follow-up item, which resulted in
        30+ messages of spam. Follow-ups are now consolidated into a single PDF
        report via proactive_followup.py.

        Kept for backward compatibility in case manual triggering is needed.
        """
        fu_id = item.get("id", "?")
        project = escape(str(item.get("project_key", "Unknown")))
        commitment = escape(str(item.get("commitment", ""))[:200])
        owner = escape(str(item.get("owner", "Unassigned")))
        follow_up_date = item.get("follow_up_date", "?")
        committed_date = item.get("committed_date", "?")

        today = datetime.now(CT).strftime("%Y-%m-%d")
        overdue_tag = " <b>[OVERDUE]</b>" if follow_up_date < today else ""

        # Truncate draft for display
        draft_display = escape(draft[:2800])

        text = (
            f"<b>Follow-Up Reminder</b>{overdue_tag}\n"
            f"<b>Project:</b> {project}\n"
            f"<b>Owner:</b> {owner}\n"
            f"<b>Commitment:</b> {commitment}\n"
            f"<b>Committed:</b> {committed_date} | <b>Due:</b> {follow_up_date}\n\n"
            f"<b>Suggested follow-up:</b>\n"
            f"<i>{draft_display}</i>"
        )

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            # Fallback without HTML
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                )
            except Exception:
                logger.exception("Failed to send follow-up reminder to Telegram")

        # Mark that a reminder was sent for this item
        try:
            await self._db.execute(
                "UPDATE follow_ups SET reminder_sent = 1 WHERE id = ?",
                (fu_id,),
            )
            await self._db.commit()
        except Exception:
            logger.exception(f"Failed to mark reminder_sent for follow-up {fu_id}")

    # ------------------------------------------------------------------
    # Get overdue summary (for morning report)
    # ------------------------------------------------------------------

    async def get_overdue_summary(self) -> str:
        """Return HTML-formatted list of overdue and due-today follow-up items.

        Used by the morning report and trend analysis to insert a
        'Follow-Up Queue' section.
        """
        today = datetime.now(CT).strftime("%Y-%m-%d")

        # Overdue items
        cursor = await self._db.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date < ? AND status = 'pending' "
            "ORDER BY follow_up_date ASC",
            (today,),
        )
        overdue = [dict(row) for row in await cursor.fetchall()]

        # Due today
        cursor = await self._db.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date = ? AND status = 'pending' "
            "ORDER BY created_at ASC",
            (today,),
        )
        due_today = [dict(row) for row in await cursor.fetchall()]

        if not overdue and not due_today:
            return "<i>No follow-ups due or overdue today.</i>"

        lines = []

        if overdue:
            lines.append(f"<b>Overdue ({len(overdue)})</b>")
            for item in overdue[:10]:  # Cap display at 10
                project = escape(str(item.get("project_key", "?")))
                commitment = escape(str(item.get("commitment", ""))[:120])
                due = item.get("follow_up_date", "?")
                owner = escape(str(item.get("owner", "?")))
                lines.append(
                    f"  - [{project}] {commitment}\n"
                    f"    <i>Due: {due} | Owner: {owner}</i>"
                )

        if due_today:
            lines.append(f"<b>Due Today ({len(due_today)})</b>")
            for item in due_today[:10]:
                project = escape(str(item.get("project_key", "?")))
                commitment = escape(str(item.get("commitment", ""))[:120])
                owner = escape(str(item.get("owner", "?")))
                lines.append(
                    f"  - [{project}] {commitment}\n"
                    f"    <i>Owner: {owner}</i>"
                )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Main entry point — called by the scheduler
    # ------------------------------------------------------------------

    async def run_follow_up_scan(self, bot, chat_id: int) -> int:
        """Run a full follow-up scan cycle.

        DEPRECATED (2026-03-01): This method is no longer called by the scheduler.
        It scanned for due items and sent individual Telegram messages for each one,
        resulting in 30+ messages of spam. The scheduler tasks that called this
        (followup_10:00, followup_16:00, sunday_followup_scan) are all disabled.

        Follow-ups are now consolidated into a single end-of-day PDF report via
        proactive_followup.py. The data-layer methods (scan_for_follow_ups,
        create_follow_up, check_due_follow_ups) are still used by that system.

        Returns the number of follow-up drafts sent for approval.
        """
        logger.info("Follow-up scan starting...")
        start = time.monotonic()
        drafts_sent = 0

        try:
            # 1. Scan for new items that need follow-ups
            new_items = await self.scan_for_follow_ups()
            if new_items:
                logger.info(f"Follow-up scan: {len(new_items)} new item(s) to queue")
                for item in new_items:
                    try:
                        await self.create_follow_up(
                            constraint_id=item.get("constraint_id"),
                            project_key=item.get("project_key", ""),
                            owner=item.get("owner", "Unknown"),
                            commitment=item.get("commitment", ""),
                            committed_date=item.get("committed_date", ""),
                            follow_up_date=item.get("follow_up_date", ""),
                        )
                    except Exception:
                        logger.exception("Error creating follow-up entry")

            # 2. Check for due follow-ups
            due_items = await self.check_due_follow_ups()
            if not due_items:
                logger.info("Follow-up scan: no follow-ups due at this time")
            else:
                logger.info(f"Follow-up scan: {len(due_items)} item(s) due for follow-up")

                # 3. Process each due item
                for item in due_items:
                    try:
                        # Skip if reminder already sent (avoid duplicate sends within
                        # the same day across the 2 daily scans)
                        if item.get("reminder_sent", 0):
                            continue

                        # Draft a follow-up message
                        draft = await self.generate_follow_up_draft(item)
                        if not draft:
                            logger.warning(
                                f"Follow-up {item.get('id')}: failed to draft message"
                            )
                            continue

                        # Send for approval via Telegram
                        await self.send_for_approval(bot, chat_id, item, draft)
                        drafts_sent += 1

                        logger.info(
                            f"Follow-up draft sent: id={item.get('id')} "
                            f"project={item.get('project_key')}"
                        )

                        # Small delay to avoid Telegram rate limits
                        await asyncio.sleep(1)

                    except Exception:
                        logger.exception(
                            f"Error processing follow-up {item.get('id', '?')}"
                        )

        except Exception:
            logger.exception("Follow-up scan failed")

        duration = time.monotonic() - start
        logger.info(
            f"Follow-up scan complete: {drafts_sent} draft(s) sent in {duration:.1f}s"
        )
        return drafts_sent

    # ------------------------------------------------------------------
    # Log follow-up action to memory
    # ------------------------------------------------------------------

    async def log_to_memory(self, item: dict, action: str) -> None:
        """Log a follow-up action to the bot's memory system."""
        try:
            from bot.memory.store import MemoryStore

            memory = MemoryStore(MEMORY_DB_PATH)
            await memory.initialize()

            project = item.get("project_key", "Unknown")
            commitment = item.get("commitment", "")[:200]
            owner = item.get("owner", "Unknown")

            await memory.save(
                category="action_item",
                summary=f"Follow-up {action}: {project} — {commitment}",
                detail=(
                    f"Owner: {owner}\n"
                    f"Commitment: {commitment}\n"
                    f"Follow-up date: {item.get('follow_up_date')}\n"
                    f"Action: {action}"
                ),
                project_key=project if project != "Unknown" else None,
                source="followup_queue",
                tags=f"followup,{action}",
            )

            await memory.close()
        except Exception:
            logger.exception("Failed to log follow-up to memory")

    # ------------------------------------------------------------------
    # Mark follow-up as completed or rejected
    # ------------------------------------------------------------------

    async def mark_completed(self, fu_id: int) -> None:
        """Mark a follow-up as completed (approved and sent)."""
        await self._db.execute(
            "UPDATE follow_ups SET status = 'completed' WHERE id = ?",
            (fu_id,),
        )
        await self._db.commit()
        logger.info(f"Follow-up {fu_id} marked as completed")

    async def mark_rejected(self, fu_id: int) -> None:
        """Mark a follow-up as rejected (user declined)."""
        await self._db.execute(
            "UPDATE follow_ups SET status = 'rejected' WHERE id = ?",
            (fu_id,),
        )
        await self._db.commit()
        logger.info(f"Follow-up {fu_id} marked as rejected")
