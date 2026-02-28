import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import aiosqlite

from bot.agents.resilience import is_transient_error, compute_backoff
from bot.memory.models import Memory

logger = logging.getLogger(__name__)

# SQLite busy-timeout in milliseconds — wait up to 5 seconds for locks
_BUSY_TIMEOUT_MS = 5000

# Maximum retries for transient errors (busy/locked, timeouts, etc.)
_MAX_RETRIES_TRANSIENT = 5

# Maximum retries for non-transient errors (no retries — just raise)
_MAX_RETRIES_DEFAULT = 1

# Window for health-status error counting (seconds)
_HEALTH_WINDOW_SECONDS = 300  # 5 minutes

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    category    TEXT NOT NULL,
    project_key TEXT,
    summary     TEXT NOT NULL,
    detail      TEXT,
    source      TEXT DEFAULT 'nimrod',
    tags        TEXT,
    resolved    INTEGER DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    summary,
    detail,
    content=memories,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, detail) VALUES (new.id, new.summary, new.detail);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, detail)
        VALUES('delete', old.id, old.summary, old.detail);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, detail)
        VALUES('delete', old.id, old.summary, old.detail);
    INSERT INTO memories_fts(rowid, summary, detail) VALUES (new.id, new.summary, new.detail);
END;
"""

# ---------------------------------------------------------------------------
# Substrings specific to SQLite transient errors (not covered by resilience.py)
# ---------------------------------------------------------------------------
_SQLITE_TRANSIENT_SUBSTRINGS = ("locked", "busy")


def _is_sqlite_transient(error_msg: str, exception: Exception | None = None) -> bool:
    """Check if an error is transient for SQLite purposes.

    Combines resilience.py's generic is_transient_error with SQLite-specific
    locked/busy detection.
    """
    if is_transient_error(error_msg, exception):
        return True
    msg_lower = (error_msg or "").lower()
    return any(sub in msg_lower for sub in _SQLITE_TRANSIENT_SUBSTRINGS)


# ---------------------------------------------------------------------------
# MemorySearchResult — structured result for memory queries
# ---------------------------------------------------------------------------


@dataclass
class MemorySearchResult:
    """Structured result from memory search operations.

    Supports iteration and truthiness so callers that expect a plain list
    continue to work without modification:
        for item in result:  # iterates over result.memories
        if result:           # True when memories is non-empty
        len(result)          # number of memories
    """

    success: bool
    memories: list[Memory] = field(default_factory=list)
    error: Optional[str] = None
    degraded: bool = False  # True if partial results (e.g., FTS failed, fell back to LIKE)

    # --- list-like protocol for backward compatibility ---

    def __iter__(self) -> Iterator[Memory]:
        return iter(self.memories)

    def __bool__(self) -> bool:
        return bool(self.memories)

    def __len__(self) -> int:
        return len(self.memories)

    def __getitem__(self, index):
        return self.memories[index]


class MemoryStore:
    """Async SQLite memory store with full-text search."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        # Error tracking for health status
        self._error_counts: dict[str, int] = {"search": 0, "recent": 0, "action_items": 0}
        self._error_timestamps: dict[str, list[float]] = {
            "search": [], "recent": [], "action_items": [],
        }

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        # Set busy_timeout so SQLite waits instead of raising SQLITE_BUSY immediately
        await self._db.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        # WAL mode improves concurrency (readers don't block writers)
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info(f"Memory store initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # --- Error tracking ---

    def _record_error(self, operation: str) -> None:
        """Record an error timestamp for health status tracking."""
        now = time.monotonic()
        if operation not in self._error_timestamps:
            self._error_timestamps[operation] = []
            self._error_counts[operation] = 0
        self._error_timestamps[operation].append(now)
        self._error_counts[operation] += 1

    def _recent_error_count(self) -> int:
        """Count total errors across all operations within the health window."""
        now = time.monotonic()
        cutoff = now - _HEALTH_WINDOW_SECONDS
        total = 0
        for operation in self._error_timestamps:
            # Prune old timestamps while counting
            recent = [t for t in self._error_timestamps[operation] if t > cutoff]
            self._error_timestamps[operation] = recent
            total += len(recent)
        return total

    def get_health_status(self) -> str:
        """Return memory system health based on recent error counts.

        Returns:
            "healthy"   - 0 errors in the last 5 minutes
            "degraded"  - 1-3 errors in the last 5 minutes
            "unhealthy" - 4+ errors in the last 5 minutes
        """
        count = self._recent_error_count()
        if count == 0:
            return "healthy"
        elif count <= 3:
            return "degraded"
        else:
            return "unhealthy"

    # --- Health check & helpers ---

    async def health_check(self) -> dict:
        """Verify the DB is accessible and FTS index works.

        Returns a dict with 'healthy' (bool), 'db_ok', 'fts_ok', 'error' (str|None),
        and 'status' (str: healthy/degraded/unhealthy).
        """
        result = {"healthy": False, "db_ok": False, "fts_ok": False, "error": None}
        try:
            # Basic DB access
            cursor = await self._db.execute("SELECT COUNT(*) FROM memories")
            row = await cursor.fetchone()
            result["db_ok"] = row is not None

            # FTS index integrity — run a trivial match query
            cursor = await self._db.execute(
                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ? LIMIT 1",
                ("test",),
            )
            await cursor.fetchall()
            result["fts_ok"] = True

            result["healthy"] = result["db_ok"] and result["fts_ok"]
        except Exception as exc:
            result["error"] = str(exc)
            logger.warning(f"Memory health check failed: {exc}")
        result["status"] = self.get_health_status()
        return result

    @staticmethod
    def _sanitize_fts_query(query: str) -> str:
        """Sanitize a user-provided string for safe use in FTS5 MATCH.

        FTS5 has its own query syntax where special characters (*, ", :, ^, etc.)
        can cause parse errors. This method wraps each token in double-quotes
        to force literal matching, and strips any characters that would break
        the quoting.
        """
        if not query or not query.strip():
            return ""
        # Remove characters that would break FTS5 even inside double-quotes
        cleaned = re.sub(r'["\x00]', " ", query)
        # Split into tokens and quote each one to prevent FTS5 syntax errors
        tokens = cleaned.split()
        if not tokens:
            return ""
        # Quote each token and join with spaces (implicit AND in FTS5)
        return " ".join(f'"{token}"' for token in tokens[:30])  # cap at 30 tokens

    async def _execute_with_retry(self, sql: str, params=None, *, is_write: bool = False):
        """Execute a SQL statement with retry logic for transient errors.

        Uses is_transient_error from resilience.py combined with SQLite-specific
        locked/busy detection. Transient errors get up to 5 retries with
        exponential backoff; non-transient errors are raised immediately.

        Returns the cursor on success. Raises the final exception on exhaustion.
        """
        import asyncio as _asyncio

        last_exc = None
        for attempt in range(_MAX_RETRIES_TRANSIENT):
            try:
                cursor = await self._db.execute(sql, params or ())
                if is_write:
                    await self._db.commit()
                return cursor
            except Exception as exc:
                exc_str = str(exc)
                if _is_sqlite_transient(exc_str, exc):
                    last_exc = exc
                    wait = compute_backoff(
                        attempt, base_delay=0.1, max_delay=5.0, jitter=True
                    )
                    logger.warning(
                        f"SQLite transient error (attempt {attempt + 1}/{_MAX_RETRIES_TRANSIENT}), "
                        f"retrying in {wait:.2f}s: {exc}"
                    )
                    await _asyncio.sleep(wait)
                else:
                    # Non-transient error — do not retry
                    raise
        raise last_exc  # type: ignore[misc]

    async def save(
        self,
        category: str,
        summary: str,
        detail: str = None,
        project_key: str = None,
        source: str = "nimrod",
        tags: str = None,
    ) -> int:
        cursor = await self._execute_with_retry(
            "INSERT INTO memories (category, project_key, summary, detail, source, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (category, project_key, summary, detail, source, tags),
            is_write=True,
        )
        logger.info(f"Memory saved: [{category}] {summary[:80]}")
        return cursor.lastrowid

    async def search(
        self,
        query: str,
        limit: int = 10,
        project_key: str = None,
        category: str = None,
    ) -> MemorySearchResult:
        conditions = []
        params: list = []
        fts_used = False
        degraded = False

        if query:
            safe_query = self._sanitize_fts_query(query)
            if safe_query:
                conditions.append(
                    "m.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
                )
                params.append(safe_query)
                fts_used = True
        if project_key:
            conditions.append("m.project_key = ?")
            params.append(project_key)
        if category:
            conditions.append("m.category = ?")
            params.append(category)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM memories m {where} ORDER BY m.created_at DESC LIMIT ?"
        params.append(limit)

        try:
            cursor = await self._execute_with_retry(sql, params)
            rows = await cursor.fetchall()
            return MemorySearchResult(
                success=True,
                memories=[Memory.from_row(row) for row in rows],
            )
        except Exception as exc:
            logger.error(f"Memory FTS search failed (query={query!r}): {exc}")

            # If FTS was used, fall back to LIKE-based search
            if fts_used:
                try:
                    fallback_result = await self._search_like_fallback(
                        query, limit, project_key, category
                    )
                    return fallback_result
                except Exception as fallback_exc:
                    logger.error(f"LIKE fallback also failed: {fallback_exc}")

            self._record_error("search")
            return MemorySearchResult(
                success=False,
                memories=[],
                error=f"Memory search failed: {exc}",
            )

    async def _search_like_fallback(
        self,
        query: str,
        limit: int = 10,
        project_key: str = None,
        category: str = None,
    ) -> MemorySearchResult:
        """Fallback search using LIKE when FTS5 MATCH fails."""
        logger.warning(f"FTS search failed, falling back to LIKE for query={query!r}")

        conditions = []
        params: list = []

        if query:
            like_pattern = f"%{query}%"
            conditions.append("(m.summary LIKE ? OR m.detail LIKE ?)")
            params.extend([like_pattern, like_pattern])
        if project_key:
            conditions.append("m.project_key = ?")
            params.append(project_key)
        if category:
            conditions.append("m.category = ?")
            params.append(category)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM memories m {where} ORDER BY m.created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._execute_with_retry(sql, params)
        rows = await cursor.fetchall()
        return MemorySearchResult(
            success=True,
            memories=[Memory.from_row(row) for row in rows],
            degraded=True,
        )

    async def get_recent(self, limit: int = 20, project_key: str = None) -> MemorySearchResult:
        try:
            if project_key:
                cursor = await self._execute_with_retry(
                    "SELECT * FROM memories WHERE project_key = ? ORDER BY created_at DESC LIMIT ?",
                    (project_key, limit),
                )
            else:
                cursor = await self._execute_with_retry(
                    "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return MemorySearchResult(
                success=True,
                memories=[Memory.from_row(row) for row in rows],
            )
        except Exception as exc:
            logger.error(f"get_recent failed: {exc}")
            self._record_error("recent")
            return MemorySearchResult(
                success=False,
                memories=[],
                error=f"get_recent failed: {exc}",
            )

    async def get_action_items(self, resolved: bool = False) -> MemorySearchResult:
        try:
            cursor = await self._execute_with_retry(
                "SELECT * FROM memories WHERE category = 'action_item' AND resolved = ? "
                "ORDER BY created_at DESC",
                (int(resolved),),
            )
            rows = await cursor.fetchall()
            return MemorySearchResult(
                success=True,
                memories=[Memory.from_row(row) for row in rows],
            )
        except Exception as exc:
            logger.error(f"get_action_items failed: {exc}")
            self._record_error("action_items")
            return MemorySearchResult(
                success=False,
                memories=[],
                error=f"get_action_items failed: {exc}",
            )

    async def resolve_action_item(self, memory_id: int) -> None:
        await self._execute_with_retry(
            "UPDATE memories SET resolved = 1 WHERE id = ?",
            (memory_id,),
            is_write=True,
        )

    async def format_for_prompt(
        self,
        query: str = None,
        project_key: str = None,
        limit: int = 15,
    ) -> str:
        """Format memories for prompt injection. Guaranteed to return a string and never raise."""
        try:
            if query:
                result = await self.search(query, limit=limit, project_key=project_key)
            else:
                result = await self.get_recent(limit=limit, project_key=project_key)

            # Handle failed search
            if not result.success:
                return (
                    "MEMORY UNAVAILABLE: Search failed. "
                    "Operating without historical context.\n"
                )

            if not result.memories:
                return "(No relevant memories found.)"

            lines = []

            # Prepend degradation warning if applicable
            if result.degraded:
                lines.append(
                    "MEMORY DEGRADED: Search results may be incomplete.\n"
                )

            for m in result.memories:
                try:
                    ts = m.created_at[:10] if m.created_at else "??"
                    proj = f"[{m.project_key}] " if m.project_key else ""
                    tag = f" #{m.category}" if m.category else ""
                    resolved = " [RESOLVED]" if m.resolved else ""
                    lines.append(f"- [{ts}] {proj}{m.summary}{tag}{resolved}")
                    if m.detail:
                        lines.append(f"  Detail: {m.detail[:200]}")
                except Exception as exc:
                    logger.warning(f"Skipping malformed memory row: {exc}")
                    continue

            return "\n".join(lines) if lines else "(No relevant memories found.)"
        except Exception as exc:
            logger.error(f"format_for_prompt failed: {exc}")
            return "(Memory unavailable.)"
