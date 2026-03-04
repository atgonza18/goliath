"""Recall.ai Meeting Bot integration — automated Teams call transcription.

Flow:
1. User sends a Teams meeting link (via Telegram command or message)
2. We call Recall.ai API to create a bot that joins the meeting
3. Bot records and transcribes the meeting
4. When the meeting ends, Recall.ai sends a webhook to /webhook/recall
5. We fetch the full transcript and feed it into the transcript processor
6. Results (summary, action items, constraints) are sent to the user
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

import aiohttp
import aiosqlite

from bot.config import (
    RECALL_API_KEY,
    RECALL_API_BASE_URL,
    RECALL_BOT_NAME,
    WEBHOOK_PORT,
    REPO_ROOT,
)

logger = logging.getLogger(__name__)

# Regex to detect Teams meeting URLs in messages
# Supports both classic /l/meetup-join/ URLs and newer /meet/ URLs
TEAMS_URL_PATTERN = re.compile(
    r'https?://(?:teams\.microsoft\.com/(?:l/meetup-join|meet)/|teams\.live\.com/meet/)[^\s<>"\']+',
    re.IGNORECASE,
)

# SQLite schema for tracking active/completed bots
RECALL_BOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT UNIQUE NOT NULL,
    meeting_url TEXT NOT NULL,
    bot_name TEXT DEFAULT 'Aaron Gonzalez',
    status TEXT DEFAULT 'creating',
    recording_id TEXT,
    transcript_id TEXT,
    transcript_text TEXT,
    transcript_file TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    completed_at TEXT,
    error TEXT,
    chat_id INTEGER
);
"""


class RecallService:
    """Manages Recall.ai meeting bot lifecycle — create, track, fetch transcripts."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db
        self._api_key = RECALL_API_KEY
        self._base_url = RECALL_API_BASE_URL.rstrip("/")
        self._bot_name = RECALL_BOT_NAME
        self._webhook_base_url: Optional[str] = None  # Set externally if available

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def initialize(self) -> None:
        await self._db.executescript(RECALL_BOT_SCHEMA)
        await self._db.commit()
        logger.info("Recall.ai bot tracking table initialized")

    def set_webhook_base_url(self, url: str) -> None:
        """Set the externally reachable base URL for webhooks (e.g., https://your-server.com)."""
        self._webhook_base_url = url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def extract_teams_url(text: str) -> Optional[str]:
        """Extract a Teams meeting URL from a text message. Returns None if not found."""
        match = TEAMS_URL_PATTERN.search(text)
        return match.group(0) if match else None

    async def send_bot_to_meeting(
        self,
        meeting_url: str,
        chat_id: int,
        join_at: Optional[str] = None,
    ) -> dict:
        """Create a Recall.ai bot and send it to join a meeting.

        Args:
            meeting_url: The Teams meeting URL
            chat_id: Telegram chat ID to send results back to
            join_at: Optional ISO timestamp for scheduled join (default: immediate)

        Returns:
            Dict with bot_id, status, and any error info
        """
        if not self.is_configured:
            return {"error": "Recall.ai API key not configured"}

        payload = {
            "meeting_url": meeting_url,
            "bot_name": self._bot_name,
            "recording_config": {
                "transcript": {
                    "provider": {
                        "recallai_streaming": {
                            "language_code": "en_us",
                            "mode": "prioritize_accuracy",
                        }
                    }
                }
            },
        }

        if join_at:
            payload["join_at"] = join_at

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/api/v1/bot/",
                    headers=self._headers(),
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()

                    if resp.status in (200, 201):
                        bot_id = data.get("id")
                        logger.info(
                            f"Recall bot created: {bot_id} for meeting {meeting_url[:80]}"
                        )

                        # Save to DB
                        await self._db.execute(
                            """INSERT INTO recall_bots
                               (bot_id, meeting_url, bot_name, status, chat_id)
                               VALUES (?, ?, ?, 'joining', ?)""",
                            (bot_id, meeting_url, self._bot_name, chat_id),
                        )
                        await self._db.commit()

                        # Start background polling for transcript
                        asyncio.create_task(
                            self._poll_bot_status(bot_id, chat_id)
                        )

                        return {
                            "bot_id": bot_id,
                            "status": "joining",
                            "meeting_url": meeting_url,
                        }
                    else:
                        error_msg = data.get("detail", str(data))
                        logger.error(
                            f"Recall API error ({resp.status}): {error_msg}"
                        )
                        return {"error": f"Recall API error ({resp.status}): {error_msg}"}

        except aiohttp.ClientError as e:
            logger.exception("Failed to call Recall.ai API")
            return {"error": f"Network error: {str(e)}"}
        except Exception as e:
            logger.exception("Unexpected error creating Recall bot")
            return {"error": f"Unexpected error: {str(e)}"}

    async def get_bot_status(self, bot_id: str) -> dict:
        """Get the current status of a Recall.ai bot."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base_url}/api/v1/bot/{bot_id}/",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    return await resp.json()
        except Exception as e:
            logger.exception(f"Failed to get bot status for {bot_id}")
            return {"error": str(e)}

    async def get_transcript(self, bot_id: str) -> Optional[str]:
        """Fetch the full transcript for a completed bot session.

        Returns the transcript as formatted text, or None if not available.
        """
        try:
            async with aiohttp.ClientSession() as session:
                # First get bot details to find recording ID
                async with session.get(
                    f"{self._base_url}/api/v1/bot/{bot_id}/",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    bot_data = await resp.json()

                # Get recordings from the bot
                recordings = bot_data.get("recordings", [])
                if not recordings:
                    logger.warning(f"No recordings found for bot {bot_id}")
                    return None

                recording_id = recordings[0] if isinstance(recordings[0], str) else recordings[0].get("id")

                # Get recording details with media shortcuts
                async with session.get(
                    f"{self._base_url}/api/v1/recording/{recording_id}/",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    recording_data = await resp.json()

                # Get the transcript download URL
                media_shortcuts = recording_data.get("media_shortcuts", {})
                transcript_info = media_shortcuts.get("transcript", {})
                transcript_data = transcript_info.get("data", {})
                download_url = transcript_data.get("download_url")

                if not download_url:
                    logger.warning(f"No transcript download URL for bot {bot_id}")
                    return None

                # Download the actual transcript
                async with session.get(
                    download_url,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    transcript_json = await resp.json()

                # Convert Recall.ai transcript JSON to readable text
                return self._format_transcript(transcript_json)

        except Exception as e:
            logger.exception(f"Failed to get transcript for bot {bot_id}")
            return None

    def _format_transcript(self, transcript_json: list) -> str:
        """Convert Recall.ai transcript JSON to human-readable text format.

        Input format: list of segments, each with 'participant' and 'words' arrays.
        Output: Speaker-attributed transcript similar to .vtt format.
        """
        if not transcript_json:
            return ""

        lines = []
        current_speaker = None

        for segment in transcript_json:
            participant = segment.get("participant", {})
            speaker_name = participant.get("name", "Unknown Speaker")
            words = segment.get("words", [])

            if not words:
                continue

            # Combine all words in this segment
            text = " ".join(w.get("text", "") for w in words).strip()
            if not text:
                continue

            # Get timestamp
            start_ts = words[0].get("start_timestamp", {})
            relative_secs = start_ts.get("relative", 0)
            timestamp = self._seconds_to_timestamp(relative_secs)

            if speaker_name != current_speaker:
                current_speaker = speaker_name
                lines.append(f"\n[{timestamp}] {speaker_name}:")

            lines.append(f"  {text}")

        return "\n".join(lines).strip()

    @staticmethod
    def _seconds_to_timestamp(seconds: float) -> str:
        """Convert seconds to HH:MM:SS format."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    async def _poll_bot_status(self, bot_id: str, chat_id: int) -> None:
        """Background task: poll Recall.ai for bot status until done or failed.

        Checks every 30 seconds. When the bot finishes, fetches the transcript
        and saves it for processing.
        """
        logger.info(f"Starting status poll for bot {bot_id}")
        poll_interval = 30  # seconds
        max_polls = 240  # 2 hours max (240 * 30s)
        polls = 0
        bot_ref = None  # Will hold reference to Telegram bot for notifications

        # Try to get bot reference from stored data
        try:
            from bot.main import _background_tasks
        except ImportError:
            pass

        terminal_statuses = {"done", "fatal", "analysis_done"}
        in_call_notified = False

        while polls < max_polls:
            await asyncio.sleep(poll_interval)
            polls += 1

            try:
                status_data = await self.get_bot_status(bot_id)
                status_code = (
                    status_data.get("status_changes", [{}])[-1].get("code", "unknown")
                    if status_data.get("status_changes")
                    else "unknown"
                )

                logger.debug(f"Bot {bot_id} poll #{polls}: status={status_code}")

                # Update DB
                await self._db.execute(
                    """UPDATE recall_bots
                       SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                       WHERE bot_id = ?""",
                    (status_code, bot_id),
                )
                await self._db.commit()

                if status_code == "fatal":
                    error = status_data.get("status_changes", [{}])[-1].get("sub_code", "unknown error")
                    logger.error(f"Bot {bot_id} failed: {error}")
                    await self._db.execute(
                        """UPDATE recall_bots
                           SET error = ?, completed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                           WHERE bot_id = ?""",
                        (str(error), bot_id),
                    )
                    await self._db.commit()

                    # Queue a notification about the failure
                    await self._queue_notification(
                        chat_id,
                        f"⚠️ Meeting bot failed: {error}",
                    )
                    return

                if status_code == "done":
                    logger.info(f"Bot {bot_id} done — fetching transcript")
                    await self._db.execute(
                        """UPDATE recall_bots
                           SET completed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                           WHERE bot_id = ?""",
                        (bot_id,),
                    )
                    await self._db.commit()

                    # Fetch and save transcript
                    transcript_text = await self.get_transcript(bot_id)
                    if transcript_text:
                        # Save transcript to file
                        date_str = datetime.now(CT).strftime("%Y-%m-%d")
                        transcript_dir = REPO_ROOT / "transcripts" / "recall"
                        transcript_dir.mkdir(parents=True, exist_ok=True)
                        transcript_file = transcript_dir / f"{date_str}-{bot_id[:8]}.txt"
                        transcript_file.write_text(transcript_text)

                        # Update DB
                        await self._db.execute(
                            """UPDATE recall_bots
                               SET transcript_text = ?, transcript_file = ?
                               WHERE bot_id = ?""",
                            (transcript_text[:5000], str(transcript_file), bot_id),
                        )
                        await self._db.commit()

                        logger.info(
                            f"Transcript saved: {transcript_file} "
                            f"({len(transcript_text)} chars)"
                        )

                        # Queue for transcript processing
                        await self._queue_transcript_for_processing(
                            chat_id, transcript_file, transcript_text
                        )

                        # Mark in persistent tracker so the cron poller
                        # doesn't reprocess this bot on restart
                        try:
                            from bot.services.recall_transcript_poller import RecallTranscriptPoller
                            tracker = RecallTranscriptPoller._load_tracker()
                            tracker["processed"][bot_id] = {
                                "processed_at": datetime.now(CT).isoformat(),
                                "transcript_file": str(transcript_file),
                            }
                            from bot.services.recall_transcript_poller import TRACKER_FILE
                            TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
                            TRACKER_FILE.write_text(json.dumps(tracker, indent=2) + "\n")
                            logger.info(f"Marked bot {bot_id[:8]} in persistent dedup tracker")
                        except Exception as e:
                            logger.warning(f"Failed to update dedup tracker for {bot_id[:8]}: {e}")
                    else:
                        await self._queue_notification(
                            chat_id,
                            "Meeting ended but no transcript was available. "
                            "The meeting may have been too short or recording failed.",
                        )
                    return

            except Exception as e:
                logger.exception(f"Error polling bot {bot_id}")
                # Don't stop polling on transient errors
                continue

        # Timed out
        logger.warning(f"Bot {bot_id} polling timed out after {max_polls * poll_interval}s")
        await self._queue_notification(
            chat_id,
            f"Meeting bot {bot_id[:8]} timed out after 2 hours. "
            "It may still be running — check with /recall_status.",
        )

    async def _queue_notification(self, chat_id: int, text: str) -> None:
        """Queue a notification message to send to the user via Telegram.

        Uses the message_queue table with source='recall_notification'.
        """
        try:
            await self._db.execute(
                """INSERT INTO message_queue
                   (source, direction, status, body, telegram_chat_id)
                   VALUES ('recall_notification', 'outbound', 'new', ?, ?)""",
                (text, chat_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception("Failed to queue Recall notification")

    async def _queue_transcript_for_processing(
        self, chat_id: int, transcript_file: Path, transcript_text: str
    ) -> None:
        """Queue the transcript for processing by the orchestrator.

        Creates a message_queue entry that the queue processor will pick up
        and route through the transcript_processor subagent.
        """
        try:
            body = (
                f"[RECALL.AI TRANSCRIPT READY — auto-processing]\n\n"
                f"Transcript file: {transcript_file}\n"
                f"Length: {len(transcript_text)} characters\n\n"
                f"Process this meeting transcript. Route to transcript_processor subagent.\n"
                f"Extract: meeting summary, speakers, project identification, constraints, "
                f"action items (WHO/WHAT/WHEN), key decisions, and follow-ups.\n"
                f"Save action items and decisions to memory."
            )
            await self._db.execute(
                """INSERT INTO message_queue
                   (source, direction, status, body, telegram_chat_id)
                   VALUES ('recall_transcript', 'inbound', 'new', ?, ?)""",
                (body, chat_id),
            )
            await self._db.commit()
            logger.info(f"Transcript queued for processing (chat_id={chat_id})")
        except Exception:
            logger.exception("Failed to queue transcript for processing")

    async def get_active_bots(self) -> list[dict]:
        """Get all active (non-completed) bots."""
        cursor = await self._db.execute(
            """SELECT bot_id, meeting_url, status, created_at, chat_id
               FROM recall_bots
               WHERE completed_at IS NULL AND error IS NULL
               ORDER BY created_at DESC""",
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_recent_bots(self, limit: int = 10) -> list[dict]:
        """Get recent bot sessions."""
        cursor = await self._db.execute(
            """SELECT bot_id, meeting_url, status, created_at, completed_at, error, transcript_file
               FROM recall_bots
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
