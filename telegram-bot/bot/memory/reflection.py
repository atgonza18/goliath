"""Post-interaction reflection and self-scoring.

After every user interaction the orchestrator fires a lightweight,
non-blocking reflection that evaluates how well it handled the request.
All scoring is **heuristic-based** (no LLM call) to save tokens.

The stored reflections feed into:
  - V3 experience replay   (get_recent_reflections)
  - V4 prompt self-review   (get_low_scoring)
  - Quality dashboards       (get_average_score)

Storage: SQLite table ``reflections`` in the shared memory.db.
"""

import asyncio
import logging
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

# Telegram message guidelines (approximate)
_TELEGRAM_IDEAL_LEN = 2000   # Sweet spot for a single Telegram message
_TELEGRAM_MAX_LEN = 4096     # Telegram hard limit per message

REFLECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS reflections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    user_message_summary TEXT,
    response_summary    TEXT,
    agents_used         TEXT,
    total_tokens        INTEGER DEFAULT 0,
    duration_ms         INTEGER DEFAULT 0,
    reflection_text     TEXT,
    quality_score       INTEGER DEFAULT 3,
    categories          TEXT,
    tags                TEXT
);

CREATE INDEX IF NOT EXISTS idx_reflections_created ON reflections(created_at);
CREATE INDEX IF NOT EXISTS idx_reflections_score   ON reflections(quality_score);
"""


class ReflectionStore:
    """Persists and queries post-interaction reflections."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(REFLECTION_SCHEMA)
        await self._db.commit()
        logger.info("Reflection table initialized")

    # ------------------------------------------------------------------
    # Core: reflect on an interaction
    # ------------------------------------------------------------------

    async def reflect_on_interaction(
        self,
        user_message: str,
        response_text: str,
        agents_used: list[str],
        total_tokens: int = 0,
        duration_ms: int = 0,
        errors: Optional[list[str]] = None,
        subagent_count: int = 0,
        failed_subagents: int = 0,
    ) -> int:
        """Evaluate a completed interaction and persist the reflection.

        All scoring is heuristic — no LLM call is made.

        Returns the row id of the saved reflection.
        """
        errors = errors or []
        agents_str = ", ".join(agents_used) if agents_used else ""

        # --- Heuristic scoring ---
        score, positives, negatives = self._score(
            user_message=user_message,
            response_text=response_text,
            agents_used=agents_used,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            errors=errors,
            subagent_count=subagent_count,
            failed_subagents=failed_subagents,
        )

        # Build categories dict
        categories = {
            "went_well": positives,
            "could_improve": negatives,
        }

        # Build human-readable reflection text
        reflection_lines = [f"Score: {score}/5"]
        if positives:
            reflection_lines.append("Went well: " + "; ".join(positives))
        if negatives:
            reflection_lines.append("Could improve: " + "; ".join(negatives))
        reflection_text = "\n".join(reflection_lines)

        # Auto-generate tags
        tags = self._generate_tags(
            agents_used, errors, score, response_text, duration_ms
        )

        # Persist
        import json

        cursor = await self._db.execute(
            "INSERT INTO reflections "
            "(user_message_summary, response_summary, agents_used, "
            " total_tokens, duration_ms, reflection_text, quality_score, "
            " categories, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (user_message or "")[:200],
                (response_text or "")[:200],
                agents_str,
                total_tokens,
                duration_ms,
                reflection_text,
                score,
                json.dumps(categories),
                tags,
            ),
        )
        await self._db.commit()
        logger.info(
            f"Reflection saved: score={score}/5 agents=[{agents_str}] "
            f"tokens={total_tokens} duration={duration_ms}ms"
        )
        return cursor.lastrowid

    # ------------------------------------------------------------------
    # Scoring rubric (heuristic, NOT LLM-based)
    # ------------------------------------------------------------------

    @staticmethod
    def _score(
        *,
        user_message: str,
        response_text: str,
        agents_used: list[str],
        total_tokens: int,
        duration_ms: int,
        errors: list[str],
        subagent_count: int,
        failed_subagents: int,
    ) -> tuple[int, list[str], list[str]]:
        """Return (score, positives, negatives).

        Rubric:
          5 — Clean execution, relevant subagents, concise response, no errors
          4 — Good execution, minor inefficiency
          3 — Adequate but with issues
          2 — Problems (multiple failures, very long response)
          1 — Bad (crashed, no response, completely wrong routing)
        """
        positives: list[str] = []
        negatives: list[str] = []

        # Start at 5, deduct for issues
        score = 5

        # --- Check 1: Did we produce a response? ---
        resp_len = len(response_text or "")
        if resp_len == 0:
            negatives.append("No response produced")
            return 1, positives, negatives  # Immediately score 1

        # --- Check 2: Error rate ---
        error_count = len(errors)
        if error_count == 0 and failed_subagents == 0:
            positives.append("No errors")
        elif failed_subagents > 0 and failed_subagents < subagent_count:
            negatives.append(
                f"{failed_subagents}/{subagent_count} subagent(s) failed"
            )
            score -= 1  # Partial failure
        elif failed_subagents > 0 and failed_subagents >= subagent_count and subagent_count > 0:
            negatives.append("All dispatched subagents failed")
            score -= 2
        if error_count > 0:
            negatives.append(f"{error_count} error(s) occurred")
            score -= 1

        # --- Check 3: Response conciseness ---
        if resp_len <= _TELEGRAM_IDEAL_LEN:
            positives.append("Concise response")
        elif resp_len <= _TELEGRAM_MAX_LEN:
            negatives.append("Response slightly long")
            score -= 0  # Minor, no deduction
        elif resp_len <= _TELEGRAM_MAX_LEN * 2:
            negatives.append(
                f"Response too long ({resp_len} chars, needs chunking)"
            )
            score -= 1
        else:
            negatives.append(
                f"Response very long ({resp_len} chars, multiple chunks)"
            )
            score -= 1

        # --- Check 4: Token efficiency ---
        if total_tokens > 0:
            if total_tokens <= 5000:
                positives.append("Token-efficient")
            elif total_tokens <= 20000:
                pass  # Normal range, no note
            elif total_tokens <= 50000:
                negatives.append(f"High token usage ({total_tokens:,})")
                score -= 1
            else:
                negatives.append(f"Very high token usage ({total_tokens:,})")
                score -= 1

        # --- Check 5: Duration ---
        if duration_ms > 0:
            if duration_ms <= 15_000:
                positives.append("Fast response")
            elif duration_ms <= 60_000:
                pass  # Normal
            elif duration_ms <= 180_000:
                negatives.append(
                    f"Slow response ({duration_ms // 1000}s)"
                )
            else:
                negatives.append(
                    f"Very slow response ({duration_ms // 1000}s)"
                )
                score -= 1

        # --- Check 6: Subagent relevance (basic heuristic) ---
        if agents_used:
            positives.append(
                f"Dispatched {len(agents_used)} subagent(s): "
                + ", ".join(agents_used)
            )
        else:
            positives.append("Handled directly (no subagent dispatch needed)")

        # Clamp score to [1, 5]
        score = max(1, min(5, score))

        return score, positives, negatives

    # ------------------------------------------------------------------
    # Tag generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_tags(
        agents_used: list[str],
        errors: list[str],
        score: int,
        response_text: str,
        duration_ms: int,
    ) -> str:
        """Generate comma-separated tags for filtering/analysis."""
        tags = []

        if score >= 4:
            tags.append("good")
        elif score <= 2:
            tags.append("needs_review")

        if errors:
            tags.append("has_errors")

        if not agents_used:
            tags.append("direct_response")
        else:
            tags.append("subagent_dispatch")

        if duration_ms > 120_000:
            tags.append("slow")

        resp_len = len(response_text or "")
        if resp_len > _TELEGRAM_MAX_LEN * 2:
            tags.append("very_long")

        return ", ".join(tags)

    # ------------------------------------------------------------------
    # Query helpers (for V3 experience replay / V4 prompt self-review)
    # ------------------------------------------------------------------

    async def get_recent_reflections(self, limit: int = 20) -> list[dict]:
        """Return the N most recent reflections.

        Primary consumer: V3 experience replay.
        """
        cursor = await self._db.execute(
            "SELECT id, created_at, user_message_summary, response_summary, "
            "  agents_used, total_tokens, duration_ms, reflection_text, "
            "  quality_score, categories, tags "
            "FROM reflections ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def get_average_score(self, days: int = 7) -> dict:
        """Return average quality score and count for the last N days.

        Primary consumer: quality trend dashboards.
        """
        cursor = await self._db.execute(
            "SELECT COUNT(*), "
            "  COALESCE(AVG(quality_score), 0.0), "
            "  MIN(quality_score), "
            "  MAX(quality_score), "
            "  COALESCE(SUM(CASE WHEN quality_score >= 4 THEN 1 ELSE 0 END), 0), "
            "  COALESCE(SUM(CASE WHEN quality_score <= 2 THEN 1 ELSE 0 END), 0) "
            "FROM reflections "
            "WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )
        row = await cursor.fetchone()
        total = row[0] or 0
        return {
            "period_days": days,
            "total_interactions": total,
            "average_score": round(row[1], 2) if total > 0 else 0.0,
            "min_score": row[2] if total > 0 else None,
            "max_score": row[3] if total > 0 else None,
            "good_count": row[4],       # score >= 4
            "poor_count": row[5],       # score <= 2
        }

    async def get_low_scoring(
        self, threshold: int = 3, limit: int = 10
    ) -> list[dict]:
        """Return interactions scored at or below threshold.

        Primary consumer: V4 prompt self-review — find interactions to learn from.
        """
        cursor = await self._db.execute(
            "SELECT id, created_at, user_message_summary, response_summary, "
            "  agents_used, total_tokens, duration_ms, reflection_text, "
            "  quality_score, categories, tags "
            "FROM reflections "
            "WHERE quality_score <= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (threshold, limit),
        )
        rows = await cursor.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def get_score_distribution(self) -> dict:
        """Return count of reflections at each score level (1-5)."""
        cursor = await self._db.execute(
            "SELECT quality_score, COUNT(*) "
            "FROM reflections "
            "GROUP BY quality_score ORDER BY quality_score"
        )
        rows = await cursor.fetchall()
        dist = {i: 0 for i in range(1, 6)}
        for row in rows:
            if row[0] in dist:
                dist[row[0]] = row[1]
        return dist

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row) -> dict:
        import json

        categories_raw = row[9]
        try:
            categories = json.loads(categories_raw) if categories_raw else {}
        except (json.JSONDecodeError, TypeError):
            categories = {}

        return {
            "id": row[0],
            "created_at": row[1],
            "user_message_summary": row[2],
            "response_summary": row[3],
            "agents_used": row[4],
            "total_tokens": row[5],
            "duration_ms": row[6],
            "reflection_text": row[7],
            "quality_score": row[8],
            "categories": categories,
            "tags": row[10],
        }


# ======================================================================
# Fire-and-forget hook for the orchestrator
# ======================================================================

# Module-level reference — set during startup (same pattern as token_tracker)
_reflection_store: Optional[ReflectionStore] = None


def set_reflection_store(store: ReflectionStore) -> None:
    """Wire the global ReflectionStore reference (called at startup)."""
    global _reflection_store
    _reflection_store = store


def post_interaction_hook(
    user_message: str,
    response_text: str,
    agents_used: list[str],
    total_tokens: int = 0,
    duration_ms: int = 0,
    errors: Optional[list[str]] = None,
    subagent_count: int = 0,
    failed_subagents: int = 0,
) -> None:
    """Fire-and-forget reflection after the response has been sent.

    Creates an ``asyncio.Task`` wrapped in ``try/except`` so it **never**
    crashes the caller.  Safe to call even if the store is not initialised.
    """
    if _reflection_store is None:
        logger.debug("Reflection store not initialised — skipping reflection")
        return

    async def _run():
        try:
            await _reflection_store.reflect_on_interaction(
                user_message=user_message,
                response_text=response_text,
                agents_used=agents_used,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                errors=errors,
                subagent_count=subagent_count,
                failed_subagents=failed_subagents,
            )
        except Exception:
            logger.exception("Reflection failed (non-fatal)")

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        # No running event loop — nothing we can do
        logger.debug("No event loop — skipping reflection")
