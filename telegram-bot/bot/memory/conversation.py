import logging
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id         INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    token_estimate  INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_conv_chat_time
    ON conversation_turns(chat_id, created_at DESC);
"""


class ConversationStore:
    """Per-chat conversation history for prompt injection."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(CONVERSATION_SCHEMA)
        await self._db.commit()
        logger.info("Conversation store initialized")

    async def add_turn(self, chat_id: int, role: str, content: str) -> None:
        token_est = len(content) // 4
        await self._db.execute(
            "INSERT INTO conversation_turns (chat_id, role, content, token_estimate) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, role, content, token_est),
        )
        await self._db.commit()

    async def get_recent_turns(
        self, chat_id: int, max_turns: int = 20, max_tokens: int = 16000
    ) -> list[tuple[str, str]]:
        """Get recent turns bounded by turn count and token budget."""
        cursor = await self._db.execute(
            "SELECT role, content, token_estimate FROM conversation_turns "
            "WHERE chat_id = ? ORDER BY created_at DESC LIMIT ?",
            (chat_id, max_turns),
        )
        rows = await cursor.fetchall()

        selected = []
        budget = max_tokens
        for row in rows:
            cost = row[2] or (len(row[1]) // 4)
            if budget - cost < 0 and selected:
                break
            selected.append((row[0], row[1]))
            budget -= cost

        selected.reverse()
        return selected

    async def format_for_prompt(
        self, chat_id: int, max_turns: int = 20, max_tokens: int = 16000
    ) -> str:
        """Format conversation history as a prompt-injectable block."""
        turns = await self.get_recent_turns(chat_id, max_turns, max_tokens)
        if not turns:
            return ""

        lines = []
        for role, content in turns:
            label = "User" if role == "user" else "Nimrod"
            truncated = content[:2000] + "..." if len(content) > 2000 else content
            lines.append(f"{label}: {truncated}")

        return "CONVERSATION HISTORY (recent):\n" + "\n\n".join(lines)

    async def clear_chat(self, chat_id: int) -> int:
        cursor = await self._db.execute(
            "DELETE FROM conversation_turns WHERE chat_id = ?", (chat_id,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def cleanup_old(self, max_age_hours: int = 48) -> int:
        cursor = await self._db.execute(
            "DELETE FROM conversation_turns WHERE created_at < "
            "strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)",
            (f'-{max_age_hours} hours',),
        )
        await self._db.commit()
        deleted = cursor.rowcount
        if deleted:
            logger.info(f"Cleaned up {deleted} old conversation turns")
        return deleted
