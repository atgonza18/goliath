"""
Experience Replay — extracts lessons from past interactions.

Periodically reviews reflections (especially low-scoring ones) and generates
heuristic-based LESSONS that can be injected into future agent prompts.

This module CONSUMES data from the `reflections` table (created by
reflection.py, built by another agent) with expected columns:
    id, created_at, user_message_summary, response_summary, agents_used,
    total_tokens, duration_ms, reflection_text, quality_score (1-5),
    categories, tags

Lessons are stored in a `lessons_learned` table in the shared memory.db.

Designed to be scheduled via the bot's async scheduler (see
`run_experience_replay()` at module level).
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from bot.config import (
    EXPERIENCE_REPLAY_ENABLED,
    EXPERIENCE_REPLAY_MIN_REFLECTIONS,
    EXPERIENCE_REPLAY_MAX_LESSONS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

LESSONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons_learned (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    source_reflection_ids   TEXT NOT NULL,
    lesson_type             TEXT NOT NULL,
    lesson_text             TEXT NOT NULL,
    applicable_agents       TEXT,
    confidence              REAL NOT NULL DEFAULT 0.5,
    times_applied           INTEGER NOT NULL DEFAULT 0,
    last_applied_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_lessons_type ON lessons_learned(lesson_type);
CREATE INDEX IF NOT EXISTS idx_lessons_confidence ON lessons_learned(confidence);

-- Track which reflection IDs have already been analysed so we never
-- re-process them.  This is a simple set of IDs.
CREATE TABLE IF NOT EXISTS lessons_analysed_reflections (
    reflection_id   INTEGER PRIMARY KEY
);
"""

# ---------------------------------------------------------------------------
# Lesson types (enum-like constants)
# ---------------------------------------------------------------------------

LESSON_TYPES = {
    "routing_pattern",
    "response_quality",
    "efficiency",
    "error_handling",
    "agent_selection",
}


# ---------------------------------------------------------------------------
# Heuristic pattern detectors
# ---------------------------------------------------------------------------

def _detect_agent_reliability(grouped: dict) -> list[dict]:
    """If the same agent appears in multiple low-scoring interactions,
    flag a reliability concern."""
    lessons = []
    # Count how often each individual agent appears in low-score groups
    agent_fail_count: dict[str, list[int]] = defaultdict(list)
    for key, reflections in grouped.items():
        agents_str = key[0]  # agents_used part of the grouping key
        if not agents_str:
            continue
        for agent in _split_agents(agents_str):
            for r in reflections:
                agent_fail_count[agent].append(r["id"])

    for agent, ref_ids in agent_fail_count.items():
        if len(ref_ids) >= EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            unique_ids = sorted(set(ref_ids))
            lessons.append({
                "source_reflection_ids": ",".join(str(i) for i in unique_ids),
                "lesson_type": "agent_selection",
                "lesson_text": (
                    f"Agent '{agent}' appeared in {len(unique_ids)} low-scoring "
                    f"interactions. Review whether it is being routed appropriate "
                    f"task types or needs prompt improvements."
                ),
                "applicable_agents": agent,
                "confidence": _confidence_from_count(len(unique_ids)),
            })
    return lessons


def _detect_verbose_responses(grouped: dict) -> list[dict]:
    """If responses in a category group are consistently long (high tokens)
    relative to simple queries, suggest conciseness."""
    lessons = []
    for key, reflections in grouped.items():
        if len(reflections) < EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            continue
        # Check if most reflections mention verbosity clues
        # Use token counts as a proxy: if average tokens > 3000 it may be too verbose
        token_counts = [r.get("total_tokens") or 0 for r in reflections]
        avg_tokens = sum(token_counts) / len(token_counts) if token_counts else 0
        if avg_tokens > 3000:
            ids = sorted(r["id"] for r in reflections)
            categories = key[1] if key[1] else "general"
            lessons.append({
                "source_reflection_ids": ",".join(str(i) for i in ids),
                "lesson_type": "response_quality",
                "lesson_text": (
                    f"Responses for '{categories}' queries averaged {int(avg_tokens)} "
                    f"tokens across {len(ids)} low-scoring interactions. "
                    f"Consider more concise responses for this category."
                ),
                "applicable_agents": key[0] if key[0] else "nimrod",
                "confidence": _confidence_from_count(len(ids)),
            })
    return lessons


def _detect_over_dispatch(grouped: dict) -> list[dict]:
    """If multiple agents were dispatched for what looks like a simple query,
    suggest handling directly."""
    lessons = []
    for key, reflections in grouped.items():
        if len(reflections) < EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            continue
        agents_str = key[0]
        if not agents_str:
            continue
        agent_count = len(_split_agents(agents_str))
        if agent_count >= 3:
            ids = sorted(r["id"] for r in reflections)
            categories = key[1] if key[1] else "general"
            lessons.append({
                "source_reflection_ids": ",".join(str(i) for i in ids),
                "lesson_type": "routing_pattern",
                "lesson_text": (
                    f"Dispatched {agent_count} agents ({agents_str}) for "
                    f"'{categories}' queries {len(ids)} times with low scores. "
                    f"Consider handling these directly or with fewer agents."
                ),
                "applicable_agents": "nimrod",
                "confidence": _confidence_from_count(len(ids)),
            })
    return lessons


def _detect_high_token_simple(grouped: dict) -> list[dict]:
    """If token usage is high for short/simple user messages, suggest
    direct handling instead of subagent dispatch."""
    lessons = []
    for key, reflections in grouped.items():
        if len(reflections) < EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            continue
        # Look for cases where user messages are short but tokens are high
        high_cost = []
        for r in reflections:
            summary = r.get("user_message_summary") or ""
            tokens = r.get("total_tokens") or 0
            # Short user message (< 50 chars) but high token usage (> 2000)
            if len(summary) < 50 and tokens > 2000:
                high_cost.append(r)

        if len(high_cost) >= EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            ids = sorted(r["id"] for r in high_cost)
            agents_str = key[0] if key[0] else "unknown"
            lessons.append({
                "source_reflection_ids": ",".join(str(i) for i in ids),
                "lesson_type": "efficiency",
                "lesson_text": (
                    f"Simple queries (short user messages) dispatched to "
                    f"'{agents_str}' consumed excessive tokens "
                    f"({len(ids)} occurrences). Consider handling simple "
                    f"queries directly without subagent dispatch."
                ),
                "applicable_agents": "nimrod",
                "confidence": _confidence_from_count(len(ids)),
            })
    return lessons


def _detect_error_patterns(grouped: dict) -> list[dict]:
    """If reflection text mentions errors/failures repeatedly, flag it."""
    ERROR_KEYWORDS = {"error", "failed", "exception", "timeout", "crash", "traceback"}
    lessons = []
    for key, reflections in grouped.items():
        if len(reflections) < EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            continue
        error_refs = []
        for r in reflections:
            text = (r.get("reflection_text") or "").lower()
            if any(kw in text for kw in ERROR_KEYWORDS):
                error_refs.append(r)

        if len(error_refs) >= EXPERIENCE_REPLAY_MIN_REFLECTIONS:
            ids = sorted(r["id"] for r in error_refs)
            agents_str = key[0] if key[0] else "unknown"
            lessons.append({
                "source_reflection_ids": ",".join(str(i) for i in ids),
                "lesson_type": "error_handling",
                "lesson_text": (
                    f"Repeated error/failure patterns detected in "
                    f"'{agents_str}' interactions ({len(ids)} occurrences). "
                    f"Investigate root cause and add error handling or "
                    f"fallback strategies."
                ),
                "applicable_agents": agents_str,
                "confidence": _confidence_from_count(len(ids)),
            })
    return lessons


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_agents(agents_str: str) -> list[str]:
    """Split a comma-separated agents_used string into a clean list."""
    if not agents_str:
        return []
    return [a.strip() for a in agents_str.split(",") if a.strip()]


def _confidence_from_count(count: int) -> float:
    """Map occurrence count to a confidence score (0.0 - 1.0).

    2 occurrences  -> 0.4  (minimum threshold, low confidence)
    3 occurrences  -> 0.55
    5 occurrences  -> 0.7
    10+ occurrences -> 0.9 (capped)
    """
    if count <= 1:
        return 0.2
    # Logarithmic scaling: confidence = 0.3 + 0.2 * ln(count)
    import math
    raw = 0.3 + 0.2 * math.log(count)
    return round(min(max(raw, 0.2), 0.95), 2)


# ---------------------------------------------------------------------------
# Main Store class
# ---------------------------------------------------------------------------

class ExperienceReplayStore:
    """Extracts and stores lessons from past interaction reflections.

    Uses the shared memory.db SQLite database. Follows the same pattern as
    ActivityLogStore and TokenTracker: receives an aiosqlite connection and
    creates its own tables.
    """

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        """Create the lessons_learned table if it doesn't exist."""
        await self._db.executescript(LESSONS_SCHEMA)
        await self._db.commit()
        logger.info("Experience replay tables initialized")

    # ------------------------------------------------------------------
    # Core: extract lessons from reflections
    # ------------------------------------------------------------------

    async def extract_lessons(self) -> list[dict]:
        """Analyse unprocessed low-scoring reflections and generate lessons.

        Returns a list of newly created lesson dicts.
        """
        if not EXPERIENCE_REPLAY_ENABLED:
            logger.info("Experience replay is disabled, skipping extraction")
            return []

        # Check if reflections table exists (it may not be created yet)
        if not await self._reflections_table_exists():
            logger.info(
                "Reflections table does not exist yet — skipping lesson extraction. "
                "This is expected if reflection.py has not been deployed."
            )
            return []

        # Fetch low-scoring reflections that haven't been analysed
        unanalysed = await self._get_unanalysed_reflections()
        if not unanalysed:
            logger.info("No new low-scoring reflections to analyse")
            return []

        logger.info(f"Analysing {len(unanalysed)} unprocessed reflections for lessons")

        # Group reflections by (agents_used, categories) pattern
        grouped = self._group_reflections(unanalysed)

        # Run all heuristic detectors
        new_lessons: list[dict] = []
        new_lessons.extend(_detect_agent_reliability(grouped))
        new_lessons.extend(_detect_verbose_responses(grouped))
        new_lessons.extend(_detect_over_dispatch(grouped))
        new_lessons.extend(_detect_high_token_simple(grouped))
        new_lessons.extend(_detect_error_patterns(grouped))

        # Deduplicate against existing lessons (same type + same agents)
        new_lessons = await self._deduplicate_lessons(new_lessons)

        # Save new lessons
        saved = []
        for lesson in new_lessons:
            lesson_id = await self._save_lesson(lesson)
            lesson["id"] = lesson_id
            saved.append(lesson)
            logger.info(
                f"New lesson [{lesson['lesson_type']}] (confidence={lesson['confidence']}): "
                f"{lesson['lesson_text'][:100]}..."
            )

        # Mark all fetched reflections as analysed
        reflection_ids = [r["id"] for r in unanalysed]
        await self._mark_reflections_analysed(reflection_ids)

        # Enforce max lessons cap
        await self._enforce_max_lessons()

        logger.info(
            f"Experience replay complete: analysed {len(unanalysed)} reflections, "
            f"generated {len(saved)} new lessons"
        )
        return saved

    # ------------------------------------------------------------------
    # Read: get lessons for consumers
    # ------------------------------------------------------------------

    async def get_applicable_lessons(
        self,
        agent_name: Optional[str] = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return lessons relevant to a specific agent (or all agents).

        Ordered by confidence * (1 + times_applied) descending — lessons
        that have been validated by repeated application rank higher.
        """
        if agent_name:
            # Match lessons where applicable_agents contains the agent name
            # or is empty/NULL (applies to all agents)
            cursor = await self._db.execute(
                "SELECT id, created_at, source_reflection_ids, lesson_type, "
                "       lesson_text, applicable_agents, confidence, "
                "       times_applied, last_applied_at "
                "FROM lessons_learned "
                "WHERE applicable_agents LIKE ? "
                "   OR applicable_agents IS NULL "
                "   OR applicable_agents = '' "
                "ORDER BY (confidence * (1 + times_applied)) DESC "
                "LIMIT ?",
                (f"%{agent_name}%", limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, created_at, source_reflection_ids, lesson_type, "
                "       lesson_text, applicable_agents, confidence, "
                "       times_applied, last_applied_at "
                "FROM lessons_learned "
                "ORDER BY (confidence * (1 + times_applied)) DESC "
                "LIMIT ?",
                (limit,),
            )

        rows = await cursor.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def get_lessons_for_prompt_injection(self, limit: int = 3) -> str:
        """Return the top lessons formatted for injection into agent prompts.

        Returns a short block (under 500 chars) suitable for prepending to
        the Nimrod system prompt. Returns empty string if no lessons exist.
        """
        lessons = await self.get_applicable_lessons(agent_name="nimrod", limit=limit)
        if not lessons:
            return ""

        lines = ["LESSONS FROM EXPERIENCE:"]
        char_budget = 450  # leave room for the header
        for lesson in lessons:
            # Truncate individual lesson text to keep the block compact
            text = lesson["lesson_text"]
            if len(text) > 140:
                text = text[:137] + "..."
            line = f"- {text}"
            if len("\n".join(lines + [line])) > char_budget:
                break
            lines.append(line)

        if len(lines) <= 1:
            return ""  # only header, no lessons fit

        return "\n".join(lines)

    async def mark_lesson_applied(self, lesson_id: int) -> None:
        """Increment times_applied and update last_applied_at timestamp."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        await self._db.execute(
            "UPDATE lessons_learned "
            "SET times_applied = times_applied + 1, last_applied_at = ? "
            "WHERE id = ?",
            (now, lesson_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Stats / introspection
    # ------------------------------------------------------------------

    async def get_stats(self) -> dict:
        """Return summary statistics about the lessons store."""
        cursor = await self._db.execute(
            "SELECT COUNT(*), "
            "  AVG(confidence), "
            "  SUM(times_applied), "
            "  COUNT(DISTINCT lesson_type) "
            "FROM lessons_learned"
        )
        row = await cursor.fetchone()

        # Count analysed reflections
        cursor2 = await self._db.execute(
            "SELECT COUNT(*) FROM lessons_analysed_reflections"
        )
        analysed_row = await cursor2.fetchone()

        return {
            "total_lessons": row[0] or 0,
            "avg_confidence": round(row[1] or 0, 2),
            "total_applications": row[2] or 0,
            "lesson_types_used": row[3] or 0,
            "reflections_analysed": analysed_row[0] or 0,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _reflections_table_exists(self) -> bool:
        """Check if the reflections table has been created by reflection.py."""
        try:
            cursor = await self._db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='reflections'"
            )
            row = await cursor.fetchone()
            return row is not None
        except Exception as exc:
            logger.warning(f"Error checking for reflections table: {exc}")
            return False

    async def _get_unanalysed_reflections(self) -> list[dict]:
        """Fetch reflections with quality_score <= 3 that haven't been
        analysed yet."""
        try:
            cursor = await self._db.execute(
                "SELECT r.id, r.created_at, r.user_message_summary, "
                "       r.response_summary, r.agents_used, r.total_tokens, "
                "       r.duration_ms, r.reflection_text, r.quality_score, "
                "       r.categories, r.tags "
                "FROM reflections r "
                "LEFT JOIN lessons_analysed_reflections lar ON r.id = lar.reflection_id "
                "WHERE r.quality_score <= 3 "
                "  AND lar.reflection_id IS NULL "
                "ORDER BY r.created_at DESC "
                "LIMIT 200"
            )
            rows = await cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "created_at": row[1],
                    "user_message_summary": row[2],
                    "response_summary": row[3],
                    "agents_used": row[4],
                    "total_tokens": row[5],
                    "duration_ms": row[6],
                    "reflection_text": row[7],
                    "quality_score": row[8],
                    "categories": row[9],
                    "tags": row[10],
                }
                for row in rows
            ]
        except Exception as exc:
            logger.error(f"Failed to fetch unanalysed reflections: {exc}")
            return []

    @staticmethod
    def _group_reflections(reflections: list[dict]) -> dict:
        """Group reflections by (agents_used, categories) pattern.

        Returns a dict mapping (agents_key, categories_key) to a list of
        reflection dicts.
        """
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in reflections:
            # Normalise agents_used: sort alphabetically for consistent grouping
            agents_raw = r.get("agents_used") or ""
            agents_key = ",".join(sorted(_split_agents(agents_raw)))

            # Use categories as-is (already a string)
            cat_key = (r.get("categories") or "").strip().lower()

            grouped[(agents_key, cat_key)].append(r)
        return grouped

    async def _deduplicate_lessons(self, new_lessons: list[dict]) -> list[dict]:
        """Remove lessons that duplicate existing ones (same type + agents)."""
        if not new_lessons:
            return []

        # Fetch existing lessons for comparison
        cursor = await self._db.execute(
            "SELECT lesson_type, applicable_agents, lesson_text "
            "FROM lessons_learned"
        )
        existing = await cursor.fetchall()
        existing_keys = {
            (row[0], (row[1] or "").lower())
            for row in existing
        }

        deduplicated = []
        for lesson in new_lessons:
            key = (
                lesson["lesson_type"],
                (lesson.get("applicable_agents") or "").lower(),
            )
            if key not in existing_keys:
                deduplicated.append(lesson)
                existing_keys.add(key)  # prevent intra-batch dupes too
            else:
                logger.debug(
                    f"Skipping duplicate lesson: [{lesson['lesson_type']}] "
                    f"for {lesson.get('applicable_agents')}"
                )

        return deduplicated

    async def _save_lesson(self, lesson: dict) -> int:
        """Insert a lesson into the lessons_learned table. Returns row id."""
        cursor = await self._db.execute(
            "INSERT INTO lessons_learned "
            "(source_reflection_ids, lesson_type, lesson_text, "
            " applicable_agents, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                lesson["source_reflection_ids"],
                lesson["lesson_type"],
                lesson["lesson_text"],
                lesson.get("applicable_agents"),
                lesson["confidence"],
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def _mark_reflections_analysed(self, reflection_ids: list[int]) -> None:
        """Record that these reflection IDs have been processed."""
        if not reflection_ids:
            return
        for rid in reflection_ids:
            try:
                await self._db.execute(
                    "INSERT OR IGNORE INTO lessons_analysed_reflections "
                    "(reflection_id) VALUES (?)",
                    (rid,),
                )
            except Exception as exc:
                logger.warning(f"Failed to mark reflection {rid} as analysed: {exc}")
        await self._db.commit()

    async def _enforce_max_lessons(self) -> None:
        """Prune oldest low-confidence lessons if we exceed the cap."""
        cursor = await self._db.execute("SELECT COUNT(*) FROM lessons_learned")
        row = await cursor.fetchone()
        total = row[0] or 0

        if total <= EXPERIENCE_REPLAY_MAX_LESSONS:
            return

        excess = total - EXPERIENCE_REPLAY_MAX_LESSONS
        # Delete the lowest-confidence, least-applied lessons first
        await self._db.execute(
            "DELETE FROM lessons_learned WHERE id IN ("
            "  SELECT id FROM lessons_learned "
            "  ORDER BY confidence ASC, times_applied ASC "
            "  LIMIT ?"
            ")",
            (excess,),
        )
        await self._db.commit()
        logger.info(f"Pruned {excess} low-confidence lessons (cap={EXPERIENCE_REPLAY_MAX_LESSONS})")

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convert an aiosqlite Row to a plain dict."""
        return {
            "id": row[0],
            "created_at": row[1],
            "source_reflection_ids": row[2],
            "lesson_type": row[3],
            "lesson_text": row[4],
            "applicable_agents": row[5],
            "confidence": row[6],
            "times_applied": row[7],
            "last_applied_at": row[8],
        }


# ---------------------------------------------------------------------------
# Schedulable entry point
# ---------------------------------------------------------------------------

async def run_experience_replay(db_path: Optional[str] = None) -> list[dict]:
    """Run the experience replay pipeline.

    Designed to be called by the bot scheduler at 2:00 AM CT daily.
    Can also be invoked manually for testing.

    Args:
        db_path: Path to the SQLite database. If None, uses the default
                 MEMORY_DB_PATH from config.

    Returns:
        List of newly generated lesson dicts.
    """
    from bot.config import MEMORY_DB_PATH

    if not EXPERIENCE_REPLAY_ENABLED:
        logger.info("Experience replay disabled via config")
        return []

    path = db_path or str(MEMORY_DB_PATH)
    logger.info(f"Starting experience replay pipeline (db={path})")

    try:
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA busy_timeout = 5000")
            await db.execute("PRAGMA journal_mode = WAL")

            store = ExperienceReplayStore(db)
            await store.initialize()
            lessons = await store.extract_lessons()

            stats = await store.get_stats()
            logger.info(
                f"Experience replay stats: {stats['total_lessons']} lessons, "
                f"{stats['reflections_analysed']} reflections analysed, "
                f"avg confidence {stats['avg_confidence']}"
            )

            return lessons
    except Exception as exc:
        logger.error(f"Experience replay pipeline failed: {exc}", exc_info=True)
        return []
