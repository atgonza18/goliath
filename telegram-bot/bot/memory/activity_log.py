import json
import logging
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

ACTIVITY_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    chat_id              INTEGER,
    query                TEXT,
    nimrod_pass1_duration REAL,
    subagents_dispatched TEXT,
    subagent_results     TEXT,
    nimrod_pass2_duration REAL,
    total_duration       REAL,
    success              INTEGER DEFAULT 1,
    error                TEXT
);
"""


class ActivityLogStore:
    """Logs every orchestration run for /logs command."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(ACTIVITY_LOG_SCHEMA)
        await self._db.commit()
        logger.info("Activity log table initialized")

    async def log_run(
        self,
        chat_id: int,
        query: str,
        total_duration: float,
        success: bool = True,
        error: str = None,
        nimrod_pass1_duration: float = None,
        nimrod_pass2_duration: float = None,
        subagent_results: list[dict] = None,
    ) -> int:
        dispatched = ""
        results_json = ""
        if subagent_results:
            dispatched = ", ".join(r["agent"] for r in subagent_results)
            results_json = json.dumps(subagent_results)

        cursor = await self._db.execute(
            "INSERT INTO activity_log "
            "(chat_id, query, nimrod_pass1_duration, subagents_dispatched, "
            " subagent_results, nimrod_pass2_duration, total_duration, success, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                query[:500],
                nimrod_pass1_duration,
                dispatched,
                results_json,
                nimrod_pass2_duration,
                total_duration,
                int(success),
                error[:1000] if error else None,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_recent(self, limit: int = 10) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT id, created_at, query, subagents_dispatched, "
            "total_duration, success, error "
            "FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "created_at": r[1],
                "query": r[2],
                "subagents": r[3],
                "total_duration": r[4],
                "success": bool(r[5]),
                "error": r[6],
            })
        return results

    async def get_detail(self, log_id: int) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM activity_log WHERE id = ?", (log_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "created_at": row[1],
            "chat_id": row[2],
            "query": row[3],
            "nimrod_pass1_duration": row[4],
            "subagents_dispatched": row[5],
            "subagent_results": json.loads(row[6]) if row[6] else [],
            "nimrod_pass2_duration": row[7],
            "total_duration": row[8],
            "success": bool(row[9]),
            "error": row[10],
        }

    async def get_agent_usage_counts(self, days: int = 30) -> dict:
        """Return per-agent usage counts and last-used timestamps.

        Parses the comma-separated subagents_dispatched column.
        Returns {agent_name: {"count": N, "last_used": "YYYY-MM-DDTHH:MM:SS"}}.
        """
        cursor = await self._db.execute(
            "SELECT subagents_dispatched, created_at "
            "FROM activity_log "
            "WHERE success = 1 "
            "  AND subagents_dispatched IS NOT NULL "
            "  AND subagents_dispatched != '' "
            "  AND created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )
        rows = await cursor.fetchall()

        counts: dict[str, dict] = {}
        for row in rows:
            dispatched_str = row[0]
            created_at = row[1]
            agents = [a.strip() for a in dispatched_str.split(",") if a.strip()]
            for agent in agents:
                if agent not in counts:
                    counts[agent] = {"count": 0, "last_used": created_at}
                counts[agent]["count"] += 1
                if created_at > counts[agent]["last_used"]:
                    counts[agent]["last_used"] = created_at

        return counts

    async def get_stats(self) -> dict:
        cursor = await self._db.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), "
            "AVG(total_duration), "
            "MAX(total_duration) "
            "FROM activity_log"
        )
        row = await cursor.fetchone()
        return {
            "total_runs": row[0] or 0,
            "successful": row[1] or 0,
            "avg_duration": row[2] or 0.0,
            "max_duration": row[3] or 0.0,
        }

    async def get_period_stats(self, days: int = 30) -> dict:
        """Return aggregate stats for a rolling window of N days."""
        cursor = await self._db.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END), "
            "AVG(total_duration), "
            "MAX(total_duration) "
            "FROM activity_log "
            "WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )
        row = await cursor.fetchone()
        total = row[0] or 0
        successful = row[1] or 0
        return {
            "total_runs": total,
            "successful": successful,
            "success_rate": round(successful / total * 100) if total else 0,
            "avg_duration": round(row[2] or 0, 1),
            "max_duration": round(row[3] or 0, 1),
        }
