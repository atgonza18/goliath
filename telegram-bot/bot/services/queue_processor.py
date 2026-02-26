"""Background async processor for the message queue.

Picks up new inbound messages, routes them through Nimrod for draft responses,
then pushes approval requests to Telegram.
"""

import asyncio
import logging

from bot.agents.orchestrator import NimrodOrchestrator
from bot.services.message_queue import MessageQueue

logger = logging.getLogger(__name__)


def _build_draft_prompt(item: dict) -> str:
    source = item["source"]
    sender = item["sender"] or "Unknown"
    subject = item["subject"] or "(no subject)"
    body = item["body"] or "(empty)"
    channel = item.get("channel") or ""

    if source == "email":
        return (
            f"You received the following email.\n\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Body: {body}\n\n"
            f"Draft a professional response on behalf of the user. Keep the user's communication "
            f"style in mind (check memories for preferences). Be concise and direct.\n"
            f"Output ONLY the response text — no preamble, no explanation."
        )
    else:
        context = f"in channel #{channel}" if channel else "as a DM"
        return (
            f"You received the following Teams message {context}.\n\n"
            f"From: {sender}\n"
            f"Message: {body}\n\n"
            f"Draft a professional response on behalf of the user. Keep the user's communication "
            f"style in mind (check memories for preferences). Be concise and direct.\n"
            f"Output ONLY the response text — no preamble, no explanation."
        )


async def process_queue_once(queue: MessageQueue, memory, bot, chat_id: int) -> int:
    """Process all pending items. Returns number of items processed."""
    pending = await queue.get_pending()
    if not pending:
        return 0

    orchestrator = NimrodOrchestrator(memory=memory)
    processed = 0

    for item in pending:
        try:
            prompt = _build_draft_prompt(item)
            result = await asyncio.wait_for(
                orchestrator.handle_message(prompt),
                timeout=300,
            )

            draft = result.text
            await queue.update_draft(item["id"], draft)

            # Send approval request to Telegram
            from bot.handlers.approval import send_approval_request
            updated_item = await queue.get_by_id(item["id"])
            await send_approval_request(bot, chat_id, updated_item)

            processed += 1
            logger.info(f"Processed queue item {item['id']} — draft ready for approval")

        except asyncio.TimeoutError:
            logger.error(f"Queue item {item['id']} timed out during Nimrod processing")
        except Exception:
            logger.exception(f"Failed to process queue item {item['id']}")

    return processed


async def run_queue_processor(queue: MessageQueue, memory, bot, chat_id: int, interval: int = 30):
    """Background loop that processes the queue every `interval` seconds."""
    logger.info(f"Queue processor started (interval={interval}s, chat_id={chat_id})")
    while True:
        try:
            count = await process_queue_once(queue, memory, bot, chat_id)
            if count:
                logger.info(f"Queue processor handled {count} item(s)")
        except Exception:
            logger.exception("Queue processor error")

        await asyncio.sleep(interval)
