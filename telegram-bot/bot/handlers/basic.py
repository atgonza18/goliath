import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import PROJECTS
from bot.services.project_service import get_project_summary, get_portfolio_overview

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info(f"/start from chat_id={chat_id}")
    welcome = (
        "What's up. I'm Nimrod, COO of GOLIATH.\n\n"
        "I run ops for 12 solar construction projects. I've got a team of "
        "specialist agents I can spin up — schedule analysis, constraints, "
        "production tracking, report writing, Excel work, you name it.\n\n"
        "Just tell me what you need in plain English. Or hit /help "
        "if you want the full menu.\n\n"
        "I remember things between conversations, so I'll get sharper the more we work together."
    )
    await update.message.reply_text(welcome)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "GOLIATH Commands:\n\n"
        "/start — Welcome message\n"
        "/help — This menu\n"
        "/status — Portfolio overview (all 12 projects)\n"
        "/project <name> — Detail for one project\n"
        "/files <project> [subfolder] — List files in a project folder\n"
        "/read <project> <filepath> — Read a file's content\n"
        "/memory <query> — Search my memory (or: recent, actions)\n"
        "/agents — See my specialist team\n"
        "/logs — Activity log (what agents ran, timing, success/fail)\n"
        "/voice on|off — Toggle voice memos\n"
        "/history — Conversation history (or: /history clear)\n\n"
        "Or just type whatever you need in plain English — I'll figure it out."
    )
    await update.message.reply_text(help_text)


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    overview = get_portfolio_overview()
    await update.message.reply_text(overview, parse_mode="Markdown")


async def project_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        project_list = "\n".join(
            f"  `{key}` — {info['name']}" for key, info in PROJECTS.items()
        )
        await update.message.reply_text(
            f"Usage: /project <name>\n\nAvailable projects:\n{project_list}",
            parse_mode="Markdown",
        )
        return

    # Allow flexible matching: "union-ridge", "union ridge", "unionridge"
    raw = " ".join(args).lower().strip()
    key = raw.replace(" ", "-")

    if key not in PROJECTS:
        # Try fuzzy: check if input is a substring of any project key or name
        matches = [
            k for k, v in PROJECTS.items()
            if raw in k or raw in v["name"].lower()
        ]
        if len(matches) == 1:
            key = matches[0]
        elif len(matches) > 1:
            match_list = ", ".join(f"`{m}`" for m in matches)
            await update.message.reply_text(
                f"Multiple matches: {match_list}\nBe more specific.",
                parse_mode="Markdown",
            )
            return
        else:
            await update.message.reply_text(
                f"Unknown project: `{raw}`\nUse /project to see the list.",
                parse_mode="Markdown",
            )
            return

    summary = get_project_summary(key)
    await update.message.reply_text(summary, parse_mode="Markdown")
