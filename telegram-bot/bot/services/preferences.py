import logging

import aiosqlite

logger = logging.getLogger(__name__)

PREFERENCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_preferences (
    chat_id       INTEGER PRIMARY KEY,
    voice_enabled INTEGER DEFAULT 1,
    updated_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""


class PreferenceStore:
    """Per-chat user preferences (voice toggle, etc.)."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(PREFERENCES_SCHEMA)
        await self._db.commit()
        logger.info("Preferences table initialized")

    async def get_voice(self, chat_id: int) -> bool:
        cursor = await self._db.execute(
            "SELECT voice_enabled FROM user_preferences WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return True  # default: voice on
        return bool(row[0])

    async def set_voice(self, chat_id: int, enabled: bool) -> None:
        await self._db.execute(
            "INSERT INTO user_preferences (chat_id, voice_enabled, updated_at) "
            "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%S', 'now')) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "voice_enabled = excluded.voice_enabled, "
            "updated_at = excluded.updated_at",
            (chat_id, int(enabled)),
        )
        await self._db.commit()
