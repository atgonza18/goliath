import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import PROJECTS, PROJECT_SUBFOLDERS
from bot.services.project_service import list_project_files, read_project_file
from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)


async def files_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List files: /files <project> [subfolder]"""
    args = context.args
    if not args:
        subfolder_list = "\n".join(f"  `{s}`" for s in PROJECT_SUBFOLDERS)
        await update.message.reply_text(
            f"Usage: /files <project> [subfolder]\n\nSubfolders:\n{subfolder_list}",
            parse_mode="Markdown",
        )
        return

    project_key = args[0].lower().replace(" ", "-")
    subfolder = args[1] if len(args) > 1 else None

    if project_key not in PROJECTS:
        await update.message.reply_text(
            f"Unknown project: `{project_key}`\nUse /project to see the list.",
            parse_mode="Markdown",
        )
        return

    file_list = list_project_files(project_key, subfolder)
    await update.message.reply_text(file_list, parse_mode="Markdown")


async def read_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Read a file: /read <project> <relative_path>"""
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /read <project> <path/to/file>\n\n"
            "Example: `/read union-ridge constraints/tracker.md`",
            parse_mode="Markdown",
        )
        return

    project_key = args[0].lower().replace(" ", "-")
    file_path = " ".join(args[1:])

    if project_key not in PROJECTS:
        await update.message.reply_text(
            f"Unknown project: `{project_key}`\nUse /project to see the list.",
            parse_mode="Markdown",
        )
        return

    content = read_project_file(project_key, file_path)

    for chunk in chunk_message(content, max_len=4000):
        await update.message.reply_text(chunk, parse_mode="Markdown")
