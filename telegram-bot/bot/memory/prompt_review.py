"""
Prompt Self-Review System (V4) — Scheduled heuristic audit of agent prompts.

Performs periodic HEURISTIC checks (no LLM calls) on agent system prompts:
  - Length audit: flags prompts that are too long, too short, or outliers
  - Staleness check: flags prompts not updated in 30+ days (git-based)
  - Lesson integration: cross-references experience_replay lessons
  - Effectiveness check: cross-references reflection scores and usage

CRITICAL: This system NEVER auto-modifies prompts. It generates PROPOSALS
that require human approval via approve_review() / reject_review().

Proposal flow:
  1. run_prompt_review() performs heuristic checks → stores findings
  2. get_pending_reviews() surfaces unresolved findings (e.g., in morning report)
  3. Human reviews and calls approve_review() or reject_review()
  4. Approved proposals can then be manually applied by the devops agent

Severity levels:
  - info: FYI observation, no action required
  - suggestion: recommended improvement, worth considering
  - warning: potential problem that should be addressed soon
"""

import asyncio
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from bot.config import (
    PROMPT_REVIEW_ENABLED,
    PROMPT_REVIEW_MAX_LENGTH_WARNING,
    PROMPT_REVIEW_STALENESS_DAYS,
)

logger = logging.getLogger(__name__)

# SQLite busy-timeout in milliseconds
_BUSY_TIMEOUT_MS = 5000

PROMPT_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    agent_name      TEXT NOT NULL,
    review_type     TEXT NOT NULL,
    finding         TEXT NOT NULL,
    recommendation  TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    status          TEXT NOT NULL DEFAULT 'pending',
    approved_by     TEXT,
    applied_at      TEXT
);
"""

# Valid enum values
REVIEW_TYPES = {"length_audit", "clarity_check", "lesson_integration",
                "staleness_check", "effectiveness"}
SEVERITIES = {"info", "suggestion", "warning"}
STATUSES = {"pending", "approved", "rejected", "applied"}

# Agent definition directory (relative to repo root)
_AGENT_DEFS_DIR = Path(__file__).resolve().parent.parent / "agents" / "agent_definitions"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # /opt/goliath


class PromptReviewStore:
    """Async SQLite store for prompt self-review findings."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Create the table if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        await self._db.execute("PRAGMA journal_mode = WAL")
        # Performance pragmas — safe with WAL mode
        await self._db.execute("PRAGMA synchronous = NORMAL")
        await self._db.execute("PRAGMA cache_size = -8000")
        await self._db.execute("PRAGMA mmap_size = 67108864")
        await self._db.execute("PRAGMA temp_store = MEMORY")
        await self._db.executescript(PROMPT_REVIEW_SCHEMA)
        await self._db.commit()
        logger.info(f"Prompt review store initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    async def _store_finding(
        self,
        agent_name: str,
        review_type: str,
        finding: str,
        recommendation: str,
        severity: str = "info",
    ) -> int:
        """Insert a single review finding. Returns the row ID."""
        cursor = await self._db.execute(
            "INSERT INTO prompt_reviews "
            "(agent_name, review_type, finding, recommendation, severity) "
            "VALUES (?, ?, ?, ?, ?)",
            (agent_name, review_type, finding, recommendation, severity),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_pending_reviews(
        self, agent_name: Optional[str] = None
    ) -> list[dict]:
        """Return unresolved (pending) findings, optionally filtered by agent."""
        if agent_name:
            cursor = await self._db.execute(
                "SELECT * FROM prompt_reviews "
                "WHERE status = 'pending' AND agent_name = ? "
                "ORDER BY severity DESC, created_at DESC",
                (agent_name,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM prompt_reviews "
                "WHERE status = 'pending' "
                "ORDER BY severity DESC, created_at DESC",
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def approve_review(self, review_id: int, approved_by: str = "user") -> bool:
        """Mark a review finding as approved. Returns True if row was updated."""
        cursor = await self._db.execute(
            "UPDATE prompt_reviews SET status = 'approved', approved_by = ? "
            "WHERE id = ? AND status = 'pending'",
            (approved_by, review_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def reject_review(self, review_id: int) -> bool:
        """Mark a review finding as rejected. Returns True if row was updated."""
        cursor = await self._db.execute(
            "UPDATE prompt_reviews SET status = 'rejected' "
            "WHERE id = ? AND status = 'pending'",
            (review_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def mark_applied(self, review_id: int) -> bool:
        """Mark an approved review as applied (prompt was actually changed)."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        cursor = await self._db.execute(
            "UPDATE prompt_reviews SET status = 'applied', applied_at = ? "
            "WHERE id = ? AND status = 'approved'",
            (now, review_id),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def get_review_summary(self) -> str:
        """Generate a human-readable summary suitable for morning report inclusion.

        Returns a multi-line string like:
            PROMPT HEALTH:
            * 2 warnings, 3 suggestions, 1 info
            * constraints_manager: avg score 4.2 (healthy)
            * devops: prompt is 8,200 chars (consider trimming)
            * folder_organizer: not used in 45 days
        """
        # Count pending by severity
        cursor = await self._db.execute(
            "SELECT severity, COUNT(*) FROM prompt_reviews "
            "WHERE status = 'pending' GROUP BY severity"
        )
        severity_counts = {row[0]: row[1] for row in await cursor.fetchall()}

        warnings = severity_counts.get("warning", 0)
        suggestions = severity_counts.get("suggestion", 0)
        infos = severity_counts.get("info", 0)
        total = warnings + suggestions + infos

        if total == 0:
            return "PROMPT HEALTH: All clear — no pending findings."

        lines = ["PROMPT HEALTH:"]
        count_parts = []
        if warnings:
            count_parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
        if suggestions:
            count_parts.append(f"{suggestions} suggestion{'s' if suggestions != 1 else ''}")
        if infos:
            count_parts.append(f"{infos} info")
        lines.append(f"  {', '.join(count_parts)}")

        # Get up to 8 most important pending findings for the summary
        cursor = await self._db.execute(
            "SELECT agent_name, finding, severity FROM prompt_reviews "
            "WHERE status = 'pending' "
            "ORDER BY CASE severity "
            "  WHEN 'warning' THEN 1 "
            "  WHEN 'suggestion' THEN 2 "
            "  WHEN 'info' THEN 3 END, "
            "created_at DESC LIMIT 8"
        )
        rows = await cursor.fetchall()
        for row in rows:
            agent = row[0]
            finding_short = row[1][:80]
            sev = row[2]
            icon = {"warning": "[!]", "suggestion": "[~]", "info": "[i]"}.get(sev, "")
            lines.append(f"  {icon} {agent}: {finding_short}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Heuristic checks
    # ------------------------------------------------------------------

    def _load_agent_prompts(self) -> dict[str, str]:
        """Load all agent system prompts from agent definition files.

        Returns dict of {agent_name: system_prompt_text}.
        """
        prompts = {}
        try:
            # Import the agent definitions registry
            from bot.agents.agent_definitions import ALL_AGENTS
            for name, agent_def in ALL_AGENTS.items():
                prompts[name] = agent_def.system_prompt
        except Exception as exc:
            logger.warning(f"Failed to import agent definitions: {exc}")
        return prompts

    def _get_agent_def_file(self, agent_name: str) -> Optional[Path]:
        """Return the .py file path for an agent definition, if it exists."""
        candidate = _AGENT_DEFS_DIR / f"{agent_name}.py"
        if candidate.exists():
            return candidate
        return None

    async def _check_length(self, prompts: dict[str, str]) -> list[dict]:
        """LENGTH AUDIT: flag prompts that are too long, too short, or outliers."""
        findings = []
        if not prompts:
            return findings

        lengths = {name: len(text) for name, text in prompts.items()}
        avg_length = sum(lengths.values()) / len(lengths) if lengths else 0

        for name, length in lengths.items():
            # Too long
            if length > PROMPT_REVIEW_MAX_LENGTH_WARNING:
                findings.append({
                    "agent_name": name,
                    "review_type": "length_audit",
                    "finding": f"prompt is {length:,} chars (threshold: {PROMPT_REVIEW_MAX_LENGTH_WARNING:,})",
                    "recommendation": (
                        f"Consider trimming the {name} prompt. Long prompts may cause "
                        f"context dilution and slower inference. Look for redundant "
                        f"instructions, examples that could be shortened, or sections "
                        f"that could be moved to Claude.md."
                    ),
                    "severity": "warning",
                })
            # Too short
            elif length < 500:
                findings.append({
                    "agent_name": name,
                    "review_type": "length_audit",
                    "finding": f"prompt is only {length:,} chars (may be too sparse)",
                    "recommendation": (
                        f"The {name} prompt may lack sufficient guidance. Consider "
                        f"adding more specific instructions, examples, or constraints "
                        f"to improve agent performance."
                    ),
                    "severity": "info",
                })

            # Outlier check: more than 2x the average
            if avg_length > 0 and length > 2 * avg_length:
                findings.append({
                    "agent_name": name,
                    "review_type": "length_audit",
                    "finding": (
                        f"prompt is {length:,} chars — "
                        f"{length / avg_length:.1f}x the average ({avg_length:,.0f})"
                    ),
                    "recommendation": (
                        f"The {name} prompt is significantly longer than other agents. "
                        f"Review whether all content is necessary or if some can be "
                        f"consolidated into shared context (Claude.md)."
                    ),
                    "severity": "suggestion",
                })

        return findings

    async def _check_staleness(self, prompts: dict[str, str]) -> list[dict]:
        """STALENESS CHECK: flag prompts not modified in 30+ days via git log."""
        findings = []
        threshold = timedelta(days=PROMPT_REVIEW_STALENESS_DAYS)
        now = datetime.now(timezone.utc)

        for agent_name in prompts:
            agent_file = self._get_agent_def_file(agent_name)
            if not agent_file:
                continue

            try:
                # Get last commit date for this file
                result = subprocess.run(
                    ["git", "log", "-1", "--format=%aI", "--", str(agent_file)],
                    capture_output=True,
                    text=True,
                    cwd=str(_REPO_ROOT),
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    last_modified_str = result.stdout.strip()
                    # Parse ISO 8601 date from git
                    last_modified = datetime.fromisoformat(last_modified_str)
                    age = now - last_modified
                    if age > threshold:
                        days = age.days
                        findings.append({
                            "agent_name": agent_name,
                            "review_type": "staleness_check",
                            "finding": (
                                f"prompt hasn't been updated in {days} days "
                                f"(last: {last_modified_str[:10]})"
                            ),
                            "recommendation": (
                                f"Review the {agent_name} prompt for relevance. "
                                f"The system has evolved — check if instructions "
                                f"still match current capabilities and workflows."
                            ),
                            "severity": "info",
                        })
                else:
                    # File has never been committed (new file)
                    logger.debug(f"No git history for {agent_file}")
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                logger.warning(f"Git log check failed for {agent_name}: {exc}")

        return findings

    async def _check_lesson_integration(self, prompts: dict[str, str]) -> list[dict]:
        """LESSON INTEGRATION: cross-reference experience_replay lessons.

        Checks if unintegrated lessons with high confidence exist for each agent.
        Gracefully skips if experience_replay tables don't exist yet (V3 in parallel).
        """
        findings = []
        try:
            # Check if the experience_replay table exists
            cursor = await self._db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='lessons_learned'"
            )
            row = await cursor.fetchone()
            if not row:
                logger.debug("lessons_learned table not found — skipping lesson integration check")
                return findings

            # Query high-confidence lessons per agent
            for agent_name in prompts:
                cursor = await self._db.execute(
                    "SELECT lesson_text, confidence FROM lessons_learned "
                    "WHERE applicable_agents LIKE '%' || ? || '%' AND confidence > 0.7 AND times_applied = 0 "
                    "ORDER BY confidence DESC LIMIT 5",
                    (agent_name,),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    lesson_text = row[0] if isinstance(row, (list, tuple)) else row["lesson_text"]
                    confidence = row[1] if isinstance(row, (list, tuple)) else row["confidence"]
                    findings.append({
                        "agent_name": agent_name,
                        "review_type": "lesson_integration",
                        "finding": (
                            f"unintegrated lesson (confidence {confidence:.2f}): "
                            f"{str(lesson_text)[:120]}"
                        ),
                        "recommendation": (
                            f"Consider adding to {agent_name} prompt: {str(lesson_text)[:200]}"
                        ),
                        "severity": "suggestion",
                    })

            # Also check: if lessons exist for an agent but the prompt hasn't
            # been updated since the lessons were created, flag it
            for agent_name in prompts:
                cursor = await self._db.execute(
                    "SELECT COUNT(*) FROM lessons_learned "
                    "WHERE applicable_agents LIKE '%' || ? || '%' AND times_applied = 0",
                    (agent_name,),
                )
                count_row = await cursor.fetchone()
                lesson_count = count_row[0] if count_row else 0
                if lesson_count > 0:
                    # Check git modification date
                    agent_file = self._get_agent_def_file(agent_name)
                    if agent_file:
                        try:
                            result = subprocess.run(
                                ["git", "log", "-1", "--format=%aI", "--", str(agent_file)],
                                capture_output=True, text=True,
                                cwd=str(_REPO_ROOT), timeout=10,
                            )
                            if result.returncode == 0 and result.stdout.strip():
                                last_mod = datetime.fromisoformat(result.stdout.strip())
                                # Check if any lessons were created after the last prompt update
                                cursor = await self._db.execute(
                                    "SELECT COUNT(*) FROM lessons_learned "
                                    "WHERE applicable_agents LIKE '%' || ? || '%' AND times_applied = 0 "
                                    "AND created_at > ?",
                                    (agent_name, last_mod.strftime("%Y-%m-%dT%H:%M:%S")),
                                )
                                newer_row = await cursor.fetchone()
                                newer_count = newer_row[0] if newer_row else 0
                                if newer_count > 0:
                                    findings.append({
                                        "agent_name": agent_name,
                                        "review_type": "staleness_check",
                                        "finding": (
                                            f"{newer_count} lesson(s) accumulated since last "
                                            f"prompt update ({last_mod.strftime('%Y-%m-%d')})"
                                        ),
                                        "recommendation": (
                                            f"The {agent_name} prompt has not been updated to "
                                            f"incorporate {newer_count} learned lesson(s). "
                                            f"Review and integrate applicable insights."
                                        ),
                                        "severity": "suggestion",
                                    })
                        except (subprocess.TimeoutExpired, FileNotFoundError):
                            pass

        except Exception as exc:
            logger.warning(f"Lesson integration check failed: {exc}")

        return findings

    async def _check_effectiveness(self, prompts: dict[str, str]) -> list[dict]:
        """EFFECTIVENESS CHECK: cross-reference reflection scores and usage.

        Checks average reflection scores and last-used dates per agent.
        Gracefully skips if reflections table doesn't exist yet (V1/V2 in parallel).
        """
        findings = []
        now = datetime.now(timezone.utc)
        staleness_threshold = timedelta(days=PROMPT_REVIEW_STALENESS_DAYS)

        try:
            # Check if reflections table exists
            cursor = await self._db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='reflections'"
            )
            row = await cursor.fetchone()
            has_reflections = row is not None

            if has_reflections:
                # Average score per agent
                for agent_name in prompts:
                    cursor = await self._db.execute(
                        "SELECT AVG(quality_score), COUNT(*) FROM reflections "
                        "WHERE agents_used LIKE '%' || ? || '%'",
                        (agent_name,),
                    )
                    row = await cursor.fetchone()
                    avg_score = row[0] if row and row[0] is not None else None
                    count = row[1] if row else 0

                    if avg_score is not None and count >= 3 and avg_score < 3.0:
                        findings.append({
                            "agent_name": agent_name,
                            "review_type": "effectiveness",
                            "finding": (
                                f"avg reflection score {avg_score:.1f}/5 "
                                f"across {count} interactions"
                            ),
                            "recommendation": (
                                f"The {agent_name} agent is underperforming "
                                f"(avg score {avg_score:.1f}). Review the system prompt "
                                f"for clarity issues, missing instructions, or misaligned "
                                f"expectations. Check recent low-scoring interactions for "
                                f"common failure patterns."
                            ),
                            "severity": "warning",
                        })

            # Check activity_log for last usage per agent
            cursor = await self._db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='activity_log'"
            )
            row = await cursor.fetchone()
            has_activity_log = row is not None

            if has_activity_log:
                for agent_name in prompts:
                    if agent_name == "nimrod":
                        # Nimrod is always used — skip
                        continue
                    cursor = await self._db.execute(
                        "SELECT MAX(created_at) FROM activity_log "
                        "WHERE subagents_dispatched LIKE ?",
                        (f"%{agent_name}%",),
                    )
                    row = await cursor.fetchone()
                    last_used_str = row[0] if row and row[0] else None

                    if last_used_str:
                        last_used = datetime.fromisoformat(last_used_str).replace(
                            tzinfo=timezone.utc
                        )
                        age = now - last_used
                        if age > staleness_threshold:
                            findings.append({
                                "agent_name": agent_name,
                                "review_type": "effectiveness",
                                "finding": f"not used in {age.days} days",
                                "recommendation": (
                                    f"The {agent_name} agent hasn't been dispatched "
                                    f"in {age.days} days. It may be obsolete, or "
                                    f"Nimrod may not be routing tasks to it. "
                                    f"Review whether this agent is still needed and "
                                    f"whether routing guidance in Nimrod's prompt "
                                    f"mentions it clearly."
                                ),
                                "severity": "info",
                            })
                    else:
                        # Never dispatched at all (no matching rows)
                        findings.append({
                            "agent_name": agent_name,
                            "review_type": "effectiveness",
                            "finding": "never dispatched (no activity log entries)",
                            "recommendation": (
                                f"The {agent_name} agent has no dispatch history. "
                                f"Either it's brand new, or Nimrod is not routing "
                                f"tasks to it. Verify routing guidance exists in "
                                f"Nimrod's prompt."
                            ),
                            "severity": "info",
                        })

        except Exception as exc:
            logger.warning(f"Effectiveness check failed: {exc}")

        return findings

    # ------------------------------------------------------------------
    # Main review runner
    # ------------------------------------------------------------------

    async def run_prompt_review(self) -> str:
        """Run all heuristic checks and store findings.

        Returns a summary string suitable for morning report inclusion.
        """
        if not PROMPT_REVIEW_ENABLED:
            return "PROMPT HEALTH: Review disabled."

        logger.info("Starting prompt self-review...")
        prompts = self._load_agent_prompts()
        if not prompts:
            logger.warning("No agent prompts found — skipping review")
            return "PROMPT HEALTH: No agent prompts found."

        all_findings = []

        # Run all checks
        length_findings = await self._check_length(prompts)
        all_findings.extend(length_findings)

        staleness_findings = await self._check_staleness(prompts)
        all_findings.extend(staleness_findings)

        lesson_findings = await self._check_lesson_integration(prompts)
        all_findings.extend(lesson_findings)

        effectiveness_findings = await self._check_effectiveness(prompts)
        all_findings.extend(effectiveness_findings)

        # Deduplicate: don't re-store findings that are already pending
        # with the same agent + review_type + similar finding text
        existing_pending = await self.get_pending_reviews()
        existing_keys = {
            (r["agent_name"], r["review_type"], r["finding"][:60])
            for r in existing_pending
        }

        stored_count = 0
        for f in all_findings:
            key = (f["agent_name"], f["review_type"], f["finding"][:60])
            if key not in existing_keys:
                await self._store_finding(**f)
                stored_count += 1
                existing_keys.add(key)

        logger.info(
            f"Prompt review complete: {len(all_findings)} findings, "
            f"{stored_count} new (rest already pending)"
        )

        # Return the summary
        return await self.get_review_summary()


# --------------------------------------------------------------------------
# Schedulable entry point (does NOT modify scheduler.py)
# --------------------------------------------------------------------------

async def run_weekly_prompt_review() -> str:
    """Standalone entry point for weekly prompt self-review.

    Designed to be called from the scheduler (e.g., Sunday 3 AM CT).
    Opens its own DB connection, runs all checks, closes cleanly.

    Returns a summary string suitable for morning report.
    """
    from bot.config import MEMORY_DB_PATH

    store = PromptReviewStore(MEMORY_DB_PATH)
    try:
        await store.initialize()
        summary = await store.run_prompt_review()
        logger.info(f"Weekly prompt review complete:\n{summary}")
        return summary
    except Exception as exc:
        logger.error(f"Weekly prompt review failed: {exc}")
        return f"PROMPT HEALTH: Review failed — {exc}"
    finally:
        await store.close()
