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
    # Short body preview — just enough context to know what the email was about
    raw_body = (queue_item["body"] or "").strip()
    body_short = escape(raw_body[:200].rsplit(" ", 1)[0] + ("…" if len(raw_body) > 200 else ""))
    # Full draft — show everything so the user can review before approving
    # Telegram max message is 4096 chars; reserve ~400 for header/buttons
    raw_draft = (queue_item["draft_response"] or "").strip()
    max_draft = 3600
    draft_display = escape(
        raw_draft[:max_draft] + ("…" if len(raw_draft) > max_draft else "")
    )
    queue_id = queue_item["id"]

    if source == "email":
        header = f"📧 <b>{sender}</b>"
        meta = f"<b>Re:</b> {subject}"
    else:
        channel = escape(queue_item.get("channel") or "DM")
        header = f"💬 <b>{sender}</b>"
        meta = f"<b>Channel:</b> {channel}"

    text = (
        f"{header}\n"
        f"{meta}\n"
        f"<i>{body_short}</i>\n\n"
        f"<b>Draft reply:</b>\n{draft_display}\n\n"
        f"Approve, edit, or reject?"
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

        # If source is email, send it now via Gmail SMTP
        send_status = ""
        if item["source"] == "email":
            email_service = context.bot_data.get("email_service")
            if email_service and email_service.is_configured:
                try:
                    sent = await email_service.send_approved_message(queue_id)
                    if sent:
                        send_status = "\nEmail sent successfully."
                    else:
                        send_status = "\nEmail send failed — check logs. Item remains approved for retry."
                except Exception as e:
                    logger.exception(f"Email send failed for queue item {queue_id}")
                    send_status = f"\nEmail send error: {str(e)[:100]}"
            else:
                send_status = "\nEmail service not configured — queued for outbox pickup."

        # Show full draft in confirmation (capped at Telegram's 4096 limit minus header)
        draft_text = escape(item['draft_response'] or '')[:3400]
        await query.edit_message_text(
            f"✅ <b>Approved & sent.</b>{send_status}\n\n"
            f"<i>{draft_text}</i>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    elif action == "reject":
        await queue.reject(queue_id)
        await query.edit_message_text("❌ <b>Rejected</b> — no response will be sent.", parse_mode="HTML")
        return ConversationHandler.END

    elif action == "edit":
        context.user_data["editing_queue_id"] = queue_id
        # Show full draft so user knows what they're editing
        draft_text = escape(item['draft_response'] or '')[:3400]
        await query.edit_message_text(
            f"✏️ <b>Editing response</b>\n\n"
            f"Current draft:\n<i>{draft_text}</i>\n\n"
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

    # Show full updated draft for review
    draft_text = escape(edited_text[:3400])
    await update.message.reply_text(
        f"<b>Updated draft:</b>\n{draft_text}\n\nApprove, edit again, or reject?",
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
