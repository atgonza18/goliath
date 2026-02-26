from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.handlers.basic import start_handler, help_handler, status_handler, project_handler
from bot.handlers.files import files_handler, read_handler
from bot.handlers.admin import memory_handler, agents_handler, history_handler
from bot.handlers.logs import logs_handler
from bot.handlers.preferences import voice_handler
from bot.handlers.orchestration import (
    claude_message_handler,
    photo_message_handler,
    document_message_handler,
)
from bot.handlers.approval import build_approval_conversation_handler
from bot.config import ALLOWED_CHAT_IDS


def _build_user_filter():
    """If ALLOWED_CHAT_IDS is set, restrict to those users only."""
    if ALLOWED_CHAT_IDS:
        return filters.Chat(chat_id=ALLOWED_CHAT_IDS)
    return filters.ALL


def register_all_handlers(app: Application) -> None:
    user_filter = _build_user_filter()

    # Tier 1: Basic commands
    app.add_handler(CommandHandler("start", start_handler, filters=user_filter))
    app.add_handler(CommandHandler("help", help_handler, filters=user_filter))
    app.add_handler(CommandHandler("status", status_handler, filters=user_filter))
    app.add_handler(CommandHandler("project", project_handler, filters=user_filter))

    # Tier 2: File access commands
    app.add_handler(CommandHandler("files", files_handler, filters=user_filter))
    app.add_handler(CommandHandler("read", read_handler, filters=user_filter))

    # Admin: Memory, agent, and history commands
    app.add_handler(CommandHandler("memory", memory_handler, filters=user_filter))
    app.add_handler(CommandHandler("agents", agents_handler, filters=user_filter))
    app.add_handler(CommandHandler("history", history_handler, filters=user_filter))
    app.add_handler(CommandHandler("logs", logs_handler, filters=user_filter))
    app.add_handler(CommandHandler("voice", voice_handler, filters=user_filter))

    # Approval inline buttons + edit conversation flow (must be before catch-all)
    approval_handler = build_approval_conversation_handler(user_filter)
    app.add_handler(approval_handler)

    # Photos and documents
    app.add_handler(MessageHandler(filters.PHOTO & user_filter, photo_message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL & user_filter, document_message_handler))

    # Tier 3: Natural language -> Nimrod orchestrator (catch-all, must be last)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & user_filter,
        claude_message_handler,
    ))
