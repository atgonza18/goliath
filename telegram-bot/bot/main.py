import asyncio
import logging

from telegram.ext import ApplicationBuilder, ContextTypes

from bot.config import (
    TELEGRAM_BOT_TOKEN, MEMORY_DB_PATH,
    WEBHOOK_PORT, WEBHOOK_AUTH_TOKEN, REPORT_CHAT_ID,
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
)
from bot.handlers import register_all_handlers
from bot.memory.store import MemoryStore
from bot.memory.conversation import ConversationStore
from bot.memory.activity_log import ActivityLogStore
from bot.services.message_queue import MessageQueue
from bot.services.preferences import PreferenceStore
from bot.services.webhook_server import start_webhook_server
from bot.services.queue_processor import run_queue_processor
from bot.services.email_service import EmailService
from bot.scheduler import create_scheduler
from bot.utils.logging_config import setup_logging

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

    # User preferences shares the same DB connection
    preferences = PreferenceStore(memory._db)
    await preferences.initialize()
    application.bot_data["preferences"] = preferences

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

    # Start webhook server
    if WEBHOOK_AUTH_TOKEN:
        webhook_runner = await start_webhook_server(queue, WEBHOOK_AUTH_TOKEN, WEBHOOK_PORT)
        application.bot_data["webhook_runner"] = webhook_runner
        logger.info(f"Webhook server running on port {WEBHOOK_PORT}")
    else:
        logger.warning("WEBHOOK_AUTH_TOKEN not set — webhook server disabled")

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

    # Send startup notification
    if REPORT_CHAT_ID:
        try:
            chat_id = int(REPORT_CHAT_ID)
            await application.bot.send_message(
                chat_id=chat_id,
                text="<b>GOLIATH is online.</b>\n"
                     "Bot restarted and all systems operational.\n\n"
                     f"<i>Scheduler active with {len(scheduler.list_tasks())} tasks.</i>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"Failed to send startup notification: {e}")


async def _cleanup_conversations(context: ContextTypes.DEFAULT_TYPE) -> None:
    conversation = context.bot_data.get("conversation")
    if conversation:
        await conversation.cleanup_old(max_age_hours=48)


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
