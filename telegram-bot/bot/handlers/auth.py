"""/reauth and /tokenhealth command handlers.

/reauth — Manual OAuth re-authentication from Telegram.
  Step 1: User sends /reauth
  Step 2: Bot generates OAuth URL and sends it
  Step 3: User taps URL, logs into Claude, gets redirected with a code
  Step 4: User copies the code from the URL and sends it back
  Step 5: Bot exchanges the code for new tokens

/tokenhealth — Shows current token status (expiry, health, refresh history).
"""

import logging
import re
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.services.token_health import get_token_health_monitor

logger = logging.getLogger(__name__)

# ConversationHandler states
AWAITING_CODE = 1


async def reauth_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the re-auth flow — send OAuth URL to user."""
    monitor = get_token_health_monitor()
    status = monitor.get_token_status()

    auth_url, state = monitor.generate_reauth_url()

    status_text = (
        f"Current token status: <b>{status.get('status', 'unknown').upper()}</b>\n"
        f"Expires in: {status.get('expires_in_human', 'unknown')}\n\n"
    )

    await update.message.reply_text(
        f"🔐 <b>Manual Re-Authentication</b>\n\n"
        f"{status_text}"
        f"<b>Step 1:</b> Tap the link below to log in:\n"
        f"<a href=\"{auth_url}\">Log in to Claude</a>\n\n"
        f"<b>Step 2:</b> After logging in, you'll be redirected to a URL. "
        f"Copy the <code>code</code> parameter from the URL and paste it here.\n\n"
        f"The URL will look like:\n"
        f"<code>https://claude.ai/oauth/callback?code=XXXXX&amp;state=...</code>\n\n"
        f"Copy everything after <code>code=</code> and before the next <code>&amp;</code>, "
        f"then send it to me.\n\n"
        f"<i>This session expires in 15 minutes.</i>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    return AWAITING_CODE


async def reauth_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the auth code from the user and exchange it for tokens."""
    text = update.message.text.strip()

    # Try to extract code from a full URL or just the raw code
    code = text
    url_match = re.search(r'[?&]code=([^&\s]+)', text)
    if url_match:
        code = url_match.group(1)

    if not code or len(code) < 10:
        await update.message.reply_text(
            "That doesn't look like a valid auth code. "
            "It should be a long string of characters.\n"
            "Try again or send /cancel to abort.",
            parse_mode="HTML",
        )
        return AWAITING_CODE

    monitor = get_token_health_monitor()

    await update.message.reply_text("Exchanging code for tokens... ⏳")

    success = await monitor.exchange_auth_code(code)

    if success:
        new_status = monitor.get_token_status()
        await update.message.reply_text(
            "✅ <b>Re-authentication successful!</b>\n\n"
            f"New token status: <b>{new_status.get('status', 'unknown').upper()}</b>\n"
            f"Expires in: {new_status.get('expires_in_human', 'unknown')}\n\n"
            "All systems are go. The bot will auto-refresh this token "
            "before it expires next time.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            "❌ <b>Re-authentication failed.</b>\n\n"
            "The code might be expired or invalid. Common issues:\n"
            "• The code expires quickly — try again promptly\n"
            "• Make sure you copied the full code value\n"
            "• Send /reauth to start a fresh attempt",
            parse_mode="HTML",
        )

    return ConversationHandler.END


async def reauth_cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the re-auth flow."""
    await update.message.reply_text("Re-auth cancelled.", parse_mode="HTML")
    return ConversationHandler.END


async def tokenhealth_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current token health status."""
    monitor = get_token_health_monitor()
    status = monitor.get_token_status()

    status_emoji = {
        "healthy": "🟢",
        "expiring_soon": "🟡",
        "critical": "🟠",
        "expired": "🔴",
        "unknown": "⚪",
    }

    emoji = status_emoji.get(status.get("status", "unknown"), "⚪")
    lines = [
        f"{emoji} <b>Token Health: {status.get('status', 'unknown').upper()}</b>\n",
        f"<b>Expires in:</b> {status.get('expires_in_human', 'unknown')}",
    ]

    if status.get("expires_at"):
        lines.append(
            f"<b>Expires at:</b> {status['expires_at'].strftime('%b %d, %I:%M %p CT')}"
        )

    lines.append(f"<b>Has refresh token:</b> {'Yes' if status.get('has_refresh_token') else 'No'}")

    if status.get("consecutive_refresh_failures", 0) > 0:
        lines.append(
            f"<b>Consecutive refresh failures:</b> {status['consecutive_refresh_failures']}"
        )

    if status.get("last_successful_refresh"):
        lines.append(
            f"<b>Last successful refresh:</b> "
            f"{status['last_successful_refresh'].strftime('%b %d, %I:%M %p CT')}"
        )

    lines.append(f"\n<b>Access token:</b> <code>{status.get('access_token_prefix', 'n/a')}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def build_reauth_conversation_handler(user_filter) -> ConversationHandler:
    """Build the ConversationHandler for the multi-step /reauth flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("reauth", reauth_handler, filters=user_filter)],
        states={
            AWAITING_CODE: [
                CommandHandler("cancel", reauth_cancel_handler),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & user_filter,
                    reauth_code_handler,
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", reauth_cancel_handler)],
        conversation_timeout=900,  # 15 min timeout
    )
