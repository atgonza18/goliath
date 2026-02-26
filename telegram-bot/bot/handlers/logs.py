import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)


async def logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/logs [N] [detail ID] — show recent agent activity."""
    activity_log = context.bot_data.get("activity_log")
    if not activity_log:
        await update.message.reply_text("Activity log not initialized.")
        return

    args = context.args

    # /logs detail <id> — show full breakdown of a single run
    if args and args[0] == "detail" and len(args) >= 2:
        try:
            log_id = int(args[1])
        except ValueError:
            await update.message.reply_text("Usage: /logs detail <id>")
            return

        detail = await activity_log.get_detail(log_id)
        if not detail:
            await update.message.reply_text(f"No log entry #{log_id}")
            return

        status = "OK" if detail["success"] else "FAILED"
        lines = [
            f"<b>Run #{detail['id']}</b> — {status}",
            f"<b>Time:</b> {detail['created_at']}",
            f"<b>Query:</b> {detail['query'][:200]}",
            f"<b>Total:</b> {detail['total_duration']:.1f}s",
        ]

        if detail["nimrod_pass1_duration"] is not None:
            lines.append(f"<b>Pass 1 (routing):</b> {detail['nimrod_pass1_duration']:.1f}s")

        if detail["subagent_results"]:
            lines.append("\n<b>Subagents:</b>")
            for sa in detail["subagent_results"]:
                s = "OK" if sa.get("success") else "FAIL"
                lines.append(f"  {sa['agent']} — {sa.get('duration', 0):.1f}s [{s}]")

        if detail["nimrod_pass2_duration"] is not None:
            lines.append(f"\n<b>Pass 2 (synthesis):</b> {detail['nimrod_pass2_duration']:.1f}s")

        if detail.get("error"):
            lines.append(f"\n<b>Error:</b> {detail['error'][:300]}")

        text = "\n".join(lines)
        try:
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text)
        return

    # /logs stats — aggregate stats
    if args and args[0] == "stats":
        stats = await activity_log.get_stats()
        fail = stats["total_runs"] - stats["successful"]
        text = (
            f"<b>Activity Stats</b>\n"
            f"Total runs: {stats['total_runs']}\n"
            f"Successful: {stats['successful']}  Failed: {fail}\n"
            f"Avg duration: {stats['avg_duration']:.1f}s\n"
            f"Max duration: {stats['max_duration']:.1f}s"
        )
        try:
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(text)
        return

    # /logs [N] — list recent runs (default 10)
    limit = 10
    if args:
        try:
            limit = int(args[0])
            limit = min(limit, 50)
        except ValueError:
            await update.message.reply_text(
                "Usage:\n"
                "  /logs — last 10 runs\n"
                "  /logs 20 — last 20 runs\n"
                "  /logs detail <id> — full breakdown\n"
                "  /logs stats — aggregate stats"
            )
            return

    entries = await activity_log.get_recent(limit)
    if not entries:
        await update.message.reply_text("No activity logged yet.")
        return

    lines = ["<b>Recent Activity</b>\n"]
    for e in entries:
        status = "OK" if e["success"] else "FAIL"
        agents = e["subagents"] or "direct"
        query_preview = e["query"][:60] if e["query"] else "?"
        lines.append(
            f"<b>#{e['id']}</b> [{status}] {e['total_duration']:.0f}s "
            f"| {agents}\n"
            f"  <i>{query_preview}</i>"
        )

    text = "\n".join(lines)
    for c in chunk_message(text, max_len=4000):
        try:
            await update.message.reply_text(c, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(c)
