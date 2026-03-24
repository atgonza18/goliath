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
from yarl import URL as _URL

from bot.config import (
    RECALL_API_KEY,
    RECALL_API_BASE_URL,
    RECALL_BOT_NAME,
    RECALL_WEBHOOK_URL,
    WEBHOOK_PORT,
    REPO_ROOT,
)

logger = logging.getLogger(__name__)

# Regex to detect meeting URLs in messages.
# Supports Teams, Zoom, and Google Meet links.
#
# Teams variants:
#   https://teams.microsoft.com/l/meetup-join/...  (classic invite link)
#   https://teams.microsoft.com/meet/...            (new short link)
#   https://teams.live.com/meet/...                 (personal Teams)
#
# Zoom variants:
#   https://zoom.us/j/12345                         (standard meeting)
#   https://us02web.zoom.us/j/12345                 (regional subdomain)
#   https://company.zoom.us/j/12345                 (vanity subdomain)
#
# Google Meet:
#   https://meet.google.com/abc-defg-hij
MEETING_URL_PATTERN = re.compile(
    r'https?://(?:'
    r'teams\.microsoft\.com/(?:l/meetup-join|meet)/'
    r'|teams\.live\.com/meet/'
    r'|(?:[\w-]+\.)?zoom\.us/j/'
    r'|meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}'
    r')[^\s<>"\']*',
    re.IGNORECASE,
)

# Backward-compatible alias (used by email handler and handlers/meeting.py)
TEAMS_URL_PATTERN = MEETING_URL_PATTERN

# SQLite schema for tracking active/completed bots
RECALL_BOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS recall_bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT UNIQUE NOT NULL,
    meeting_url TEXT NOT NULL,
    bot_name TEXT DEFAULT 'Aaron Gonzalez - note taker',
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
        # Webhook URL that Recall.ai POSTs events to when bot status changes
        self._webhook_url: Optional[str] = RECALL_WEBHOOK_URL or None

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def initialize(self) -> None:
        await self._db.executescript(RECALL_BOT_SCHEMA)
        await self._db.commit()
        logger.info("Recall.ai bot tracking table initialized")

    def set_webhook_url(self, url: str) -> None:
        """Set the full webhook URL for Recall.ai callbacks (e.g., https://your-server.com/webhook/recall)."""
        self._webhook_url = url.rstrip("/")

    # Backward-compatible alias
    def set_webhook_base_url(self, url: str) -> None:
        """Deprecated — use set_webhook_url(). Sets base URL and appends /webhook/recall."""
        self._webhook_url = url.rstrip("/") + "/webhook/recall"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def extract_meeting_url(text: str) -> Optional[str]:
        """Extract a meeting URL (Teams, Zoom, Google Meet) from text. Returns None if not found."""
        match = MEETING_URL_PATTERN.search(text)
        return match.group(0) if match else None

    @staticmethod
    def extract_teams_url(text: str) -> Optional[str]:
        """Backward-compatible alias for extract_meeting_url().

        Now detects Teams, Zoom, AND Google Meet URLs.
        """
        return RecallService.extract_meeting_url(text)

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

        # NOTE: webhook_url intentionally NOT sent.  We rely on outbound
        # polling (RecallTranscriptPoller every 2 min) instead of inbound
        # webhooks.  This avoids needing a public HTTPS endpoint / Cloudflare
        # tunnel for Recall.ai to POST to.

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

    async def list_api_bots(self, status: str = None, limit: int = 50) -> list[dict]:
        """List recent bots from the Recall.ai API (outbound polling).

        Calls GET /api/v1/bot/ to discover bots — including ones that may
        have been created outside our normal flow (e.g., via API console).

        Args:
            status: Optional latest-status filter (e.g., 'done', 'fatal')
            limit: Max results per page (default 50)

        Returns:
            List of bot dicts from the API, or empty list on error.
        """
        if not self.is_configured:
            return []

        params = {"ordering": "-created_at", "limit": str(limit)}
        if status:
            params["status_changes__latest_code"] = status

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base_url}/api/v1/bot/",
                    headers=self._headers(),
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"Recall API list bots returned HTTP {resp.status}"
                        )
                        return []
                    data = await resp.json()
                    return data.get("results", [])
        except Exception as e:
            logger.warning(f"Failed to list bots from Recall.ai API: {e}")
            return []

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

    async def get_transcript(
        self, bot_id: str, *, _retries: int = 4, _base_delay: float = 15.0
    ) -> Optional[str]:
        """Fetch the full transcript for a completed bot session.

        Returns the transcript as formatted text, or None if not available.

        Recall.ai sometimes marks the bot "done" before the transcript S3
        object is fully available.  The pre-signed download URL may return
        403 (AccessDenied) for a short window.  We retry with exponential
        back-off to ride through this delay.
        """
        for attempt in range(1, _retries + 1):
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

                    # Get recording details with media shortcuts — re-fetch
                    # each attempt so we get a fresh pre-signed URL.
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

                    # Download the actual transcript.
                    # CRITICAL: Use yarl URL(encoded=True) to prevent aiohttp
                    # from double-encoding the pre-signed S3 query parameters.
                    # Without this, the AWS signature check fails (403).
                    async with session.get(
                        _URL(download_url, encoded=True),
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        if resp.status == 403 or resp.status == 404:
                            # S3 object not ready yet — retry after delay
                            if attempt < _retries:
                                delay = _base_delay * (2 ** (attempt - 1))
                                logger.warning(
                                    f"Transcript download for bot {bot_id[:8]} returned "
                                    f"HTTP {resp.status} (attempt {attempt}/{_retries}). "
                                    f"Transcript may not be ready yet — retrying in {delay:.0f}s..."
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                logger.error(
                                    f"Transcript download for bot {bot_id[:8]} still "
                                    f"returning HTTP {resp.status} after {_retries} attempts"
                                )
                                return None

                        if resp.status != 200:
                            logger.error(
                                f"Transcript download for bot {bot_id[:8]} "
                                f"returned unexpected HTTP {resp.status}"
                            )
                            return None

                        # Parse JSON — use content_type=None to tolerate
                        # non-standard Content-Type headers from S3.
                        transcript_json = await resp.json(content_type=None)

                    # Convert Recall.ai transcript JSON to readable text
                    return self._format_transcript(transcript_json)

            except Exception as e:
                if attempt < _retries:
                    delay = _base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"Transcript fetch for bot {bot_id[:8]} failed "
                        f"(attempt {attempt}/{_retries}): {e}. Retrying in {delay:.0f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.exception(f"Failed to get transcript for bot {bot_id}")
                    return None
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

                    # ── Idempotency gate: check if the persistent poller
                    # already handled this bot while we were sleeping ──
                    try:
                        from bot.services.recall_transcript_poller import RecallTranscriptPoller
                        if bot_id in RecallTranscriptPoller._load_tracker().get("processed", {}):
                            logger.info(
                                f"Bot {bot_id[:8]} already in dedup tracker "
                                f"(persistent poller handled it) — skipping"
                            )
                            return
                    except Exception as e:
                        logger.warning(f"Dedup tracker check failed for {bot_id[:8]}: {e}")

                    await self._db.execute(
                        """UPDATE recall_bots
                           SET completed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                           WHERE bot_id = ?""",
                        (bot_id,),
                    )
                    await self._db.commit()

                    # Fetch meeting metadata for debrief enrichment
                    # (participants, duration, URL — gives transcript_processor
                    # context for "who was on, how long")
                    metadata = await self.get_meeting_metadata(bot_id)

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

                        # Mark in persistent tracker BEFORE queuing so
                        # the persistent poller can't also queue it
                        # during the race window.
                        try:
                            from bot.services.recall_transcript_poller import RecallTranscriptPoller, TRACKER_FILE
                            tracker = RecallTranscriptPoller._load_tracker()
                            tracker["processed"][bot_id] = {
                                "processed_at": datetime.now(CT).isoformat(),
                                "transcript_file": str(transcript_file),
                            }
                            TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
                            tmp = TRACKER_FILE.with_suffix(".tmp")
                            tmp.write_text(json.dumps(tracker, indent=2) + "\n")
                            tmp.replace(TRACKER_FILE)
                            logger.info(f"Marked bot {bot_id[:8]} in persistent dedup tracker")
                        except Exception as e:
                            logger.warning(f"Failed to update dedup tracker for {bot_id[:8]}: {e}")

                        # Queue for transcript processing (after tracker
                        # is marked, so the persistent poller will skip it)
                        await self._queue_transcript_for_processing(
                            chat_id, transcript_file, transcript_text,
                            metadata=metadata,
                        )
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

    async def get_meeting_metadata(self, bot_id: str) -> dict:
        """Fetch meeting metadata from Recall.ai API for debrief enrichment.

        Extracts participants, duration, and meeting URL from the bot status
        response. This data is injected into the transcript processing queue
        so the transcript_processor can produce a richer debrief (who was on,
        how long the meeting lasted, which project the meeting URL maps to).

        Returns dict with keys: participants, duration_minutes, meeting_url,
        bot_id. Returns empty dict on error (non-critical — debrief still
        works without metadata, it just won't have call-level context).
        """
        try:
            status_data = await self.get_bot_status(bot_id)
            if "error" in status_data:
                return {}

            # Extract participant names from meeting_participants array
            participants = []
            for p in status_data.get("meeting_participants", []):
                name = p.get("name", "")
                if name:
                    participants.append(name)

            # Compute duration from status_changes timestamps
            duration_minutes = 0
            status_changes = status_data.get("status_changes", [])
            if len(status_changes) >= 2:
                try:
                    first_ts = status_changes[0].get("created_at", "")
                    last_ts = status_changes[-1].get("created_at", "")
                    if first_ts and last_ts:
                        t1 = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
                        t2 = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                        duration_minutes = max(0, int((t2 - t1).total_seconds() / 60))
                except Exception:
                    pass

            meeting_url = status_data.get("meeting_url", "")

            return {
                "participants": participants,
                "duration_minutes": duration_minutes,
                "meeting_url": meeting_url,
                "bot_id": bot_id,
            }
        except Exception:
            logger.debug(f"Failed to get meeting metadata for {bot_id[:8]}", exc_info=True)
            return {}

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
        self, chat_id: int, transcript_file: Path, transcript_text: str,
        metadata: dict = None,
    ) -> None:
        """Queue the transcript for processing by the orchestrator.

        Creates a message_queue entry that the queue processor will pick up
        and route through the transcript_processor subagent.

        Args:
            chat_id: Telegram chat ID for result delivery
            transcript_file: Path to saved transcript file
            transcript_text: Raw transcript content
            metadata: Optional meeting metadata from get_meeting_metadata()
                      (participants, duration_minutes, meeting_url, bot_id)
        """
        try:
            # Build enriched context from meeting metadata (if available).
            # This gives transcript_processor richer context for the debrief:
            # "who was on, what project, how long".
            meta_lines = []
            if metadata:
                bot_id = metadata.get("bot_id", "")
                if bot_id:
                    meta_lines.append(f"Recall Bot ID: {bot_id[:8]}")
                participants = metadata.get("participants", [])
                if participants:
                    meta_lines.append(
                        f"Meeting participants ({len(participants)}): "
                        f"{', '.join(participants)}"
                    )
                duration = metadata.get("duration_minutes", 0)
                if duration:
                    meta_lines.append(f"Meeting duration: ~{duration} minutes")
                meeting_url = metadata.get("meeting_url", "")
                if meeting_url:
                    meta_lines.append(f"Meeting URL: {meeting_url[:120]}")

            metadata_section = ""
            if meta_lines:
                metadata_section = "\n".join(meta_lines) + "\n\n"

            body = (
                f"[RECALL.AI TRANSCRIPT READY — auto-processing]\n\n"
                f"Transcript file: {transcript_file}\n"
                f"Length: {len(transcript_text)} characters\n"
                f"{metadata_section}"
                f"\nProcess this meeting transcript. Route to transcript_processor subagent.\n"
                f"Extract: meeting summary, speakers, project identification, constraints, "
                f"action items (WHO/WHAT/WHEN), key decisions, and follow-ups.\n"
                f"Save action items and decisions to memory."
            )

            # Store bot_id as external_message_id so error handlers can
            # reference which bot's transcript failed (e.g., "Post-call
            # processing failed for bot abc12345").
            ext_id = (metadata or {}).get("bot_id", "")

            await self._db.execute(
                """INSERT INTO message_queue
                   (source, direction, status, body, telegram_chat_id,
                    external_message_id)
                   VALUES ('recall_transcript', 'inbound', 'new', ?, ?, ?)""",
                (body, chat_id, ext_id),
            )
            await self._db.commit()
            logger.info(
                f"Transcript queued for processing (chat_id={chat_id}, "
                f"bot_id={ext_id[:8] if ext_id else 'N/A'})"
            )
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
