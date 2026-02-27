"""SQLite-backed message queue for Power Automate email/Teams integration."""

import logging
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS message_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    source TEXT NOT NULL,
    direction TEXT DEFAULT 'inbound',
    status TEXT DEFAULT 'new',
    sender TEXT,
    recipient TEXT,
    subject TEXT,
    body TEXT,
    channel TEXT,
    is_dm INTEGER DEFAULT 0,
    external_message_id TEXT,
    draft_response TEXT,
    approved_response TEXT,
    telegram_chat_id INTEGER,
    telegram_message_id INTEGER,
    project_key TEXT,
    processed_at TEXT,
    sent_at TEXT
);
"""


class MessageQueue:
    """Async SQLite queue for inbound/outbound email and Teams messages."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(QUEUE_SCHEMA)
        await self._db.commit()
        logger.info("Message queue table initialized")

    async def enqueue(
        self,
        source: str,
        sender: str = None,
        recipient: str = None,
        subject: str = None,
        body: str = None,
        channel: str = None,
        is_dm: bool = False,
        external_message_id: str = None,
        direction: str = "inbound",
    ) -> int:
        # ── Deduplication Layer ──────────────────────────────────────────
        # Primary dedup: check by external_message_id (e.g., IMAP Message-ID)
        if external_message_id:
            cursor = await self._db.execute(
                "SELECT id, status FROM message_queue WHERE external_message_id = ? LIMIT 1",
                (external_message_id,),
            )
            existing = await cursor.fetchone()
            if existing:
                logger.info(
                    f"Dedup (message_id): {external_message_id!r} already exists as "
                    f"queue item {existing['id']} (status={existing['status']}) — skipping"
                )
                return existing["id"]

        # Secondary dedup: same sender + subject + direction within last 2 hours
        # Catches re-forwarded emails where Message-ID changes
        if sender and subject:
            cursor = await self._db.execute(
                "SELECT id, status FROM message_queue "
                "WHERE sender = ? AND subject = ? AND direction = ? "
                "AND created_at > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-2 hours') LIMIT 1",
                (sender, subject, direction),
            )
            existing = await cursor.fetchone()
            if existing:
                logger.info(
                    f"Dedup (sender/subject): already queued as item {existing['id']} "
                    f"(status={existing['status']}) — skipping"
                )
                return existing["id"]
        # ── End Dedup ────────────────────────────────────────────────────

        cursor = await self._db.execute(
            "INSERT INTO message_queue "
            "(source, direction, sender, recipient, subject, body, channel, is_dm, external_message_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source, direction, sender, recipient, subject, body, channel, int(is_dm), external_message_id),
        )
        await self._db.commit()
        row_id = cursor.lastrowid
        logger.info(f"Queued {source} message id={row_id} from={sender} subject={subject!r}")
        return row_id

    async def get_pending(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM message_queue WHERE status = 'new' ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_by_id(self, queue_id: int) -> Optional[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM message_queue WHERE id = ?", (queue_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def update_draft(self, queue_id: int, draft_response: str) -> None:
        await self._db.execute(
            "UPDATE message_queue SET draft_response = ?, status = 'pending_approval', "
            "processed_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?",
            (draft_response, queue_id),
        )
        await self._db.commit()

    async def set_telegram_message(self, queue_id: int, chat_id: int, message_id: int) -> None:
        await self._db.execute(
            "UPDATE message_queue SET telegram_chat_id = ?, telegram_message_id = ? WHERE id = ?",
            (chat_id, message_id, queue_id),
        )
        await self._db.commit()

    async def approve(self, queue_id: int, response_text: str = None) -> None:
        item = await self.get_by_id(queue_id)
        if not item:
            return
        final_text = response_text or item["draft_response"]
        await self._db.execute(
            "UPDATE message_queue SET approved_response = ?, status = 'approved' WHERE id = ?",
            (final_text, queue_id),
        )
        await self._db.commit()
        logger.info(f"Queue item {queue_id} approved")

    async def reject(self, queue_id: int) -> None:
        await self._db.execute(
            "UPDATE message_queue SET status = 'rejected' WHERE id = ?",
            (queue_id,),
        )
        await self._db.commit()
        logger.info(f"Queue item {queue_id} rejected")

    async def mark_sent(self, queue_id: int) -> None:
        """Mark a single queue item as sent (called after successful email delivery)."""
        await self._db.execute(
            "UPDATE message_queue SET status = 'sent', "
            "sent_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?",
            (queue_id,),
        )
        await self._db.commit()
        logger.info(f"Queue item {queue_id} marked as sent")

    async def get_outbox(self, source: str = None) -> list[dict]:
        if source:
            cursor = await self._db.execute(
                "SELECT * FROM message_queue WHERE status = 'approved' AND source = ? "
                "ORDER BY created_at ASC",
                (source,),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM message_queue WHERE status = 'approved' ORDER BY created_at ASC"
            )
        rows = await cursor.fetchall()
        items = [dict(row) for row in rows]
        # Mark as sent
        for item in items:
            await self._db.execute(
                "UPDATE message_queue SET status = 'sent', "
                "sent_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?",
                (item["id"],),
            )
        await self._db.commit()
        return items
