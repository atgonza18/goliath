import asyncio
import logging

from telegram.ext import ApplicationBuilder, ContextTypes

from bot.config import (
    TELEGRAM_BOT_TOKEN, MEMORY_DB_PATH,
    WEBHOOK_PORT, WEBHOOK_AUTH_TOKEN, REPORT_CHAT_ID,
)
from bot.handlers import register_all_handlers
from bot.memory.store import MemoryStore
from bot.memory.conversation import ConversationStore
from bot.memory.activity_log import ActivityLogStore
from bot.services.message_queue import MessageQueue
from bot.services.preferences import PreferenceStore
from bot.services.webhook_server import start_webhook_server
from bot.services.queue_processor import run_queue_processor
from bot.services.proactive import schedule_proactive_sessions
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

        # Schedule proactive thinking sessions (6 AM + 6 PM CT)
        if application.job_queue:
            schedule_proactive_sessions(application.job_queue, chat_id)
    else:
        logger.warning("REPORT_CHAT_ID not set — queue processor and proactive sessions disabled")


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
        .post_init(post_init)
        .build()
    )
    register_all_handlers(app)

    logger.info("Bot is polling. Send /start in Telegram to begin.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
