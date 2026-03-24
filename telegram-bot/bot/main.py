import asyncio
import logging
import os
import signal
import socket as _socket
import sys
from pathlib import Path

from telegram.ext import ApplicationBuilder, ContextTypes

# ---------------------------------------------------------------------------
# TCP keepalive monkey-patch (applied before any network code runs)
# ---------------------------------------------------------------------------
# Hetzner Cloud NAT drops TCP connections idle for 300+ seconds.
# Claude Opus extended thinking creates silent API connections with no data
# flow for several minutes.  After 300s Hetzner silently drops the NAT entry;
# Node.js CLI gets ECONNRESET, terminates with SIGTERM, Python sees exit -15.
#
# Fix: force SO_KEEPALIVE + short TCP_KEEPIDLE on every socket this process
# creates.  Overrides the system default (7200s) even if we can't touch sysctl.
# The sysctl values in /opt/goliath/deploy/99-goliath-keepalive.conf should
# also be applied by root to cover the Claude CLI subprocess sockets.
_orig_socket_init = _socket.socket.__init__


def _keepalive_socket_init(self, *args, **kwargs):
    _orig_socket_init(self, *args, **kwargs)
    try:
        if self.type in (_socket.SOCK_STREAM,):
            self.setsockopt(_socket.SOL_SOCKET, _socket.SO_KEEPALIVE, 1)
            self.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPIDLE, 60)
            self.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPINTVL, 10)
            self.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_KEEPCNT, 6)
    except OSError:
        pass  # Non-TCP sockets (UDP, Unix) ignore these options — that's fine


_socket.socket.__init__ = _keepalive_socket_init
# ---------------------------------------------------------------------------

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
from bot.memory.experience_replay import ExperienceReplayStore
from bot.memory.reliability_log import ReliabilityLogStore
from bot.memory.tiered_memory import TieredMemoryStore
from bot.agents.model_router import ModelRouter
from bot.agents.tool_registry import ToolRegistry, set_tool_registry
from bot.services.message_queue import MessageQueue
from bot.services.preferences import PreferenceStore
from bot.services.webhook_server import start_webhook_server
from bot.services.queue_processor import run_queue_processor
from bot.web_api import WebConversationStore, AppBuilderStore
from bot.services.email_service import EmailService
from bot.services.recall_service import RecallService
from bot.services.teams_meeting_detector import TeamsMeetingDetector
from bot.services.token_health import TokenHealthMonitor, set_token_health_monitor
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

    # Experience replay store shares the same DB connection
    experience_replay = ExperienceReplayStore(memory._db)
    await experience_replay.initialize()
    application.bot_data["experience_replay"] = experience_replay
    logger.info("Experience replay store initialized (lesson extraction + prompt injection ready)")

    # Reliability log store shares the same DB connection (GAP H1)
    reliability_log = ReliabilityLogStore(memory._db)
    await reliability_log.initialize()
    application.bot_data["reliability_log"] = reliability_log
    logger.info("Reliability log store initialized (subagent call tracking active)")

    # Tiered memory store shares the same DB connection (GAP H2)
    tiered_memory = TieredMemoryStore(memory._db)
    await tiered_memory.initialize()
    application.bot_data["tiered_memory"] = tiered_memory
    logger.info("Tiered memory store initialized (episodic + semantic tiers active)")

    # Model router shares the same DB connection (GAP H3)
    model_router = ModelRouter(memory._db)
    await model_router.initialize()
    application.bot_data["model_router"] = model_router
    # Make globally accessible so runner_sdk can query overrides and log outcomes
    from bot.agents.model_router import set_global_router as _set_global_router
    _set_global_router(model_router)
    logger.info("Model router initialized (learned routing active)")

    # Tool registry — loads tools/manifest.yaml at startup (GAP H4)
    tool_registry = ToolRegistry.load()
    set_tool_registry(tool_registry)
    application.bot_data["tool_registry"] = tool_registry
    loaded_tools = tool_registry.list_tools()
    logger.info(
        f"Tool registry loaded: {len(loaded_tools)} tool(s) active "
        f"({[t['name'] for t in loaded_tools]})"
    )

    # Dispatch lint check — assert no CLI-based agent dispatch (GAP H5)
    try:
        from bot.agents.dispatch_lint import lint_bot_dispatch
        lint_report = lint_bot_dispatch()
        if lint_report["clean"]:
            logger.info(
                f"Dispatch lint: CLEAN ({lint_report['files_scanned']} files scanned, "
                "no CLI dispatch violations)"
            )
        else:
            violations = lint_report["violations"]
            logger.warning(
                f"Dispatch lint: {len(violations)} CLI dispatch violation(s) detected — "
                "agents should use get_runner() instead of subprocess. "
                f"Violations: {violations}"
            )
    except Exception as lint_exc:
        logger.warning(f"Dispatch lint check failed (non-fatal): {lint_exc}")

    # User preferences shares the same DB connection
    preferences = PreferenceStore(memory._db)
    await preferences.initialize()
    application.bot_data["preferences"] = preferences

    # Web conversation store shares the same DB connection
    web_conversations = WebConversationStore(memory._db)
    await web_conversations.initialize()
    application.bot_data["web_conversations"] = web_conversations

    # App builder store shares the same DB connection
    app_builder_store = AppBuilderStore(memory._db)
    await app_builder_store.initialize()
    application.bot_data["app_builder_store"] = app_builder_store

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

        # Seed the persistent dedup tracker with already-processed bots
        # so the cron poller doesn't reprocess them after restart
        try:
            from bot.services.recall_transcript_poller import RecallTranscriptPoller
            poller = RecallTranscriptPoller(recall_service, memory._db)
            await _seed_recall_tracker(poller, memory._db)
        except Exception as e:
            logger.warning(f"Failed to seed recall transcript tracker: {e}")
    else:
        logger.warning("Recall.ai not configured — RECALL_API_KEY missing from .env")

    # Teams Meeting Auto-Detector (intercepts pasted Teams invites in Telegram)
    meeting_detector = TeamsMeetingDetector(recall_service)
    application.bot_data["meeting_detector"] = meeting_detector
    logger.info("Teams meeting auto-detector initialized")

    # Token health monitor — proactive OAuth token refresh and /reauth support
    token_health = TokenHealthMonitor()
    chat_id_for_alerts = int(REPORT_CHAT_ID) if REPORT_CHAT_ID else None
    token_health.set_bot(application.bot, chat_id_for_alerts)
    set_token_health_monitor(token_health)
    application.bot_data["token_health"] = token_health
    # Run an initial token health check on startup
    try:
        status = token_health.get_token_status()
        logger.info(
            f"Token health on startup: {status.get('status', 'unknown')} "
            f"(expires in: {status.get('expires_in_human', 'unknown')})"
        )
        if status.get("status") in ("expiring_soon", "critical", "expired"):
            logger.warning("Token needs refresh — attempting now")
            asyncio.create_task(token_health.try_refresh_now(reason="startup"))
    except Exception as e:
        logger.warning(f"Startup token health check failed: {e}")

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
        # recall_webhook_secret no longer needed — polling replaces webhooks
        app_builder_store=app_builder_store,
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


async def _seed_recall_tracker(poller, db) -> None:
    """Seed the persistent dedup tracker with bots that already have transcripts.

    On startup, any bot in the recall_bots table that has a transcript_file
    (meaning the transcript was already fetched and processed) should be
    added to the tracker. This prevents the cron poller from reprocessing
    transcripts that were handled by the now-dead in-memory per-bot pollers.

    Also seeds bots that have errors (fatal status) so we don't keep
    retrying failed bots.
    """
    try:
        cursor = await db.execute(
            """SELECT bot_id, transcript_file, completed_at, error
               FROM recall_bots
               WHERE transcript_file IS NOT NULL AND transcript_file != ''
                  OR error IS NOT NULL"""
        )
        rows = await cursor.fetchall()

        seeded = 0
        for row in rows:
            bot_id = row["bot_id"]
            if not poller.is_processed(bot_id):
                transcript_file = row["transcript_file"] or ""
                if row["error"]:
                    transcript_file = f"ERROR: {row['error']}"
                poller.mark_processed(bot_id, transcript_file=transcript_file)
                seeded += 1

        if seeded > 0:
            logger.info(
                f"Recall tracker seeded: {seeded} previously-processed bot(s) "
                f"added to dedup tracker (total: {poller.get_processed_count()})"
            )
        else:
            logger.info(
                f"Recall tracker: {poller.get_processed_count()} bot(s) already tracked, "
                "no new seeds needed"
            )
    except Exception:
        logger.exception("Failed to seed recall transcript tracker from DB")


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


_conflict_count = 0
_conflict_window_start = 0.0
_CONFLICT_THRESHOLD = 50     # exit if this many Conflicts in the window
_CONFLICT_WINDOW_SECS = 120  # sliding window


async def _handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler for the Telegram bot.

    Conflict errors are normal and frequent — the library retries automatically.
    We only intervene if Conflicts become excessive (>50 in 2 minutes), which
    indicates a genuine dual-instance problem.  Network errors are silently
    retried by the library.
    """
    import time
    from telegram.error import Conflict, NetworkError, TimedOut

    error = context.error

    if isinstance(error, Conflict):
        global _conflict_count, _conflict_window_start
        now = time.monotonic()
        if now - _conflict_window_start > _CONFLICT_WINDOW_SECS:
            _conflict_count = 0
            _conflict_window_start = now
        _conflict_count += 1

        if _conflict_count <= 3 or _conflict_count % 20 == 0:
            logger.warning(f"Telegram Conflict #{_conflict_count} (library will retry)")

        if _conflict_count >= _CONFLICT_THRESHOLD:
            logger.critical(
                f"Excessive Conflicts ({_conflict_count} in {_CONFLICT_WINDOW_SECS}s). "
                "Exiting for systemd restart."
            )
            os._exit(1)
        return

    if isinstance(error, (NetworkError, TimedOut)):
        logger.warning(f"Telegram network error (will retry): {error}")
        return

    logger.error(f"Unhandled error: {error}", exc_info=context.error)


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


def _install_signal_handlers():
    """Install signal handlers for graceful shutdown.

    SIGTERM from systemd (ExecStop or MemoryMax cgroup kill) and SIGINT
    (Ctrl-C during dev) are caught so we can log why the bot is stopping
    rather than dying silently.  The default Python behaviour (raising
    SystemExit / KeyboardInterrupt) still fires, which lets
    Application.run_polling() clean up properly.
    """
    import resource

    def _log_shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.warning(
            f"Received {sig_name} (signal {signum}) — initiating graceful shutdown. "
            f"Pending orchestrations will be cancelled."
        )
        # Log memory usage at shutdown for diagnostics
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss_mb = usage.ru_maxrss / 1024  # Linux: ru_maxrss is in KB
            logger.info(f"Peak RSS at shutdown: {rss_mb:.0f} MB")
        except Exception:
            pass
        # Re-raise the default handler so Python's normal shutdown proceeds
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, _log_shutdown)
    signal.signal(signal.SIGINT, _log_shutdown)


def main():
    setup_logging()
    _install_signal_handlers()
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
    app.add_error_handler(_handle_error)

    logger.info("Bot is polling. Send /start in Telegram to begin.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
