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

# Directory for downloaded photos/files
UPLOADS_DIR = REPO_ROOT / "telegram-bot" / "data" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


async def claude_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form text by routing through Nimrod orchestrator."""
    user_message = update.message.text
    chat_id = update.effective_chat.id
    logger.info(f"Message from chat_id={chat_id}: {user_message[:100]}...")

    await _run_orchestrator(update, context, user_message)


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

    await _run_orchestrator(update, context, user_message)


async def document_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document/file uploads — download and pass to Claude."""
    chat_id = update.effective_chat.id
    doc = update.message.document
    caption = update.message.caption or f"Analyze this file: {doc.file_name}"
    logger.info(f"Document from chat_id={chat_id}: {doc.file_name} ({doc.file_size} bytes)")

    # Download the file
    doc_file = await context.bot.get_file(doc.file_id)
    file_path = UPLOADS_DIR / doc.file_name
    await doc_file.download_to_drive(str(file_path))
    logger.info(f"Document saved to {file_path}")

    user_message = (
        f"{caption}\n\n"
        f"[The user uploaded a file: {doc.file_name} — saved to: {file_path}]\n"
        f"Read and analyze this file."
    )

    await _run_orchestrator(update, context, user_message)


async def _run_orchestrator(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str
):
    """Shared orchestration logic for text, photos, and documents."""
    memory = context.bot_data.get("memory")
    conversation = context.bot_data.get("conversation")
    activity_log = context.bot_data.get("activity_log")
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

    orchestrator = NimrodOrchestrator(memory=memory)

    working_msg = await update.message.reply_text(
        random.choice(STATUS_MESSAGES)
    )

    run_start = time.monotonic()
    run_success = True
    run_error = None

    try:
        # 15 minute overall timeout — generous for multi-agent report generation
        orch_result = await asyncio.wait_for(
            orchestrator.handle_message(user_message, conv_history),
            timeout=900,
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
        run_error = "Timed out after 15 minutes"
        await working_msg.edit_text(
            "Damn, that one timed out after 15 minutes. Try breaking it into a smaller task."
        )
    except Exception as e:
        run_success = False
        run_error = str(e)[:500]
        logger.exception("Orchestration failed")
        await working_msg.edit_text(f"Something broke: {str(e)[:500]}")
    finally:
        # Log the activity
        total_duration = time.monotonic() - run_start
        if activity_log:
            try:
                await activity_log.log_run(
                    chat_id=chat_id,
                    query=user_message,
                    total_duration=total_duration,
                    success=run_success,
                    error=run_error,
                    nimrod_pass1_duration=getattr(orchestrator, '_pass1_duration', None),
                    nimrod_pass2_duration=getattr(orchestrator, '_pass2_duration', None),
                    subagent_results=getattr(orchestrator, '_subagent_log', None),
                )
            except Exception:
                logger.exception("Failed to write activity log")
