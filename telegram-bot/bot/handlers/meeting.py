"""Telegram handlers for Recall.ai meeting bot integration.

Commands:
  /join <teams_link>  — Send the Goliath notetaker bot to a Teams meeting
  /meetings           — Show active and recent meeting bot sessions

Auto-detection:
  Any message containing a Teams meeting URL will offer to send the bot.
"""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from bot.services.recall_service import RecallService

logger = logging.getLogger(__name__)

# Regex to detect Teams meeting URLs
TEAMS_URL_PATTERN = re.compile(
    r'https?://teams\.microsoft\.com/l/meetup-join/[^\s<>"\']+',
    re.IGNORECASE,
)


async def join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /join command — send a bot to a Teams meeting.

    Usage: /join <teams_meeting_url>
    """
    recall: RecallService = context.bot_data.get("recall_service")
    if not recall or not recall.is_configured:
        await update.message.reply_text(
            "⚠️ Recall.ai is not configured. Add RECALL_API_KEY to .env and restart."
        )
        return

    # Get the meeting URL from the command arguments
    args_text = update.message.text.replace("/join", "", 1).strip()

    # Try to extract a Teams URL from the args
    meeting_url = RecallService.extract_teams_url(args_text)
    if not meeting_url and args_text:
        # Maybe they just pasted the URL directly
        meeting_url = args_text if "teams.microsoft.com" in args_text.lower() else None

    if not meeting_url:
        await update.message.reply_text(
            "<b>Usage:</b> <code>/join &lt;Teams meeting link&gt;</code>\n\n"
            "Paste a Microsoft Teams meeting link and I'll send the "
            "notetaker bot to join, record, and transcribe the call.\n\n"
            "You can also just paste a Teams link in chat and I'll ask if you want me to join.",
            parse_mode="HTML",
        )
        return

    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🤖 <b>Sending Aaron Gonzalez (notetaker) to join the meeting...</b>\n"
        "The bot will appear as 'Aaron Gonzalez' in the participant list.",
        parse_mode="HTML",
    )

    result = await recall.send_bot_to_meeting(meeting_url, chat_id)

    if "error" in result:
        await update.message.reply_text(
            f"❌ <b>Failed to join meeting:</b> {result['error']}",
            parse_mode="HTML",
        )
    else:
        bot_id = result.get("bot_id", "unknown")
        await update.message.reply_text(
            f"✅ <b>Bot dispatched!</b>\n\n"
            f"• Bot ID: <code>{bot_id[:8]}...</code>\n"
            f"• Status: Joining meeting\n"
            f"• The bot will record and transcribe automatically\n"
            f"• When the meeting ends, I'll process the transcript and send you "
            f"the summary, action items, and any constraints discussed\n\n"
            f"<i>Note: The host may need to admit the bot from the Teams lobby.</i>",
            parse_mode="HTML",
        )


async def meetings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /meetings command — show active and recent meeting bot sessions."""
    recall: RecallService = context.bot_data.get("recall_service")
    if not recall or not recall.is_configured:
        await update.message.reply_text(
            "⚠️ Recall.ai is not configured. Add RECALL_API_KEY to .env and restart."
        )
        return

    active_bots = await recall.get_active_bots()
    recent_bots = await recall.get_recent_bots(limit=5)

    lines = ["<b>🎙️ Meeting Bot Sessions</b>\n"]

    if active_bots:
        lines.append("<b>Active:</b>")
        for bot in active_bots:
            bid = bot["bot_id"][:8]
            lines.append(f"  • <code>{bid}</code> — {bot['status']} (since {bot['created_at'][:16]})")
    else:
        lines.append("<i>No active meeting bots.</i>")

    if recent_bots:
        lines.append("\n<b>Recent:</b>")
        for bot in recent_bots:
            bid = bot["bot_id"][:8]
            status = "✅" if bot.get("completed_at") else ("❌" if bot.get("error") else "⏳")
            transcript = "📝" if bot.get("transcript_file") else ""
            lines.append(f"  {status} <code>{bid}</code> — {bot['status']} {transcript}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
