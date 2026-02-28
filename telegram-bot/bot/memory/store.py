import logging
import re
from pathlib import Path
from typing import Optional

import aiosqlite

from bot.memory.models import Memory

logger = logging.getLogger(__name__)

# SQLite busy-timeout in milliseconds — wait up to 5 seconds for locks
_BUSY_TIMEOUT_MS = 5000

# Maximum retries for transient SQLITE_BUSY errors that slip past busy_timeout
_MAX_RETRIES = 3

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


class MemoryStore:
    """Async SQLite memory store with full-text search."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

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

    # --- Health check & helpers ---

    async def health_check(self) -> dict:
        """Verify the DB is accessible and FTS index works.

        Returns a dict with 'healthy' (bool), 'db_ok', 'fts_ok', and 'error' (str|None).
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
        """Execute a SQL statement with retry logic for transient SQLITE_BUSY errors.

        Returns the cursor on success. Raises the final exception on exhaustion.
        """
        import asyncio as _asyncio

        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                cursor = await self._db.execute(sql, params or ())
                if is_write:
                    await self._db.commit()
                return cursor
            except Exception as exc:
                exc_str = str(exc).lower()
                if "locked" in exc_str or "busy" in exc_str:
                    last_exc = exc
                    wait = 0.1 * (2 ** attempt)  # 0.1s, 0.2s, 0.4s
                    logger.warning(
                        f"SQLite busy/locked (attempt {attempt + 1}/{_MAX_RETRIES}), "
                        f"retrying in {wait:.1f}s: {exc}"
                    )
                    await _asyncio.sleep(wait)
                else:
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
    ) -> list[Memory]:
        conditions = []
        params = []

        if query:
            safe_query = self._sanitize_fts_query(query)
            if safe_query:
                conditions.append(
                    "m.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
                )
                params.append(safe_query)
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
            return [Memory.from_row(row) for row in rows]
        except Exception as exc:
            logger.error(f"Memory search failed (query={query!r}): {exc}")
            # Graceful degradation: return empty results instead of crashing
            return []

    async def get_recent(self, limit: int = 20, project_key: str = None) -> list[Memory]:
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
            return [Memory.from_row(row) for row in rows]
        except Exception as exc:
            logger.error(f"get_recent failed: {exc}")
            return []

    async def get_action_items(self, resolved: bool = False) -> list[Memory]:
        try:
            cursor = await self._execute_with_retry(
                "SELECT * FROM memories WHERE category = 'action_item' AND resolved = ? "
                "ORDER BY created_at DESC",
                (int(resolved),),
            )
            rows = await cursor.fetchall()
            return [Memory.from_row(row) for row in rows]
        except Exception as exc:
            logger.error(f"get_action_items failed: {exc}")
            return []

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
                memories = await self.search(query, limit=limit, project_key=project_key)
            else:
                memories = await self.get_recent(limit=limit, project_key=project_key)

            if not memories:
                return "(No relevant memories found.)"

            lines = []
            for m in memories:
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
