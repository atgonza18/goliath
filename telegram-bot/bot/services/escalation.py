"""
Escalation Engine — Automated constraint escalation with human-in-the-loop approval.

Runs 3x/day (9 AM, 1 PM, 5 PM CT) via the scheduler. Pulls HIGH/MEDIUM priority
constraints from ConstraintsPro, tracks escalation state in SQLite, drafts escalation
emails at 3 levels, and sends each draft to Telegram for approve/edit/reject.

Escalation levels:
  Level 1 (Helpful)    — Friendly follow-up, "just checking in on this"
  Level 2 (Firm)       — Direct, "this is blocking progress, need resolution by X"
  Level 3 (Leadership) — CC to leadership, "open X days, impacting schedule"

5-day cooldown between escalation levels for the same constraint.
All actions logged to memory for audit trail.
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

from bot.config import REPO_ROOT, MEMORY_DB_PATH

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite schema for escalation state tracking
# ---------------------------------------------------------------------------

ESCALATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS escalation_state (
    constraint_id       TEXT PRIMARY KEY,
    project_key         TEXT,
    priority            TEXT,
    last_escalation_date TEXT,
    escalation_level    INTEGER DEFAULT 0,
    last_draft_sent     TEXT,
    cooldown_until      TEXT,
    owner               TEXT,
    description         TEXT,
    need_by_date        TEXT,
    status              TEXT DEFAULT 'open',
    created_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""

# ---------------------------------------------------------------------------
# Escalation email templates
# ---------------------------------------------------------------------------

LEVEL_1_PROMPT = """\
Draft a Level 1 (Helpful/Friendly) escalation follow-up email for a solar construction constraint.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}

Tone: Friendly and collaborative. This is a gentle check-in, not a demand.
Style: "Hi [name], just wanted to check in on this..." / "Wanted to follow up and see if there's \
anything I can help with..."

Write ONLY the email body. Start with a greeting (Hi [name],) and end with a signoff (Thanks, Aaron).
Use plain text, no HTML/markdown. Keep it concise — 3-5 sentences. Be specific about the constraint \
and mention the project name.
"""

LEVEL_2_PROMPT = """\
Draft a Level 2 (Firm/Direct) escalation email for a solar construction constraint that has not \
been resolved despite a previous follow-up.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Previous escalation: Level 1 sent on {last_escalation_date}

Tone: Professional but direct. This is blocking progress and needs resolution.
Style: "Following up on this again — this constraint is blocking [specific activity]..." / \
"We need resolution on this by [date] to stay on schedule..."

Write ONLY the email body. Start with a greeting and end with a signoff (Thanks, Aaron).
Use plain text, no HTML/markdown. Be specific about impact and include a concrete deadline. \
4-6 sentences.
"""

LEVEL_3_PROMPT = """\
Draft a Level 3 (Leadership Escalation) email for a solar construction constraint that remains \
unresolved after two previous escalation attempts.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Escalation history: Level 1 sent, Level 2 sent, still unresolved
- Previous escalation: Level 2 sent on {last_escalation_date}

Tone: Formal and urgent. This is being escalated to leadership because it threatens the project \
schedule.
Style: "Escalating this constraint to leadership attention — it has been open for {days_open} days \
and is directly impacting [project] schedule..." / "Despite two previous follow-ups, this remains \
unresolved..."

Include a note that leadership is being CC'd. Write ONLY the email body.
Start with a greeting and end with a signoff (Respectfully, Aaron Gonzalez).
Use plain text, no HTML/markdown. 5-8 sentences. Be factual — dates, days open, impact.
"""


# ---------------------------------------------------------------------------
# EscalationTracker — core state management
# ---------------------------------------------------------------------------

class EscalationTracker:
    """Manages escalation state in SQLite and drives the escalation workflow."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open the SQLite DB and create the escalation table if needed."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(ESCALATION_SCHEMA)
        await self._db.commit()
        logger.info(f"Escalation tracker initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Pull live constraints via Claude CLI + constraints_manager agent
    # ------------------------------------------------------------------

    async def get_constraints_needing_escalation(self) -> list[dict]:
        """Run Claude CLI with constraints_manager to pull live ConstraintsPro data.

        Returns a list of constraint dicts with fields:
          id, project, description, owner, priority, status, need_by_date, days_open
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import SubagentRunner

        prompt = (
            "Pull ALL open HIGH and MEDIUM priority constraints from ConstraintsPro.\n\n"
            "Steps:\n"
            "1. Call projects_list to get all project IDs\n"
            "2. For each project, call constraints_list_by_project\n"
            "3. Filter to only OPEN constraints with HIGH or MEDIUM priority\n"
            "4. For MEDIUM constraints, only include those with a need-by date within 7 days from today\n\n"
            "Return the results as a JSON array. Each object must have these exact fields:\n"
            '  {"id": "...", "project": "Project Name", "description": "...", '
            '"owner": "...", "priority": "HIGH|MEDIUM", "status": "open", '
            '"need_by_date": "YYYY-MM-DD or null", "days_open": N}\n\n'
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
            logger.error(f"Failed to pull constraints: {result.error}")
            return []

        # Parse JSON from the output
        return self._parse_constraint_json(result.output)

    @staticmethod
    def _parse_constraint_json(raw: str) -> list[dict]:
        """Extract a JSON array from Claude's output (may be wrapped in code fences)."""
        import re

        # Try to find JSON inside code fences first
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else raw.strip()

        # Find the array boundaries
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            logger.warning("No JSON array found in constraints output")
            return []

        try:
            data = json.loads(text[start:end + 1])
            if not isinstance(data, list):
                return []
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse constraints JSON: {e}")
            return []

    # ------------------------------------------------------------------
    # Escalation level determination
    # ------------------------------------------------------------------

    async def get_escalation_state(self, constraint_id: str) -> Optional[dict]:
        """Get the current escalation state for a constraint."""
        cursor = await self._db.execute(
            "SELECT * FROM escalation_state WHERE constraint_id = ?",
            (constraint_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def determine_escalation_level(self, constraint: dict) -> Optional[int]:
        """Determine what escalation level to apply next.

        Returns:
            1, 2, or 3 for the next escalation level to draft.
            None if no escalation needed (e.g., cooldown active, already at max).
        """
        cid = constraint.get("id", "")
        state = await self.get_escalation_state(cid)

        now = datetime.now(CT)

        if state is None:
            # Never escalated — start at Level 1
            return 1

        current_level = state.get("escalation_level", 0)

        # Already at max level — no further escalation
        if current_level >= 3:
            return None

        # Check cooldown
        cooldown_until = state.get("cooldown_until")
        if cooldown_until:
            try:
                cooldown_dt = datetime.fromisoformat(cooldown_until)
                if now < cooldown_dt:
                    logger.debug(
                        f"Constraint {cid} in cooldown until {cooldown_until} — skipping"
                    )
                    return None
            except (ValueError, TypeError):
                pass

        # Cooldown expired or not set — escalate to next level
        return current_level + 1

    # ------------------------------------------------------------------
    # Draft escalation email via Claude
    # ------------------------------------------------------------------

    async def draft_escalation_email(
        self, constraint: dict, level: int
    ) -> Optional[str]:
        """Generate an escalation email draft using Claude CLI.

        Returns the email body text, or None on failure.
        """
        from bot.agents.definitions import NIMROD
        from bot.agents.runner import SubagentRunner

        # Build the template context
        ctx = {
            "project": constraint.get("project", "Unknown Project"),
            "description": constraint.get("description", "No description"),
            "owner": constraint.get("owner", "Unassigned"),
            "priority": constraint.get("priority", "UNKNOWN"),
            "days_open": constraint.get("days_open", "?"),
            "need_by_date": constraint.get("need_by_date") or "Not specified",
            "last_escalation_date": "N/A",
        }

        # Pull last escalation date from state
        state = await self.get_escalation_state(constraint.get("id", ""))
        if state and state.get("last_escalation_date"):
            ctx["last_escalation_date"] = state["last_escalation_date"]

        # Select the right template
        if level == 1:
            prompt = LEVEL_1_PROMPT.format(**ctx)
        elif level == 2:
            prompt = LEVEL_2_PROMPT.format(**ctx)
        else:
            prompt = LEVEL_3_PROMPT.format(**ctx)

        runner = SubagentRunner()
        result = await runner.run(
            agent=NIMROD,
            task_prompt=prompt,
            timeout=120,
            no_tools=True,  # No tool access needed — just draft text
        )

        if result.success and result.output:
            # Clean up any structured blocks that Nimrod might produce
            import re
            text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", result.output, flags=re.DOTALL)
            return text.strip()

        logger.error(f"Failed to draft escalation email: {result.error}")
        return None

    # ------------------------------------------------------------------
    # Update escalation state after a draft is sent for approval
    # ------------------------------------------------------------------

    async def record_escalation(
        self, constraint: dict, level: int, draft: str
    ) -> None:
        """Record that an escalation draft was sent for approval."""
        cid = constraint.get("id", "")
        now_str = datetime.now(CT).strftime("%Y-%m-%dT%H:%M:%S")
        cooldown = (datetime.now(CT) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")

        await self._db.execute(
            """
            INSERT INTO escalation_state
                (constraint_id, project_key, priority, last_escalation_date,
                 escalation_level, last_draft_sent, cooldown_until, owner,
                 description, need_by_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(constraint_id) DO UPDATE SET
                priority = excluded.priority,
                last_escalation_date = excluded.last_escalation_date,
                escalation_level = excluded.escalation_level,
                last_draft_sent = excluded.last_draft_sent,
                cooldown_until = excluded.cooldown_until,
                owner = excluded.owner,
                description = excluded.description,
                need_by_date = excluded.need_by_date,
                updated_at = excluded.updated_at
            """,
            (
                cid,
                constraint.get("project", ""),
                constraint.get("priority", ""),
                now_str,
                level,
                draft[:2000],  # Truncate draft for storage
                cooldown,
                constraint.get("owner", ""),
                constraint.get("description", ""),
                constraint.get("need_by_date"),
                now_str,
            ),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Send draft to Telegram with approve/edit/reject buttons
    # ------------------------------------------------------------------

    async def send_for_approval(
        self, bot, chat_id: int, constraint: dict, level: int, draft: str
    ) -> None:
        """Send an escalation email draft to Telegram with inline approval buttons."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        level_names = {1: "Helpful", 2: "Firm", 3: "Leadership CC"}
        level_label = level_names.get(level, f"Level {level}")

        project = escape(constraint.get("project", "Unknown"))
        desc = escape(constraint.get("description", "")[:200])
        owner = escape(constraint.get("owner", "Unassigned"))
        priority = escape(constraint.get("priority", ""))
        days_open = constraint.get("days_open", "?")
        cid = constraint.get("id", "unknown")

        # Truncate draft for display (Telegram 4096 char limit minus header)
        draft_display = escape(draft[:2800])

        text = (
            f"<b>Escalation Draft — Level {level} ({level_label})</b>\n"
            f"<b>Project:</b> {project}\n"
            f"<b>Constraint:</b> {desc}\n"
            f"<b>Owner:</b> {owner} | <b>Priority:</b> {priority} | "
            f"<b>Days open:</b> {days_open}\n\n"
            f"<b>Draft email:</b>\n"
            f"<i>{draft_display}</i>\n\n"
            f"Approve, edit, or reject this escalation?"
        )

        # Use escalation-specific callback data prefix to avoid collisions
        # with the email approval flow
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Approve & Send",
                    callback_data=f"esc_approve:{cid}:{level}",
                ),
                InlineKeyboardButton(
                    "Reject",
                    callback_data=f"esc_reject:{cid}:{level}",
                ),
            ]
        ])

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            # Fallback without HTML
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:
                logger.exception(f"Failed to send escalation approval to Telegram")

    # ------------------------------------------------------------------
    # Main entry point — called by the scheduler
    # ------------------------------------------------------------------

    async def run_escalation_scan(self, bot, chat_id: int) -> int:
        """Run a full escalation scan cycle.

        Returns the number of escalation drafts sent for approval.
        """
        logger.info("Escalation scan starting...")
        start = time.monotonic()
        drafts_sent = 0

        try:
            # 1. Pull live constraints from ConstraintsPro
            constraints = await self.get_constraints_needing_escalation()
            if not constraints:
                logger.info("Escalation scan: no actionable constraints found")
                return 0

            logger.info(f"Escalation scan: {len(constraints)} constraint(s) to evaluate")

            # 2. Sort: HIGH priority first, then by days_open descending
            constraints.sort(
                key=lambda c: (
                    0 if c.get("priority", "").upper() == "HIGH" else 1,
                    -(c.get("days_open", 0) or 0),
                ),
            )

            # 3. Process each constraint
            for constraint in constraints:
                try:
                    cid = constraint.get("id", "")
                    if not cid:
                        continue

                    level = await self.determine_escalation_level(constraint)
                    if level is None:
                        logger.debug(f"Constraint {cid}: no escalation needed")
                        continue

                    # Draft the escalation email
                    draft = await self.draft_escalation_email(constraint, level)
                    if not draft:
                        logger.warning(f"Constraint {cid}: failed to draft level {level} email")
                        continue

                    # Send for approval via Telegram
                    await self.send_for_approval(bot, chat_id, constraint, level, draft)

                    # Record the escalation
                    await self.record_escalation(constraint, level, draft)
                    drafts_sent += 1

                    logger.info(
                        f"Escalation draft sent: {cid} (level {level}, "
                        f"project={constraint.get('project')})"
                    )

                    # Small delay between drafts to avoid Telegram rate limits
                    await asyncio.sleep(1)

                except Exception:
                    logger.exception(f"Error processing constraint {constraint.get('id', '?')}")

        except Exception:
            logger.exception("Escalation scan failed")

        duration = time.monotonic() - start
        logger.info(
            f"Escalation scan complete: {drafts_sent} draft(s) sent in {duration:.1f}s"
        )
        return drafts_sent

    # ------------------------------------------------------------------
    # Log escalation action to memory
    # ------------------------------------------------------------------

    async def log_to_memory(
        self, constraint: dict, level: int, action: str
    ) -> None:
        """Log an escalation action to the bot's memory system."""
        try:
            from bot.memory.store import MemoryStore

            memory = MemoryStore(MEMORY_DB_PATH)
            await memory.initialize()

            project = constraint.get("project", "Unknown")
            desc = constraint.get("description", "")[:200]
            owner = constraint.get("owner", "Unknown")

            await memory.save(
                category="action_item",
                summary=(
                    f"Escalation Level {level} {action}: {project} — {desc}"
                ),
                detail=(
                    f"Constraint ID: {constraint.get('id')}\n"
                    f"Owner: {owner}\n"
                    f"Priority: {constraint.get('priority')}\n"
                    f"Days open: {constraint.get('days_open')}\n"
                    f"Action: {action}"
                ),
                project_key=constraint.get("project_key"),
                source="escalation_engine",
                tags=f"escalation,level{level},{action}",
            )

            await memory.close()
        except Exception:
            logger.exception("Failed to log escalation to memory")
