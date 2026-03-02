import asyncio
import logging
from pathlib import Path

from telegram.ext import ApplicationBuilder, ContextTypes

from bot.config import (
    TELEGRAM_BOT_TOKEN, MEMORY_DB_PATH,
    WEBHOOK_PORT, WEBHOOK_AUTH_TOKEN, REPORT_CHAT_ID,
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
    RECALL_API_KEY, REPO_ROOT,
)
from bot.handlers import register_all_handlers
from bot.memory.store import MemoryStore
from bot.memory.conversation import ConversationStore
from bot.memory.activity_log import ActivityLogStore
from bot.memory.token_tracker import TokenTracker
from bot.memory.reflection import ReflectionStore, set_reflection_store
from bot.services.message_queue import MessageQueue
from bot.services.preferences import PreferenceStore
from bot.services.webhook_server import start_webhook_server
from bot.services.queue_processor import run_queue_processor
from bot.web_api import WebConversationStore
from bot.services.email_service import EmailService
from bot.services.recall_service import RecallService
from bot.scheduler import create_scheduler
from bot.utils.logging_config import setup_logging
from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)

# Keep references so background tasks aren't garbage-collected
_background_tasks: list[asyncio.Task] = []


async def post_init(application) -> None:
    """Initialize memory, conversation, message queue, webhook server, and queue processor."""
    memory = MemoryStore(MEMORY_DB_PATH)
    await memory.initialize()
    application.bot_data["memory"] = memory

    # Conversation history shares the same DB connection
    conversation = ConversationStore(memory._db)
    await conversation.initialize()
    application.bot_data["conversation"] = conversation

    # Activity log shares the same DB connection
    activity_log = ActivityLogStore(memory._db)
    await activity_log.initialize()
    application.bot_data["activity_log"] = activity_log

    # Token tracker shares the same DB connection
    token_tracker = TokenTracker(memory._db)
    await token_tracker.initialize()
    application.bot_data["token_tracker"] = token_tracker

    # Wire token tracker into both runner backends
    from bot.agents.runner import set_token_tracker as set_cli_tracker
    from bot.agents.runner_sdk import set_token_tracker as set_sdk_tracker
    set_cli_tracker(token_tracker)
    set_sdk_tracker(token_tracker)
    logger.info("Token tracker initialized and wired into agent runners")

    # Reflection store shares the same DB connection
    reflection = ReflectionStore(memory._db)
    await reflection.initialize()
    application.bot_data["reflection"] = reflection
    set_reflection_store(reflection)
    logger.info("Reflection store initialized (post-interaction self-scoring active)")

    # User preferences shares the same DB connection
    preferences = PreferenceStore(memory._db)
    await preferences.initialize()
    application.bot_data["preferences"] = preferences

    # Web conversation store shares the same DB connection
    web_conversations = WebConversationStore(memory._db)
    await web_conversations.initialize()
    application.bot_data["web_conversations"] = web_conversations

    # Message queue shares the same DB connection
    queue = MessageQueue(memory._db)
    await queue.initialize()
    application.bot_data["message_queue"] = queue

    # Email service (Gmail SMTP sender for approved drafts)
    email_service = EmailService()
    email_service.set_queue(queue)
    application.bot_data["email_service"] = email_service
    if email_service.is_configured:
        logger.info("Email service initialized (Gmail SMTP ready)")
    else:
        logger.warning("Email service not configured — GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing")

    # Recall.ai meeting bot service (automated Teams transcription)
    recall_service = RecallService(memory._db)
    await recall_service.initialize()
    application.bot_data["recall_service"] = recall_service
    if recall_service.is_configured:
        logger.info("Recall.ai meeting bot service initialized")
    else:
        logger.warning("Recall.ai not configured — RECALL_API_KEY missing from .env")

    # Expose bot_data on the bot instance so scheduler tasks and approval
    # handlers can access services (queue, email_service) without holding
    # a reference to the Application object.
    application.bot._bot_data_ref = application.bot_data

    # Clean up stale conversation turns on startup
    await conversation.cleanup_old(max_age_hours=48)

    # Schedule hourly cleanup (if job_queue is available)
    if application.job_queue:
        application.job_queue.run_repeating(
            _cleanup_conversations, interval=3600, first=3600
        )

    # Start webhook + web API server
    # Always start the server — web platform API needs it even without WEBHOOK_AUTH_TOKEN.
    # Webhook endpoints still check their own auth; web API uses WEB_API_KEY if set.
    webhook_runner = await start_webhook_server(
        queue, WEBHOOK_AUTH_TOKEN, WEBHOOK_PORT,
        recall_service=recall_service,
        memory=memory,
        web_conversations=web_conversations,
        token_tracker=token_tracker,
    )
    application.bot_data["webhook_runner"] = webhook_runner
    if WEBHOOK_AUTH_TOKEN:
        logger.info(f"Webhook + Web API server running on port {WEBHOOK_PORT}")
    else:
        logger.info(f"Web API server running on port {WEBHOOK_PORT} (webhook auth not configured — webhook endpoints open)")

    # Start queue processor if REPORT_CHAT_ID is configured
    if REPORT_CHAT_ID:
        chat_id = int(REPORT_CHAT_ID)
        task = asyncio.create_task(
            run_queue_processor(queue, memory, application.bot, chat_id)
        )
        _background_tasks.append(task)
        logger.info(f"Queue processor started, sending approvals to chat_id={chat_id}")

        # Proactive sessions (6 AM + 6 PM CT) now handled by the custom scheduler
        # to avoid job_queue double-firing issues
    else:
        logger.warning("REPORT_CHAT_ID not set — queue processor and proactive sessions disabled")

    # Start the internal async scheduler (replaces system crontab)
    scheduler = create_scheduler(bot=application.bot)
    application.bot_data["scheduler"] = scheduler
    task = scheduler.start()
    _background_tasks.append(task)
    logger.info("Internal async scheduler started")

    # Send startup notification and process any pending startup tasks
    if REPORT_CHAT_ID:
        try:
            chat_id = int(REPORT_CHAT_ID)

            # Run startup self-test
            self_test_note = ""
            try:
                self_test_note = await _run_startup_self_test()
                if self_test_note:
                    self_test_note = f"\n\n{self_test_note}"
            except Exception as e:
                logger.warning(f"Startup self-test failed: {e}")
                self_test_note = "\n\n<i>Self-test: could not run</i>"

            # Check for pending transcripts to process
            pending_transcripts = _find_pending_transcripts()
            pending_note = ""
            if pending_transcripts:
                names = ", ".join(p.name for p in pending_transcripts)
                pending_note = (
                    f"\n\n<b>Processing pending transcript(s):</b> {names}\n"
                    "Full pipeline: analysis, constraint extraction, ConstraintsPro sync. "
                    "Results incoming shortly."
                )

            await application.bot.send_message(
                chat_id=chat_id,
                text="<b>GOLIATH is online.</b>\n"
                     "Bot restarted and all systems operational.\n\n"
                     f"<i>Scheduler active with {len(scheduler.list_tasks())} tasks.</i>"
                     f"{self_test_note}"
                     f"{pending_note}",
                parse_mode="HTML",
            )

            # Fire off transcript processing as a background task
            if pending_transcripts:
                task = asyncio.create_task(
                    _process_pending_transcripts(
                        application.bot, memory, chat_id, pending_transcripts
                    )
                )
                _background_tasks.append(task)

        except Exception as e:
            logger.warning(f"Failed to send startup notification: {e}")


def _find_pending_transcripts() -> list[Path]:
    """Scan all project transcript folders for unprocessed transcripts.

    A transcript is "pending" if it exists and has no corresponding
    .processed marker file next to it. Only returns transcripts from
    the last 2 days to avoid reprocessing ancient files.
    """
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")

    pending = []
    cutoff = datetime.now(CT).strftime("%Y-%m-%d")
    # Also check yesterday's date
    yesterday = (datetime.now(CT) - timedelta(days=1)).strftime("%Y-%m-%d")

    projects_dir = REPO_ROOT / "projects"
    if not projects_dir.exists():
        return pending

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        transcripts_dir = project_dir / "transcripts"
        if not transcripts_dir.exists():
            continue
        for transcript in transcripts_dir.glob("*.txt"):
            # Skip hidden/marker files
            if transcript.name.startswith("."):
                continue
            # Only process recent transcripts (today or yesterday)
            if not (transcript.name.startswith(cutoff) or transcript.name.startswith(yesterday)):
                continue
            # Check for processed marker
            marker = transcript.parent / f".{transcript.stem}.processed"
            if not marker.exists():
                pending.append(transcript)

    return pending


async def _process_pending_transcripts(
    bot, memory: MemoryStore, chat_id: int, transcripts: list[Path]
) -> None:
    """Process unprocessed transcripts through the full orchestrator pipeline.

    Runs after startup with a short delay to let everything initialize.
    Each transcript is processed sequentially to avoid overwhelming the system.
    """
    await asyncio.sleep(15)  # Let bot fully initialize

    for transcript_path in transcripts:
        project_key = transcript_path.parent.parent.name  # projects/<key>/transcripts/<file>
        logger.info(f"Startup task: processing transcript {transcript_path.name} for {project_key}")

        try:
            # Import here to avoid circular imports
            from bot.agents.orchestrator import NimrodOrchestrator

            orchestrator = NimrodOrchestrator(memory=memory)
            user_message = (
                f"Process the {project_key} meeting transcript that was captured. "
                f"The transcript file is at: {transcript_path}\n\n"
                f"[TRANSCRIPT UPLOAD DETECTED: {transcript_path.name} — saved to: {transcript_path}]\n"
                f"This is a meeting/call transcript. Route to transcript_processor subagent for full analysis.\n"
                f"Extract: meeting summary, speakers, project identification, constraints discussed, "
                f"action items (WHO/WHAT/WHEN), key decisions, risks, and follow-ups.\n"
                f"Save action items and decisions to memory. File the processed summary to the project's "
                f"transcripts/ folder."
            )

            result = await orchestrator.handle_message(user_message)

            if result.text:
                for chunk in chunk_message(result.text, max_len=4000):
                    try:
                        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
                    except Exception:
                        await bot.send_message(chat_id=chat_id, text=chunk)

                # Send any generated files
                for fp in result.file_paths:
                    file_path = Path(fp)
                    if file_path.is_file():
                        try:
                            with open(file_path, "rb") as f:
                                await bot.send_document(
                                    chat_id=chat_id, document=f, filename=file_path.name
                                )
                        except Exception:
                            logger.exception(f"Failed to send file: {file_path}")

            # Mark as processed so we don't redo on next restart
            marker = transcript_path.parent / f".{transcript_path.stem}.processed"
            marker.touch()
            logger.info(f"Transcript processed and marked: {marker}")

        except Exception as e:
            logger.exception(f"Failed to process transcript {transcript_path.name}")
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"<b>⚠️ Failed to process {transcript_path.name}:</b> {str(e)[:300]}",
                    parse_mode="HTML",
                )
            except Exception:
                pass


async def _cleanup_conversations(context: ContextTypes.DEFAULT_TYPE) -> None:
    conversation = context.bot_data.get("conversation")
    if conversation:
        await conversation.cleanup_old(max_age_hours=48)


async def _run_startup_self_test() -> str:
    """Run the self-test script and return an HTML summary for the startup message.

    Executes self_test.py's run_self_test_summary() in-process (it's fast and
    doesn't use Claude CLI or network calls that could hang). Falls back to
    a subprocess if the import fails.
    """
    import sys as _sys
    cron_dir = REPO_ROOT / "cron-jobs"

    # Try importing directly (fastest path)
    try:
        if str(cron_dir) not in _sys.path:
            _sys.path.insert(0, str(cron_dir))
        from self_test import run_self_test_summary
        return run_self_test_summary()
    except ImportError:
        logger.warning("Could not import self_test module, falling back to subprocess")

    # Fallback: run as subprocess
    try:
        script_path = cron_dir / "self_test.py"
        if not script_path.exists():
            return "<i>Self-test: script not found</i>"

        env = dict(os.environ)
        env.pop("CLAUDECODE", None)

        process = await asyncio.create_subprocess_exec(
            _sys.executable, "-c",
            f"import sys; sys.path.insert(0, '{cron_dir}'); "
            f"from self_test import run_self_test_summary; print(run_self_test_summary())",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
        return stdout.decode(errors="replace").strip()
    except Exception as e:
        logger.warning(f"Self-test subprocess failed: {e}")
        return f"<i>Self-test: failed ({type(e).__name__})</i>"


def main():
    setup_logging()
    logger.info("Starting GOLIATH Telegram Bot...")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        # Allow multiple user messages to be processed in parallel (swarm mode).
        # Default is sequential (1 at a time). True = up to 256 concurrent.
        .concurrent_updates(True)
        # Network timeouts — prevent the bot from hanging on flaky connections.
        # These apply to all Telegram API calls (send_message, send_document, etc.)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        # Separate timeouts for long-polling getUpdates calls
        .get_updates_read_timeout(60)
        .get_updates_write_timeout(60)
        .get_updates_connect_timeout(30)
        .get_updates_pool_timeout(30)
        .post_init(post_init)
        .build()
    )
    register_all_handlers(app)

    logger.info("Bot is polling. Send /start in Telegram to begin.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
