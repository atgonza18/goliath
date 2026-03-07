import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.agents.registry import AgentRegistry
from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)


async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search or browse memories: /memory <query> | recent | actions"""
    memory = context.bot_data.get("memory")
    if not memory:
        await update.message.reply_text("Memory system not initialized.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "  /memory recent — last 20 memories\n"
            "  /memory actions — open action items\n"
            "  /memory <search query> — search memories"
        )
        return

    query = " ".join(args)

    if query == "recent":
        result = await memory.format_for_prompt(limit=20)
    elif query == "actions":
        items = await memory.get_action_items(resolved=False)
        if items:
            result = "\n".join(
                f"- [{a.created_at[:10]}] {a.summary}" for a in items
            )
        elif hasattr(items, "success") and not items.success:
            result = f"Action items unavailable: {items.error}"
        else:
            result = "No open action items."
    else:
        result = await memory.format_for_prompt(query=query, limit=15)

    for c in chunk_message(result):
        await update.message.reply_text(c)


async def agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available subagents with usage stats: /agents"""
    registry = AgentRegistry()
    activity_log = context.bot_data.get("activity_log")

    # Fetch per-agent usage counts (last 30 days)
    usage = {}
    if activity_log:
        try:
            usage = await activity_log.get_agent_usage_counts(days=30)
        except Exception as e:
            logger.warning(f"Failed to fetch agent usage counts: {e}")

    subagents = registry.list_subagents()
    total_agents = len(subagents)
    active_agents = sum(1 for a in subagents if a.name in usage)

    lines = [
        f"<b>GOLIATH Agents</b>  —  {active_agents}/{total_agents} active (30d)",
        "",
    ]

    # Sort: most-used first, then unused alphabetically
    def sort_key(agent):
        count = usage.get(agent.name, {}).get("count", 0)
        return (-count, agent.display_name)

    for agent in sorted(subagents, key=sort_key):
        agent_usage = usage.get(agent.name, {})
        count = agent_usage.get("count", 0)

        # Color dot based on usage tier
        if count >= 20:
            dot = "\U0001f7e2"   # green — heavy use
        elif count >= 5:
            dot = "\U0001f535"   # blue — moderate
        elif count >= 1:
            dot = "\U0001f7e1"   # yellow — light
        else:
            dot = "\u26aa"       # white — unused

        # Format last-used as relative time
        last_used_str = ""
        if count > 0:
            last_used_raw = agent_usage.get("last_used", "")
            last_used_str = _format_relative_time(last_used_raw)

        # Build the agent line
        count_label = f"  <code>{count:>3}x</code>" if count > 0 else "  <code>  —</code>"
        lines.append(
            f"{dot} <b>{agent.display_name}</b>{count_label}"
            f"{'  ' + last_used_str if last_used_str else ''}"
        )
        lines.append(f"    <i>{agent.description}</i>")

    lines.append("")
    lines.append("Nimrod routes to these automatically.")

    text = "\n".join(lines)
    try:
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception:
        # Fallback to plain text if HTML fails
        await update.message.reply_text(text)


def _format_relative_time(iso_timestamp: str) -> str:
    """Convert ISO timestamp to a short relative label like '2h ago' or '3d ago'."""
    if not iso_timestamp:
        return ""
    try:
        from datetime import datetime, timezone
        # Parse the SQLite timestamp (UTC, no timezone info)
        dt = datetime.strptime(iso_timestamp[:19], "%Y-%m-%dT%H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        elif seconds < 604800:
            return f"{seconds // 86400}d ago"
        else:
            return f"{seconds // 604800}w ago"
    except Exception:
        return ""


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or clear conversation history: /history [clear]"""
    conversation = context.bot_data.get("conversation")
    if not conversation:
        await update.message.reply_text("Conversation system not initialized.")
        return

    chat_id = update.effective_chat.id

    if context.args and context.args[0] == "clear":
        deleted = await conversation.clear_chat(chat_id)
        await update.message.reply_text(f"Cleared {deleted} conversation turns.")
        return

    turns = await conversation.get_recent_turns(chat_id, max_turns=10)
    if not turns:
        await update.message.reply_text("No conversation history for this chat.")
        return

    lines = []
    for role, content in turns:
        label = "You" if role == "user" else "Nimrod"
        preview = content[:200] + "..." if len(content) > 200 else content
        lines.append(f"<b>{label}:</b> {preview}")

    for c in chunk_message("\n\n".join(lines)):
        try:
            await update.message.reply_text(c, parse_mode="HTML")
        except Exception:
            await update.message.reply_text(c)
