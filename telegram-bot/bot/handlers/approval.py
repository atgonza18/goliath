"""Telegram inline button handlers for email/Teams draft approval flow."""

import logging
from html import escape

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# ConversationHandler state for edit flow
WAITING_EDIT = 0


async def send_approval_request(bot, chat_id: int, queue_item: dict) -> None:
    """Send a formatted approval message with inline buttons to Telegram."""
    source = queue_item["source"]
    sender = escape(queue_item["sender"] or "Unknown")
    subject = escape(queue_item["subject"] or "(no subject)")
    body_preview = escape((queue_item["body"] or "")[:500])
    draft = escape(queue_item["draft_response"] or "")
    queue_id = queue_item["id"]

    if source == "email":
        header = f"📧 <b>New email from {sender}</b>"
        meta = f"<b>Subject:</b> {subject}"
    else:
        channel = escape(queue_item.get("channel") or "DM")
        header = f"💬 <b>New Teams message from {sender}</b>"
        meta = f"<b>Channel:</b> {channel}"

    text = (
        f"{header}\n"
        f"{meta}\n\n"
        f"<b>Message:</b>\n<i>{body_preview}</i>\n\n"
        f"<b>Draft response:</b>\n{draft}\n\n"
        f"Approve, edit, or reject this response?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{queue_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{queue_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{queue_id}"),
        ]
    ])

    from bot.services.message_queue import MessageQueue
    msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)

    # Store the telegram message ID for later editing
    queue: MessageQueue = bot._bot_data_ref.get("message_queue") if hasattr(bot, "_bot_data_ref") else None
    if queue:
        await queue.set_telegram_message(queue_id, chat_id, msg.message_id)


async def approval_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Approve/Edit/Reject button presses."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if ":" not in data:
        return ConversationHandler.END

    action, queue_id_str = data.split(":", 1)
    try:
        queue_id = int(queue_id_str)
    except ValueError:
        return ConversationHandler.END

    queue = context.bot_data.get("message_queue")
    if not queue:
        await query.edit_message_text("Message queue not available.")
        return ConversationHandler.END

    item = await queue.get_by_id(queue_id)
    if not item:
        await query.edit_message_text("Message not found in queue.")
        return ConversationHandler.END

    if action == "approve":
        await queue.approve(queue_id)
        await query.edit_message_text(
            f"✅ <b>Approved</b> — response queued for sending.\n\n"
            f"<i>{escape(item['draft_response'] or '')[:300]}</i>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    elif action == "reject":
        await queue.reject(queue_id)
        await query.edit_message_text("❌ <b>Rejected</b> — no response will be sent.", parse_mode="HTML")
        return ConversationHandler.END

    elif action == "edit":
        context.user_data["editing_queue_id"] = queue_id
        await query.edit_message_text(
            f"✏️ <b>Editing response</b>\n\n"
            f"Current draft:\n<i>{escape(item['draft_response'] or '')[:500]}</i>\n\n"
            f"Type your edited response below:",
            parse_mode="HTML",
        )
        return WAITING_EDIT

    return ConversationHandler.END


async def receive_edited_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the user's edited response text and re-present approval buttons."""
    queue_id = context.user_data.get("editing_queue_id")
    if not queue_id:
        await update.message.reply_text("No edit in progress.")
        return ConversationHandler.END

    queue = context.bot_data.get("message_queue")
    if not queue:
        await update.message.reply_text("Message queue not available.")
        return ConversationHandler.END

    edited_text = update.message.text
    await queue.update_draft(queue_id, edited_text)
    item = await queue.get_by_id(queue_id)

    # Re-send approval buttons with updated draft
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{queue_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{queue_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{queue_id}"),
        ]
    ])

    await update.message.reply_text(
        f"<b>Updated draft:</b>\n{escape(edited_text[:500])}\n\nApprove, edit again, or reject?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    context.user_data.pop("editing_queue_id", None)
    return ConversationHandler.END


async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the edit flow."""
    context.user_data.pop("editing_queue_id", None)
    await update.message.reply_text("Edit cancelled.")
    return ConversationHandler.END


def build_approval_conversation_handler(user_filter):
    """Build the ConversationHandler for the approval/edit flow."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(approval_callback_handler, pattern=r"^(approve|edit|reject):\d+$"),
        ],
        states={
            WAITING_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, receive_edited_response),
                MessageHandler(filters.COMMAND, cancel_edit),
            ],
        },
        fallbacks=[
            MessageHandler(filters.COMMAND, cancel_edit),
        ],
        per_message=False,
    )
