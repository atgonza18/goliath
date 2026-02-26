import logging
from pathlib import Path
from typing import Optional

import aiosqlite

from bot.memory.models import Memory

logger = logging.getLogger(__name__)

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
        await self._db.executescript(SCHEMA_SQL)
        await self._db.commit()
        logger.info(f"Memory store initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def save(
        self,
        category: str,
        summary: str,
        detail: str = None,
        project_key: str = None,
        source: str = "nimrod",
        tags: str = None,
    ) -> int:
        cursor = await self._db.execute(
            "INSERT INTO memories (category, project_key, summary, detail, source, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (category, project_key, summary, detail, source, tags),
        )
        await self._db.commit()
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
            conditions.append(
                "m.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"
            )
            params.append(query)
        if project_key:
            conditions.append("m.project_key = ?")
            params.append(project_key)
        if category:
            conditions.append("m.category = ?")
            params.append(category)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM memories m {where} ORDER BY m.created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [Memory.from_row(row) for row in rows]

    async def get_recent(self, limit: int = 20, project_key: str = None) -> list[Memory]:
        if project_key:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE project_key = ? ORDER BY created_at DESC LIMIT ?",
                (project_key, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [Memory.from_row(row) for row in rows]

    async def get_action_items(self, resolved: bool = False) -> list[Memory]:
        cursor = await self._db.execute(
            "SELECT * FROM memories WHERE category = 'action_item' AND resolved = ? "
            "ORDER BY created_at DESC",
            (int(resolved),),
        )
        rows = await cursor.fetchall()
        return [Memory.from_row(row) for row in rows]

    async def resolve_action_item(self, memory_id: int) -> None:
        await self._db.execute(
            "UPDATE memories SET resolved = 1 WHERE id = ?", (memory_id,)
        )
        await self._db.commit()

    async def format_for_prompt(
        self,
        query: str = None,
        project_key: str = None,
        limit: int = 15,
    ) -> str:
        if query:
            memories = await self.search(query, limit=limit, project_key=project_key)
        else:
            memories = await self.get_recent(limit=limit, project_key=project_key)

        if not memories:
            return "(No relevant memories found.)"

        lines = []
        for m in memories:
            ts = m.created_at[:10]
            proj = f"[{m.project_key}] " if m.project_key else ""
            tag = f" #{m.category}" if m.category else ""
            resolved = " [RESOLVED]" if m.resolved else ""
            lines.append(f"- [{ts}] {proj}{m.summary}{tag}{resolved}")
            if m.detail:
                lines.append(f"  Detail: {m.detail[:200]}")

        return "\n".join(lines)
