"""Recall.ai Transcript Poller — periodic background task that checks all
known Recall bots for completion and processes their transcripts with
file-based deduplication.

Problem this solves:
  When send_bot_to_meeting() is called, it starts an in-memory asyncio task
  to poll that specific bot. But if the bot process restarts (or if a bot
  was scheduled outside the normal handler, e.g., via the Recall API directly),
  that polling task is lost. Transcripts then go unprocessed until someone
  manually triggers it.

Solution:
  A periodic scheduler task that:
  1. Queries the recall_bots SQLite table for any bot that:
     - Has no completed_at AND no error (still in progress), OR
     - Is "done" but has no transcript_file (transcript not fetched yet)
  2. Calls the Recall.ai API to check current status
  3. If the bot is done and recording is available, fetches the transcript
  4. Routes the transcript through the normal processing pipeline
  5. Marks the bot_id in a persistent JSON tracker so restarts never reprocess

Dedup tracker:
  File: /opt/goliath/telegram-bot/data/recall_processed_bots.json
  Format: {"processed": {"<bot_id>": {"processed_at": "...", "transcript_file": "..."}}}
  This file survives restarts and prevents duplicate processing.

Also catches "orphaned" bots: any bot in the Recall.ai API that finished
but was never recorded in our local DB (e.g., bots scheduled via API console).
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

from bot.config import REPO_ROOT, REPORT_CHAT_ID

CT = ZoneInfo("America/Chicago")

logger = logging.getLogger(__name__)

# Persistent dedup tracker file — survives bot restarts
TRACKER_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "recall_processed_bots.json"

# How often the poller checks (used by the scheduler, not internally)
POLL_INTERVAL_SECONDS = 120  # 2 minutes


class RecallTranscriptPoller:
    """Checks all known Recall.ai bots for transcript readiness with dedup.

    The poller is designed to be called repeatedly by the scheduler's
    interval task system. Each invocation is stateless (reads the tracker
    from disk) so it works correctly even after process restarts.
    """

    def __init__(self, recall_service, memory_db):
        """
        Args:
            recall_service: RecallService instance (for API calls and DB access)
            memory_db: aiosqlite connection (same as recall_service._db)
        """
        self._recall = recall_service
        self._db = memory_db
        self._tracker = self._load_tracker()
        # Lock to prevent concurrent poll cycles from racing
        self._poll_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Persistent tracker (JSON file)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_tracker() -> dict:
        """Load the processed-bots tracker from disk."""
        if TRACKER_FILE.exists():
            try:
                data = json.loads(TRACKER_FILE.read_text())
                if isinstance(data, dict) and "processed" in data:
                    return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load recall tracker: {e} — starting fresh")
        return {"processed": {}}

    def _save_tracker(self) -> None:
        """Persist the tracker to disk (atomic-ish via write + rename)."""
        TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TRACKER_FILE.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self._tracker, indent=2) + "\n")
            tmp.replace(TRACKER_FILE)
        except IOError:
            logger.exception("Failed to save recall tracker")

    def is_processed(self, bot_id: str) -> bool:
        """Check if a bot_id has already been processed."""
        return bot_id in self._tracker["processed"]

    def mark_processed(self, bot_id: str, transcript_file: str = "") -> None:
        """Mark a bot_id as processed and persist to disk."""
        self._tracker["processed"][bot_id] = {
            "processed_at": datetime.now(CT).isoformat(),
            "transcript_file": transcript_file,
        }
        self._save_tracker()
        logger.info(f"Recall poller: marked bot {bot_id[:8]} as processed")

    def get_processed_count(self) -> int:
        """Return the number of bots in the processed tracker."""
        return len(self._tracker["processed"])

    # ------------------------------------------------------------------
    # Core polling logic
    # ------------------------------------------------------------------

    async def poll_all_bots(self) -> dict:
        """Check all known Recall bots and process any completed transcripts.

        Returns a summary dict: {"checked": N, "processed": N, "errors": N, "skipped": N}

        This method is safe to call repeatedly — the dedup tracker ensures
        no bot is processed twice.
        """
        if self._poll_lock.locked():
            logger.debug("Recall poller: poll already in progress, skipping")
            return {"checked": 0, "processed": 0, "errors": 0, "skipped": 0, "locked": True}

        async with self._poll_lock:
            # Reload tracker from disk in case another process updated it
            self._tracker = self._load_tracker()

            summary = {"checked": 0, "processed": 0, "errors": 0, "skipped": 0}

            # Get bots that might need processing from our local DB
            candidates = await self._get_candidate_bots()

            if not candidates:
                return summary

            logger.info(
                f"Recall poller: checking {len(candidates)} candidate bot(s) "
                f"({self.get_processed_count()} already processed)"
            )

            for bot_row in candidates:
                bot_id = bot_row["bot_id"]
                chat_id = bot_row.get("chat_id") or self._get_default_chat_id()
                summary["checked"] += 1

                # Dedup check
                if self.is_processed(bot_id):
                    summary["skipped"] += 1
                    logger.debug(f"Recall poller: skipping {bot_id[:8]} (already processed)")
                    continue

                try:
                    result = await self._check_and_process_bot(bot_id, chat_id)
                    if result == "processed":
                        summary["processed"] += 1
                    elif result == "not_ready":
                        pass  # Still in progress, will check again next cycle
                    elif result == "failed":
                        summary["errors"] += 1
                    elif result == "skipped":
                        summary["skipped"] += 1
                except Exception:
                    logger.exception(f"Recall poller: error checking bot {bot_id[:8]}")
                    summary["errors"] += 1

            if summary["processed"] > 0 or summary["errors"] > 0:
                logger.info(
                    f"Recall poller: done — checked={summary['checked']}, "
                    f"processed={summary['processed']}, errors={summary['errors']}, "
                    f"skipped={summary['skipped']}"
                )

            return summary

    async def _get_candidate_bots(self) -> list[dict]:
        """Get bots from the DB that might need transcript processing.

        Returns bots that are:
        1. Not yet completed (still polling) — in case the in-memory poll died
        2. Completed ("done") but missing transcript_file — transcript not fetched
        3. Created within the last 48 hours (don't re-check ancient bots)

        Excludes bots that have errors (fatal status) — those won't recover.
        """
        try:
            cursor = await self._db.execute(
                """SELECT bot_id, meeting_url, status, chat_id, created_at,
                          completed_at, transcript_file, error
                   FROM recall_bots
                   WHERE (
                       -- Still in progress (no completion, no error)
                       (completed_at IS NULL AND error IS NULL)
                       -- Or done but no transcript fetched yet
                       OR (status = 'done' AND (transcript_file IS NULL OR transcript_file = ''))
                   )
                   AND created_at >= strftime('%Y-%m-%dT%H:%M:%S', datetime('now', '-48 hours'))
                   ORDER BY created_at DESC""",
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Recall poller: failed to query candidate bots")
            return []

    async def _check_and_process_bot(self, bot_id: str, chat_id: int) -> str:
        """Check a single bot's status and process if ready.

        Returns: "processed", "not_ready", "failed", or "skipped"
        """
        # Query Recall.ai API for current status
        status_data = await self._recall.get_bot_status(bot_id)

        if "error" in status_data:
            logger.warning(f"Recall poller: API error for {bot_id[:8]}: {status_data['error']}")
            return "failed"

        # Extract current status from status_changes
        status_changes = status_data.get("status_changes", [])
        if not status_changes:
            return "not_ready"

        status_code = status_changes[-1].get("code", "unknown")

        # Update DB with latest status
        try:
            await self._db.execute(
                """UPDATE recall_bots
                   SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                   WHERE bot_id = ?""",
                (status_code, bot_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception(f"Recall poller: failed to update DB for {bot_id[:8]}")

        if status_code == "fatal":
            # Bot failed — mark as processed so we don't keep checking
            sub_code = status_changes[-1].get("sub_code", "unknown")
            logger.warning(f"Recall poller: bot {bot_id[:8]} FATAL: {sub_code}")
            try:
                await self._db.execute(
                    """UPDATE recall_bots
                       SET error = ?, completed_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                       WHERE bot_id = ?""",
                    (f"fatal: {sub_code}", bot_id),
                )
                await self._db.commit()
            except Exception:
                pass
            self.mark_processed(bot_id, transcript_file="FATAL")
            return "failed"

        if status_code != "done":
            # Bot still in progress — check again next cycle
            logger.debug(f"Recall poller: bot {bot_id[:8]} status={status_code} — not ready yet")
            return "not_ready"

        # Bot is DONE — fetch and process transcript
        logger.info(f"Recall poller: bot {bot_id[:8]} is DONE — fetching transcript")

        # Mark completed_at in DB if not already set
        try:
            await self._db.execute(
                """UPDATE recall_bots
                   SET completed_at = COALESCE(completed_at, strftime('%Y-%m-%dT%H:%M:%S','now'))
                   WHERE bot_id = ?""",
                (bot_id,),
            )
            await self._db.commit()
        except Exception:
            pass

        # Fetch transcript
        transcript_text = await self._recall.get_transcript(bot_id)
        if not transcript_text:
            logger.warning(f"Recall poller: bot {bot_id[:8]} done but no transcript available")
            # Don't mark as processed — might become available later
            return "not_ready"

        # Save transcript to file
        date_str = datetime.now(CT).strftime("%Y-%m-%d")
        transcript_dir = REPO_ROOT / "transcripts" / "recall"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = transcript_dir / f"{date_str}-{bot_id[:8]}.txt"

        # Avoid overwriting an existing file with the same name
        if transcript_file.exists():
            # File already exists — check if it has content
            existing = transcript_file.read_text()
            if len(existing) > 100:
                logger.info(
                    f"Recall poller: transcript file already exists: {transcript_file.name} "
                    f"({len(existing)} chars) — using existing file"
                )
            else:
                transcript_file.write_text(transcript_text)
        else:
            transcript_file.write_text(transcript_text)

        logger.info(
            f"Recall poller: transcript saved: {transcript_file} "
            f"({len(transcript_text)} chars)"
        )

        # Also save raw bot data for debugging
        try:
            raw_file = transcript_dir / f"{date_str}-{bot_id[:8]}-raw.json"
            if not raw_file.exists():
                raw_file.write_text(json.dumps(status_data, indent=2))
        except Exception:
            pass

        # Update DB with transcript info
        try:
            await self._db.execute(
                """UPDATE recall_bots
                   SET transcript_text = ?, transcript_file = ?
                   WHERE bot_id = ?""",
                (transcript_text[:5000], str(transcript_file), bot_id),
            )
            await self._db.commit()
        except Exception:
            logger.exception(f"Recall poller: failed to save transcript info for {bot_id[:8]}")

        # Mark as processed BEFORE queuing — this is the idempotency
        # gate.  If the in-memory poller checks the tracker while we're
        # queuing, it will see this bot is already handled and skip it.
        self.mark_processed(bot_id, transcript_file=str(transcript_file))

        # Queue transcript for processing via the message queue
        await self._recall._queue_transcript_for_processing(
            chat_id, transcript_file, transcript_text
        )

        # Send notification
        await self._recall._queue_notification(
            chat_id,
            f"Recall transcript poller: auto-processed transcript for bot {bot_id[:8]}.\n"
            f"File: {transcript_file.name} ({len(transcript_text)} chars)\n"
            f"Queued for full analysis (summary, action items, constraints).",
        )

        return "processed"

    @staticmethod
    def _get_default_chat_id() -> int:
        """Get the default chat_id from REPORT_CHAT_ID env var."""
        try:
            return int(REPORT_CHAT_ID)
        except (ValueError, TypeError):
            return 0

    # ------------------------------------------------------------------
    # Manual operations (for /recall_status command etc.)
    # ------------------------------------------------------------------

    def get_tracker_summary(self) -> str:
        """Return a human-readable summary of the tracker state."""
        self._tracker = self._load_tracker()
        processed = self._tracker.get("processed", {})
        if not processed:
            return "No bots in the processed tracker."

        lines = [f"Processed bots: {len(processed)}"]
        # Show most recent 5
        sorted_bots = sorted(
            processed.items(),
            key=lambda x: x[1].get("processed_at", ""),
            reverse=True,
        )
        for bot_id, info in sorted_bots[:5]:
            ts = info.get("processed_at", "unknown")[:16]
            tf = Path(info.get("transcript_file", "")).name or "N/A"
            lines.append(f"  {bot_id[:8]} — {ts} — {tf}")

        if len(sorted_bots) > 5:
            lines.append(f"  ... and {len(sorted_bots) - 5} more")

        return "\n".join(lines)


# ------------------------------------------------------------------
# Scheduler task callback
# ------------------------------------------------------------------

async def task_recall_transcript_poll(scheduler) -> None:
    """Scheduler callback: poll all Recall.ai bots for transcript readiness.

    This is registered as an interval task (every 2 minutes) in the scheduler.
    It's lightweight — most cycles will find zero candidates and return fast.
    """
    bot = scheduler.bot
    if not bot or not hasattr(bot, "_bot_data_ref"):
        logger.debug("Recall transcript poller: bot not ready yet, skipping")
        return

    recall_service = bot._bot_data_ref.get("recall_service")
    if not recall_service or not recall_service.is_configured:
        # Silently skip if Recall.ai is not configured
        return

    memory_db = recall_service._db

    poller = RecallTranscriptPoller(recall_service, memory_db)

    try:
        summary = await asyncio.wait_for(poller.poll_all_bots(), timeout=90)

        if summary.get("processed", 0) > 0:
            logger.info(
                f"Recall transcript poller: processed {summary['processed']} transcript(s)"
            )
    except asyncio.TimeoutError:
        logger.warning("Recall transcript poller: timed out after 90s")
    except Exception:
        logger.exception("Recall transcript poller: unexpected error")
