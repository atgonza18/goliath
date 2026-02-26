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
        else:
            result = "No open action items."
    else:
        result = await memory.format_for_prompt(query=query, limit=15)

    for c in chunk_message(result):
        await update.message.reply_text(c)


async def agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available subagents: /agents"""
    registry = AgentRegistry()
    lines = ["GOLIATH Subagents:\n"]
    for agent in registry.list_subagents():
        lines.append(f"  {agent.display_name} — {agent.description}")
    lines.append("\nNimrod routes to these automatically. Just ask your question.")
    await update.message.reply_text("\n".join(lines))


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
