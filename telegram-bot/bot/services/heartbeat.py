"""
Hourly Constraint Heartbeat — Periodic snapshot + diff of ConstraintsPro state.

Runs every 60 minutes via the scheduler. Takes a snapshot of all constraints,
compares to the previous snapshot, and notifies the user ONLY when something
meaningful changed. Stays completely silent if nothing changed (no spam).

Meaningful changes detected:
  - New HIGH or MEDIUM constraint appeared
  - A constraint status changed (open -> resolved, or vice versa)
  - A constraint passed its need-by date (became overdue)
  - A constraint priority was elevated (e.g., LOW -> MEDIUM, MEDIUM -> HIGH)
"""

import json
import logging
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional

from bot.config import REPO_ROOT

logger = logging.getLogger(__name__)

# Snapshot storage directory
SNAPSHOT_DIR = REPO_ROOT / "data" / "constraint_snapshots"
LATEST_SNAPSHOT_PATH = SNAPSHOT_DIR / "latest.json"
PREVIOUS_SNAPSHOT_PATH = SNAPSHOT_DIR / "previous.json"


# ---------------------------------------------------------------------------
# ConstraintHeartbeat — snapshot + diff + notify
# ---------------------------------------------------------------------------

class ConstraintHeartbeat:
    """Hourly constraint state monitor with change detection."""

    def __init__(self):
        # Ensure snapshot directory exists
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Take a snapshot via Claude CLI + constraints_manager
    # ------------------------------------------------------------------

    async def take_snapshot(self) -> Optional[list[dict]]:
        """Pull all constraints from ConstraintsPro and return as a list of dicts.

        Each dict has: id, project, description, owner, priority, status,
                       need_by_date, days_open
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import SubagentRunner

        prompt = (
            "Pull ALL constraints from ConstraintsPro (all projects, all priorities, all statuses).\n\n"
            "Steps:\n"
            "1. Call projects_list to get all project IDs\n"
            "2. For each project, call constraints_list_by_project\n"
            "3. Include ALL constraints (open, resolved, in-progress, etc.)\n\n"
            "Return the results as a JSON array. Each object must have these exact fields:\n"
            '  {"id": "...", "project": "Project Name", "description": "...", '
            '"owner": "...", "priority": "HIGH|MEDIUM|LOW", "status": "...", '
            '"need_by_date": "YYYY-MM-DD or null", "days_open": N}\n\n'
            "Output ONLY the JSON array, no other text. If no constraints exist, output [].\n"
            "Wrap the JSON in ```json ... ``` code fences."
        )

        runner = SubagentRunner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=300,
        )

        if not result.success or not result.output:
            logger.error(f"Heartbeat snapshot failed: {result.error}")
            return None

        return self._parse_constraint_json(result.output)

    @staticmethod
    def _parse_constraint_json(raw: str) -> Optional[list[dict]]:
        """Extract a JSON array from Claude's output."""
        import re

        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else raw.strip()

        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            logger.warning("Heartbeat: no JSON array found in snapshot output")
            return None

        try:
            data = json.loads(text[start:end + 1])
            if not isinstance(data, list):
                return None
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Heartbeat: failed to parse snapshot JSON: {e}")
            return None

    # ------------------------------------------------------------------
    # Save / load snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, constraints: list[dict]) -> None:
        """Save the current snapshot, rotating previous one."""
        # Rotate: current latest -> previous
        if LATEST_SNAPSHOT_PATH.exists():
            try:
                LATEST_SNAPSHOT_PATH.rename(PREVIOUS_SNAPSHOT_PATH)
            except OSError:
                # If rename fails, just copy
                PREVIOUS_SNAPSHOT_PATH.write_text(
                    LATEST_SNAPSHOT_PATH.read_text()
                )

        # Write new snapshot
        snapshot = {
            "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "count": len(constraints),
            "constraints": constraints,
        }
        LATEST_SNAPSHOT_PATH.write_text(
            json.dumps(snapshot, indent=2, default=str)
        )
        logger.info(f"Heartbeat snapshot saved: {len(constraints)} constraints")

    def load_previous_snapshot(self) -> Optional[list[dict]]:
        """Load the previous snapshot for comparison."""
        if not PREVIOUS_SNAPSHOT_PATH.exists():
            return None

        try:
            data = json.loads(PREVIOUS_SNAPSHOT_PATH.read_text())
            return data.get("constraints", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load previous snapshot: {e}")
            return None

    # ------------------------------------------------------------------
    # Compare snapshots — detect meaningful changes
    # ------------------------------------------------------------------

    def compare_snapshots(
        self, old: list[dict], new: list[dict]
    ) -> list[dict]:
        """Compare two snapshots and return a list of meaningful changes.

        Each change is a dict:
          {"type": "new_constraint|status_changed|became_overdue|priority_elevated",
           "constraint": {...}, "old": {...} or None, "detail": "human-readable"}
        """
        changes = []

        # Index old constraints by ID for fast lookup
        old_by_id = {}
        for c in old:
            cid = c.get("id")
            if cid:
                old_by_id[cid] = c

        now = datetime.utcnow().strftime("%Y-%m-%d")

        for constraint in new:
            cid = constraint.get("id")
            if not cid:
                continue

            priority = (constraint.get("priority") or "").upper()
            status = (constraint.get("status") or "").lower()
            need_by = constraint.get("need_by_date")
            project = constraint.get("project", "Unknown")
            desc = constraint.get("description", "")[:120]

            if cid not in old_by_id:
                # New constraint — only notify for HIGH or MEDIUM
                if priority in ("HIGH", "MEDIUM"):
                    changes.append({
                        "type": "new_constraint",
                        "constraint": constraint,
                        "old": None,
                        "detail": (
                            f"New {priority} constraint in {project}: {desc}"
                        ),
                    })
                continue

            old_c = old_by_id[cid]
            old_status = (old_c.get("status") or "").lower()
            old_priority = (old_c.get("priority") or "").upper()
            old_need_by = old_c.get("need_by_date")

            # Status changed
            if status != old_status:
                changes.append({
                    "type": "status_changed",
                    "constraint": constraint,
                    "old": old_c,
                    "detail": (
                        f"{project}: status changed {old_status} -> {status} — {desc}"
                    ),
                })

            # Priority elevated
            priority_rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
            new_rank = priority_rank.get(priority, -1)
            old_rank = priority_rank.get(old_priority, -1)
            if new_rank > old_rank:
                changes.append({
                    "type": "priority_elevated",
                    "constraint": constraint,
                    "old": old_c,
                    "detail": (
                        f"{project}: priority elevated {old_priority} -> {priority} — {desc}"
                    ),
                })

            # Became overdue (need-by date passed)
            if need_by and status in ("open", "in-progress", "in_progress", "pending"):
                was_overdue = (
                    old_need_by
                    and old_need_by < now
                    if old_need_by else False
                )
                is_overdue = need_by < now

                if is_overdue and not was_overdue:
                    changes.append({
                        "type": "became_overdue",
                        "constraint": constraint,
                        "old": old_c,
                        "detail": (
                            f"{project}: OVERDUE (need-by {need_by}) — {desc}"
                        ),
                    })

        return changes

    # ------------------------------------------------------------------
    # Format and send change notifications
    # ------------------------------------------------------------------

    async def notify_changes(self, bot, chat_id: int, changes: list[dict]) -> None:
        """Format changes as an HTML message and send to Telegram."""
        if not changes:
            return

        from bot.utils.formatting import chunk_message

        # Group by type
        new_constraints = [c for c in changes if c["type"] == "new_constraint"]
        status_changes = [c for c in changes if c["type"] == "status_changed"]
        overdue = [c for c in changes if c["type"] == "became_overdue"]
        elevated = [c for c in changes if c["type"] == "priority_elevated"]

        sections = []
        sections.append(
            f"<b>Constraint Heartbeat</b> "
            f"({len(changes)} change{'s' if len(changes) != 1 else ''} detected)\n"
        )

        if overdue:
            lines = [f"<b>OVERDUE</b>"]
            for c in overdue:
                lines.append(f"  {escape(c['detail'])}")
            sections.append("\n".join(lines))

        if new_constraints:
            lines = [f"<b>New Constraints</b>"]
            for c in new_constraints:
                lines.append(f"  {escape(c['detail'])}")
            sections.append("\n".join(lines))

        if elevated:
            lines = [f"<b>Priority Elevated</b>"]
            for c in elevated:
                lines.append(f"  {escape(c['detail'])}")
            sections.append("\n".join(lines))

        if status_changes:
            lines = [f"<b>Status Changes</b>"]
            for c in status_changes:
                lines.append(f"  {escape(c['detail'])}")
            sections.append("\n".join(lines))

        text = "\n\n".join(sections)

        for chunk_text in chunk_message(text, max_len=4000):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                try:
                    await bot.send_message(chat_id=chat_id, text=chunk_text)
                except Exception:
                    logger.exception("Failed to send heartbeat notification")

    # ------------------------------------------------------------------
    # Log heartbeat to memory
    # ------------------------------------------------------------------

    async def log_to_memory(self, changes: list[dict]) -> None:
        """Log heartbeat changes to memory for audit trail."""
        if not changes:
            return

        try:
            from bot.memory.store import MemoryStore
            from bot.config import MEMORY_DB_PATH

            memory = MemoryStore(MEMORY_DB_PATH)
            await memory.initialize()

            summary_parts = []
            for c in changes[:5]:  # Cap at 5 for summary
                summary_parts.append(c["detail"][:100])

            await memory.save(
                category="observation",
                summary=f"Constraint heartbeat: {len(changes)} change(s) detected",
                detail="\n".join(summary_parts),
                source="heartbeat",
                tags="heartbeat,constraints,automated",
            )

            await memory.close()
        except Exception:
            logger.exception("Failed to log heartbeat to memory")

    # ------------------------------------------------------------------
    # Main entry point — called by scheduler
    # ------------------------------------------------------------------

    async def run_heartbeat(self, bot, chat_id: int) -> int:
        """Run a full heartbeat cycle: snapshot, compare, notify.

        Returns the number of changes detected (0 = silent).
        """
        logger.info("Constraint heartbeat starting...")
        start = time.monotonic()

        try:
            # 1. Take a fresh snapshot
            new_snapshot = await self.take_snapshot()
            if new_snapshot is None:
                logger.warning("Heartbeat: failed to take snapshot — skipping cycle")
                return 0

            # 2. Load previous snapshot for comparison
            old_snapshot = self.load_previous_snapshot()

            # 3. Save the new snapshot (rotate previous)
            self.save_snapshot(new_snapshot)

            # 4. If no previous snapshot, this is the first run — nothing to compare
            if old_snapshot is None:
                logger.info(
                    f"Heartbeat: first run — saved baseline snapshot "
                    f"({len(new_snapshot)} constraints). No comparison yet."
                )
                return 0

            # 5. Compare snapshots
            changes = self.compare_snapshots(old_snapshot, new_snapshot)

            duration = time.monotonic() - start

            if not changes:
                logger.info(
                    f"Heartbeat: no meaningful changes detected ({duration:.1f}s, "
                    f"{len(new_snapshot)} constraints)"
                )
                return 0

            # 6. Notify user and log to memory
            logger.info(
                f"Heartbeat: {len(changes)} change(s) detected in {duration:.1f}s"
            )
            await self.notify_changes(bot, chat_id, changes)
            await self.log_to_memory(changes)

            return len(changes)

        except Exception:
            logger.exception("Constraint heartbeat error")
            return 0
