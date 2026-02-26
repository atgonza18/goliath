import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/voice on|off — toggle voice memos."""
    preferences = context.bot_data.get("preferences")
    if not preferences:
        await update.message.reply_text("Preferences system not initialized.")
        return

    chat_id = update.effective_chat.id
    args = context.args

    if not args or args[0].lower() not in ("on", "off"):
        current = await preferences.get_voice(chat_id)
        status = "ON" if current else "OFF"
        await update.message.reply_text(
            f"Voice memos are currently <b>{status}</b>.\n\n"
            f"Usage: /voice on | /voice off",
            parse_mode="HTML",
        )
        return

    enabled = args[0].lower() == "on"
    await preferences.set_voice(chat_id, enabled)

    if enabled:
        await update.message.reply_text("Voice memos turned ON. I'll send audio with every response.")
    else:
        await update.message.reply_text("Voice memos turned OFF. Text only from here.")
