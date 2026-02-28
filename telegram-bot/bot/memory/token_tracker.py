"""Token usage tracking for agent calls.

Records input/output tokens, cost, model, and agent name for every
subagent invocation so we can monitor spend and optimise prompts.

Storage: SQLite table in the shared memory.db (same connection as
ActivityLogStore, ConversationStore, etc.).
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

TOKEN_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    agent_name      TEXT NOT NULL,
    model           TEXT,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cache_read_tokens   INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    cost_usd        REAL,
    duration_ms     INTEGER,
    num_turns       INTEGER,
    session_id      TEXT,
    task_summary    TEXT,
    backend         TEXT DEFAULT 'sdk'
);

CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_token_usage_agent   ON token_usage(agent_name);
"""


@dataclass
class TokenUsage:
    """Captures token counts and cost for a single agent call."""

    agent_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int = 0
    model: Optional[str] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None
    session_id: Optional[str] = None
    task_summary: Optional[str] = None
    backend: str = "sdk"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @classmethod
    def from_sdk_result(
        cls,
        agent_name: str,
        result_message: Any,
        model: Optional[str] = None,
        task_summary: Optional[str] = None,
    ) -> "TokenUsage":
        """Build a TokenUsage from a claude_agent_sdk ResultMessage.

        ResultMessage fields we care about:
          - usage: dict | None  (e.g. {"input_tokens": N, "output_tokens": N, ...})
          - total_cost_usd: float | None
          - duration_ms: int
          - num_turns: int
          - session_id: str
        """
        usage = getattr(result_message, "usage", None) or {}
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)

        return cls(
            agent_name=agent_name,
            input_tokens=input_tok,
            output_tokens=output_tok,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_create,
            total_tokens=input_tok + output_tok,
            model=model,
            cost_usd=getattr(result_message, "total_cost_usd", None),
            duration_ms=getattr(result_message, "duration_ms", None),
            num_turns=getattr(result_message, "num_turns", None),
            session_id=getattr(result_message, "session_id", None),
            task_summary=task_summary,
            backend="sdk",
        )


class TokenTracker:
    """Persists and queries token usage records."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(TOKEN_USAGE_SCHEMA)
        await self._db.commit()
        logger.info("Token usage table initialized")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def log_usage(self, usage: TokenUsage) -> int:
        """Insert a single token-usage record. Returns the row id."""
        cursor = await self._db.execute(
            "INSERT INTO token_usage "
            "(agent_name, model, input_tokens, output_tokens, "
            " cache_read_tokens, cache_creation_tokens, total_tokens, "
            " cost_usd, duration_ms, num_turns, session_id, "
            " task_summary, backend) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                usage.agent_name,
                usage.model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_creation_tokens,
                usage.total_tokens,
                usage.cost_usd,
                usage.duration_ms,
                usage.num_turns,
                usage.session_id,
                (usage.task_summary or "")[:500],
                usage.backend,
            ),
        )
        await self._db.commit()
        return cursor.lastrowid

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get_daily_total(self, date_str: Optional[str] = None) -> dict:
        """Aggregate token usage for a given date (YYYY-MM-DD), default today."""
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cursor = await self._db.execute(
            "SELECT COUNT(*), "
            "  COALESCE(SUM(input_tokens), 0), "
            "  COALESCE(SUM(output_tokens), 0), "
            "  COALESCE(SUM(total_tokens), 0), "
            "  COALESCE(SUM(cost_usd), 0.0), "
            "  COALESCE(SUM(cache_read_tokens), 0), "
            "  COALESCE(SUM(cache_creation_tokens), 0) "
            "FROM token_usage "
            "WHERE created_at LIKE ? || '%'",
            (date_str,),
        )
        row = await cursor.fetchone()
        return {
            "date": date_str,
            "calls": row[0] or 0,
            "input_tokens": row[1],
            "output_tokens": row[2],
            "total_tokens": row[3],
            "cost_usd": round(row[4], 4),
            "cache_read_tokens": row[5],
            "cache_creation_tokens": row[6],
        }

    async def get_by_agent(self, agent_name: str, limit: int = 20) -> list[dict]:
        """Return the most recent token records for a specific agent."""
        cursor = await self._db.execute(
            "SELECT id, created_at, model, input_tokens, output_tokens, "
            "  total_tokens, cost_usd, duration_ms, num_turns, backend, task_summary "
            "FROM token_usage WHERE agent_name = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (agent_name, limit),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "model": r[2],
                "input_tokens": r[3],
                "output_tokens": r[4],
                "total_tokens": r[5],
                "cost_usd": r[6],
                "duration_ms": r[7],
                "num_turns": r[8],
                "backend": r[9],
                "task_summary": r[10],
            }
            for r in rows
        ]

    async def get_summary(self, days: int = 7) -> dict:
        """Return aggregate stats for the last N days, broken down by agent."""
        # Overall totals
        cursor = await self._db.execute(
            "SELECT COUNT(*), "
            "  COALESCE(SUM(input_tokens), 0), "
            "  COALESCE(SUM(output_tokens), 0), "
            "  COALESCE(SUM(total_tokens), 0), "
            "  COALESCE(SUM(cost_usd), 0.0) "
            "FROM token_usage "
            "WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )
        totals = await cursor.fetchone()

        # Per-agent breakdown
        cursor = await self._db.execute(
            "SELECT agent_name, COUNT(*), "
            "  COALESCE(SUM(input_tokens), 0), "
            "  COALESCE(SUM(output_tokens), 0), "
            "  COALESCE(SUM(total_tokens), 0), "
            "  COALESCE(SUM(cost_usd), 0.0), "
            "  AVG(total_tokens) "
            "FROM token_usage "
            "WHERE created_at >= datetime('now', ?) "
            "GROUP BY agent_name ORDER BY SUM(total_tokens) DESC",
            (f"-{days} days",),
        )
        agents = await cursor.fetchall()

        return {
            "period_days": days,
            "total_calls": totals[0] or 0,
            "total_input_tokens": totals[1],
            "total_output_tokens": totals[2],
            "total_tokens": totals[3],
            "total_cost_usd": round(totals[4], 4),
            "by_agent": [
                {
                    "agent": a[0],
                    "calls": a[1],
                    "input_tokens": a[2],
                    "output_tokens": a[3],
                    "total_tokens": a[4],
                    "cost_usd": round(a[5], 4),
                    "avg_tokens_per_call": int(a[6] or 0),
                }
                for a in agents
            ],
        }

    async def get_recent(self, limit: int = 10) -> list[dict]:
        """Return the N most recent token usage records (all agents)."""
        cursor = await self._db.execute(
            "SELECT id, created_at, agent_name, model, input_tokens, "
            "  output_tokens, total_tokens, cost_usd, duration_ms, "
            "  num_turns, backend, task_summary "
            "FROM token_usage ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "agent_name": r[2],
                "model": r[3],
                "input_tokens": r[4],
                "output_tokens": r[5],
                "total_tokens": r[6],
                "cost_usd": r[7],
                "duration_ms": r[8],
                "num_turns": r[9],
                "backend": r[10],
                "task_summary": r[11],
            }
            for r in rows
        ]
