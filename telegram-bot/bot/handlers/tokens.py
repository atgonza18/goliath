import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)


async def tokens_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tokens [days] — show token usage summary.

    Usage:
      /tokens       — last 7 days
      /tokens 30    — last 30 days
      /tokens today — today only
    """
    token_tracker = context.bot_data.get("token_tracker")
    if not token_tracker:
        await update.message.reply_text("Token tracker not initialized.")
        return

    args = context.args

    # /tokens today — shortcut for today's usage
    if args and args[0] == "today":
        today = await token_tracker.get_daily_total()
        text = (
            "<b>Token Usage (Today)</b>\n\n"
            f"<b>Calls:</b> {today['calls']}\n"
            f"<b>Input:</b> {today['input_tokens']:,} tokens\n"
            f"<b>Output:</b> {today['output_tokens']:,} tokens\n"
            f"<b>Total:</b> {today['total_tokens']:,} tokens "
            f"(${today['cost_usd']:.2f})\n"
            f"<b>Cache read:</b> {today['cache_read_tokens']:,} tokens\n"
            f"<b>Cache creation:</b> {today['cache_creation_tokens']:,} tokens"
        )
        try:
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text)
        return

    # /tokens [N] — summary for last N days (default 7)
    days = 7
    if args:
        try:
            days = int(args[0])
            days = max(1, min(days, 365))
        except ValueError:
            await update.message.reply_text(
                "Usage:\n"
                "  /tokens — last 7 days\n"
                "  /tokens 30 — last 30 days\n"
                "  /tokens today — today only"
            )
            return

    summary = await token_tracker.get_summary(days=days)
    today = await token_tracker.get_daily_total()

    # Build header
    lines = [
        f"<b>Token Usage (Last {days} Days)</b>\n",
        f"<b>Total:</b> {summary['total_tokens']:,} tokens "
        f"(${summary['total_cost_usd']:.2f})",
        f"<b>Calls:</b> {summary['total_calls']}",
        f"<b>Input:</b> {summary['total_input_tokens']:,}  "
        f"<b>Output:</b> {summary['total_output_tokens']:,}",
    ]

    # Per-agent breakdown
    if summary["by_agent"]:
        lines.append("\n<b>By Agent:</b>")
        for agent in summary["by_agent"]:
            lines.append(
                f"  {agent['agent']}: {agent['total_tokens']:,} tokens "
                f"(${agent['cost_usd']:.2f}) "
                f"— {agent['calls']} calls, "
                f"avg {agent['avg_tokens_per_call']:,}/call"
            )

    # Today's snapshot
    lines.append(
        f"\n<b>Today:</b> {today['total_tokens']:,} tokens "
        f"(${today['cost_usd']:.2f}) — {today['calls']} calls"
    )

    text = "\n".join(lines)
    for c in chunk_message(text, max_len=4000):
        try:
            await update.message.reply_text(c, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(c)
