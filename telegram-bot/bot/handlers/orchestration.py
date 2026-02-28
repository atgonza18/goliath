import asyncio
import logging
import random
import subprocess
import time
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from bot.agents.orchestrator import NimrodOrchestrator
from bot.config import REPO_ROOT
from bot.services.voice import text_to_voice
from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-chat concurrency control
# ---------------------------------------------------------------------------
# Prevents two orchestrations from running simultaneously in the same chat,
# which causes conflicting status messages (e.g. "10/10 complete" vs "Phase 1 done").
#
# Design:
#   - _chat_locks:  one asyncio.Lock per chat_id, lazily created
#   - _chat_queues: pending (update, context, user_message) tuples per chat_id
#
# When a message arrives and the lock is already held, we acknowledge and queue it.
# When an orchestration finishes, we drain the queue: only the LATEST message is
# processed (intermediate ones are dropped — the most recent message is what the
# user cares about).
# ---------------------------------------------------------------------------
_chat_locks: dict[int, asyncio.Lock] = {}
_chat_queues: dict[int, list] = {}


def _get_chat_lock(chat_id: int) -> asyncio.Lock:
    """Return the asyncio.Lock for a given chat, creating it lazily."""
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    return _chat_locks[chat_id]


async def _enqueue_or_run(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_message: str,
):
    """Gate _run_orchestrator behind a per-chat lock.

    If the chat is idle → acquire the lock and run immediately.
    If the chat is busy → acknowledge the user, queue the message, return.
    When a run finishes → pop the LATEST queued message and run it (discard older ones).
    """
    chat_id = update.effective_chat.id
    lock = _get_chat_lock(chat_id)

    # Fast path: nobody running → grab the lock and go
    if not lock.locked():
        await _locked_run(lock, chat_id, update, context, user_message)
        return

    # Slow path: orchestration already in progress → queue
    if chat_id not in _chat_queues:
        _chat_queues[chat_id] = []
    _chat_queues[chat_id].append((update, context, user_message))
    logger.info(
        f"Chat {chat_id}: orchestration busy — queued message "
        f"(queue depth: {len(_chat_queues[chat_id])})"
    )
    try:
        await update.message.reply_text(
            "Still working on your last request \u2014 I'll get to this next. \U0001f504"
        )
    except Exception:
        logger.debug("Failed to send queue acknowledgment", exc_info=True)


async def _locked_run(
    lock: asyncio.Lock,
    chat_id: int,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_message: str,
):
    """Acquire the lock, run the orchestrator, then drain any queued messages."""
    async with lock:
        try:
            await _run_orchestrator(update, context, user_message)
        except Exception:
            # _run_orchestrator has its own error handling, but if something
            # truly unexpected leaks out we still want to release the lock
            # (handled by `async with`) and not lose the exception.
            logger.exception(
                f"Chat {chat_id}: unhandled exception escaped _run_orchestrator"
            )

    # Lock is released — check for queued messages
    await _drain_queue(chat_id)


async def _drain_queue(chat_id: int):
    """Process the most recent queued message, discard older ones."""
    queue = _chat_queues.get(chat_id)
    if not queue:
        return

    # Grab everything and clear
    pending = queue[:]
    queue.clear()

    if not pending:
        return

    # Only process the LATEST message — discard the rest
    latest_update, latest_context, latest_message = pending[-1]
    dropped = len(pending) - 1
    if dropped > 0:
        logger.info(
            f"Chat {chat_id}: dropping {dropped} intermediate queued message(s), "
            f"processing latest"
        )
        try:
            await latest_update.message.reply_text(
                f"Caught up \u2014 skipped {dropped} older message(s) and handling your latest one now."
            )
        except Exception:
            logger.debug("Failed to send skip notification", exc_info=True)

    lock = _get_chat_lock(chat_id)
    await _locked_run(lock, chat_id, latest_update, latest_context, latest_message)


# Dynamic status messages — Nimrod's personality: blunt, funny, gets stuff done
STATUS_MESSAGES = [
    "\U0001f504 Firing up the brain cells... gimme a sec.",
    "\U0001f9e0 Processing... pulling memories and dispatching agents.",
    "\u26a1 On it \u2014 running the analysis now.",
    "\U0001f50d Digging into this one...",
    "\U0001f4ad Thinking through this... back in a moment.",
    "\U0001f680 Spinning up the agents. Hold tight.",
    "\U0001f477 Got it. Let me put the team on this.",
    "\U0001f3af Locking in... give me a minute.",
    "\u2699\ufe0f Cranking the gears. This'll just take a sec.",
    "\U0001f4a1 Interesting one. Let me dig in.",
    "\U0001f50e Pulling up the data and running the numbers...",
    "\U0001f916 Agents assembling. Stand by.",
    "\U0001f4ca On it \u2014 crunching through the portfolio now.",
    "\U0001f527 Working on it. The hamsters are running full speed.",
    "\U0001f30d Scanning the empire... one moment.",
]

# Periodic "still working" updates for long-running operations
STILL_WORKING_MESSAGES = [
    "\u23f3 Still cranking... this is a big one.",
    "\u23f3 Still working on it. Agents are busy.",
    "\u23f3 Making progress... hang tight.",
    "\u23f3 Deep in analysis mode. Almost there.",
    "\u23f3 Still at it. Complex request — worth the wait.",
    "\u23f3 Agents still running. I'll have something soon.",
]

# Directory for downloaded photos/files
UPLOADS_DIR = REPO_ROOT / "telegram-bot" / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


async def _periodic_working_updates(working_msg, interval: int = 60, initial_delay: int = 30):
    """Send periodic 'still working' updates to reassure the user during long ops.

    Starts after initial_delay seconds, then repeats every interval seconds.
    Designed to be run as an asyncio task and cancelled when the main work finishes.
    """
    try:
        await asyncio.sleep(initial_delay)
        idx = 0
        while True:
            try:
                msg = STILL_WORKING_MESSAGES[idx % len(STILL_WORKING_MESSAGES)]
                await working_msg.edit_text(msg)
                idx += 1
            except Exception:
                # Telegram API error (message deleted, etc.) — silently stop
                logger.debug("Failed to update working message, stopping periodic updates")
                return
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # Normal cancellation when orchestration completes
        return


async def claude_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form text by routing through Nimrod orchestrator."""
    user_message = update.message.text
    chat_id = update.effective_chat.id
    logger.info(f"Message from chat_id={chat_id}: {user_message[:100]}...")

    await _enqueue_or_run(update, context, user_message)


async def photo_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos sent via Telegram — download and pass to Claude for analysis."""
    chat_id = update.effective_chat.id
    caption = update.message.caption or "Analyze this image."
    logger.info(f"Photo from chat_id={chat_id}: caption='{caption[:80]}'")

    # Get the highest resolution version of the photo
    photo = update.message.photo[-1]
    photo_file = await context.bot.get_file(photo.file_id)

    # Download to uploads directory
    ext = Path(photo_file.file_path).suffix or ".jpg"
    file_path = UPLOADS_DIR / f"{photo.file_unique_id}{ext}"
    await photo_file.download_to_drive(str(file_path))
    logger.info(f"Photo saved to {file_path}")

    # Build the prompt with the image path
    user_message = (
        f"{caption}\n\n"
        f"[The user sent a photo. It has been saved to: {file_path}]\n"
        f"Use the Read tool to view the image and analyze it."
    )

    await _enqueue_or_run(update, context, user_message)


def _is_transcript_file(filename: str, caption: str = "") -> bool:
    """Detect if an uploaded file is a meeting transcript."""
    name_lower = filename.lower()
    caption_lower = (caption or "").lower()

    # Direct format match — .vtt files are almost always transcripts
    if name_lower.endswith(".vtt"):
        return True

    # Keyword match in filename
    transcript_keywords = ["transcript", "transcription", "meeting notes", "call notes", "meeting_notes"]
    for kw in transcript_keywords:
        if kw in name_lower or kw in caption_lower:
            return True

    return False


async def document_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document/file uploads — download and pass to Claude."""
    chat_id = update.effective_chat.id
    doc = update.message.document
    caption = update.message.caption or ""
    logger.info(f"Document from chat_id={chat_id}: {doc.file_name} ({doc.file_size} bytes)")

    # Download the file
    doc_file = await context.bot.get_file(doc.file_id)
    file_path = UPLOADS_DIR / doc.file_name
    await doc_file.download_to_drive(str(file_path))
    logger.info(f"Document saved to {file_path}")

    # Detect transcript files and route with transcript-specific instructions
    if _is_transcript_file(doc.file_name, caption):
        logger.info(f"Transcript detected: {doc.file_name}")
        user_message = (
            f"{caption or 'Process this meeting transcript.'}\n\n"
            f"[TRANSCRIPT UPLOAD DETECTED: {doc.file_name} — saved to: {file_path}]\n"
            f"This is a meeting/call transcript. Route to transcript_processor subagent for full analysis.\n"
            f"Extract: meeting summary, speakers, project identification, constraints discussed, "
            f"action items (WHO/WHAT/WHEN), key decisions, risks, and follow-ups.\n"
            f"Save action items and decisions to memory. File the processed summary to the project's "
            f"transcripts/ folder."
        )
    else:
        user_message = (
            f"{caption or f'Analyze this file: {doc.file_name}'}\n\n"
            f"[The user uploaded a file: {doc.file_name} — saved to: {file_path}]\n"
            f"Read and analyze this file."
        )

    await _enqueue_or_run(update, context, user_message)


async def _run_orchestrator(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str
):
    """Shared orchestration logic for text, photos, and documents."""
    memory = context.bot_data.get("memory")
    conversation = context.bot_data.get("conversation")
    activity_log = context.bot_data.get("activity_log")
    token_tracker = context.bot_data.get("token_tracker")
    preferences = context.bot_data.get("preferences")
    chat_id = update.effective_chat.id

    if not memory:
        await update.message.reply_text("Memory system not initialized. Restart the bot.")
        return

    # Save user turn before orchestration
    if conversation:
        await conversation.add_turn(chat_id, "user", user_message)

    # Retrieve conversation history for prompt injection
    conv_history = ""
    if conversation:
        conv_history = await conversation.format_for_prompt(chat_id)

    orchestrator = NimrodOrchestrator(memory=memory, token_tracker=token_tracker)

    working_msg = await update.message.reply_text(
        random.choice(STATUS_MESSAGES)
    )

    # Start periodic "still working" updates — fires after 30s, then every 60s.
    # Cancelled automatically when orchestration finishes (success or failure).
    heartbeat_task = asyncio.create_task(
        _periodic_working_updates(working_msg, interval=60, initial_delay=30)
    )

    run_start = time.monotonic()
    run_success = True
    run_error = None
    orch_result = None

    try:
        # 2 hour zombie safety net — NOT an operational timeout.
        # Claude CLI runs until done; this only catches truly hung orchestrations.
        # Previous values (900s, 1800s) killed legitimate long-running workflows.
        orch_result = await asyncio.wait_for(
            orchestrator.handle_message(user_message, conv_history),
            timeout=7200,
        )

        result_text = orch_result.text
        file_paths = orch_result.file_paths

        # Save Nimrod's response after successful orchestration
        if conversation:
            await conversation.add_turn(chat_id, "assistant", result_text)

        await working_msg.delete()

        for chunk in chunk_message(result_text, max_len=4000):
            try:
                await update.message.reply_text(chunk, parse_mode="HTML")
            except Exception:
                await update.message.reply_text(chunk)

        # Send any generated files (PDFs, DOCX, XLSX, etc.)
        for fp in file_paths:
            file_path = Path(fp)
            if file_path.is_file():
                try:
                    with open(file_path, "rb") as f:
                        await update.message.reply_document(
                            document=f,
                            filename=file_path.name,
                        )
                    logger.info(f"Sent file to Telegram: {file_path}")
                except Exception:
                    logger.exception(f"Failed to send file: {file_path}")
                    await update.message.reply_text(
                        f"Generated file but couldn't send it: <code>{file_path}</code>",
                        parse_mode="HTML",
                    )
            else:
                logger.warning(f"FILE_CREATED path does not exist: {fp}")

        # Handle restart if requested by DevOps agent
        if orch_result.restart_required:
            restart_reason = orch_result.restart_reason or "Code changes applied"
            await update.message.reply_text(
                f"<b>Restarting bot...</b>\n<i>{restart_reason}</i>\n\n"
                f"Give me ~5 seconds, then I'll be back with the changes live.",
                parse_mode="HTML",
            )
            logger.info(f"Restart requested: {restart_reason}")
            # Trigger restart AFTER response is sent — start.sh kills current process and starts new one
            subprocess.Popen(
                ["bash", "/opt/goliath/telegram-bot/start.sh"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return  # Skip voice memo — bot is about to restart

        # Send voice memo (only if user hasn't turned it off)
        voice_enabled = True
        if preferences:
            voice_enabled = await preferences.get_voice(chat_id)

        if voice_enabled:
            voice_path = await text_to_voice(result_text)
            if voice_path:
                try:
                    with open(voice_path, "rb") as audio:
                        await update.message.reply_voice(voice=audio)
                except Exception:
                    logger.exception("Failed to send voice memo")
                finally:
                    voice_path.unlink(missing_ok=True)

    except asyncio.TimeoutError:
        run_success = False
        run_error = "Timed out after 30 minutes"
        await working_msg.edit_text(
            "Damn, that one timed out after 30 minutes. Try breaking it into a smaller task."
        )
    except Exception as e:
        run_success = False
        run_error = str(e)[:500]
        logger.exception("Orchestration failed")
        await working_msg.edit_text(f"Something broke: {str(e)[:500]}")
    finally:
        # Stop the periodic "still working" heartbeat
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        # Log the activity (with token summary appended to subagent results)
        total_duration = time.monotonic() - run_start
        if activity_log:
            try:
                subagent_log = getattr(orchestrator, '_subagent_log', None)
                # Append token summary to the activity log if available
                token_summary = (
                    orch_result.token_summary if orch_result else None
                )
                if token_summary and subagent_log is not None:
                    subagent_log.append({
                        "agent": "_token_summary",
                        "success": True,
                        "duration": 0,
                        "total_input": token_summary["total_input"],
                        "total_output": token_summary["total_output"],
                        "total_tokens": token_summary["total_tokens"],
                        "total_cost_usd": token_summary["total_cost_usd"],
                    })
                await activity_log.log_run(
                    chat_id=chat_id,
                    query=user_message,
                    total_duration=total_duration,
                    success=run_success,
                    error=run_error,
                    nimrod_pass1_duration=getattr(orchestrator, '_pass1_duration', None),
                    nimrod_pass2_duration=getattr(orchestrator, '_pass2_duration', None),
                    subagent_results=subagent_log,
                )
            except Exception:
                logger.exception("Failed to write activity log")

        # Fire-and-forget post-interaction reflection (async, non-blocking)
        try:
            from bot.memory.reflection import post_interaction_hook
            _sub_log = getattr(orchestrator, '_subagent_log', None) or []
            _resp = orch_result.text if orch_result else ''
            post_interaction_hook(
                user_message=user_message,
                response_text=_resp,
                agents_used=[s['agent'] for s in _sub_log],
                duration_ms=int(total_duration * 1000),
                errors=[s['error'] for s in _sub_log if not s['success'] and s.get('error')]
                       + ([run_error] if run_error else []),
                subagent_count=len(_sub_log),
                failed_subagents=sum(1 for s in _sub_log if not s['success']),
            )
        except Exception:
            logger.debug("Reflection hook failed (non-fatal)", exc_info=True)
