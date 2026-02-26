"""Simple outbound Teams posting via incoming webhook."""

import logging
import aiohttp

logger = logging.getLogger(__name__)


async def post_to_teams(webhook_url: str, message_text: str) -> bool:
    """Post an adaptive card message to a Teams incoming webhook.

    Returns True on success, False on failure.
    """
    if not webhook_url:
        logger.warning("No Teams incoming webhook URL configured")
        return False

    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": message_text,
                            "wrap": True,
                        }
                    ],
                },
            }
        ],
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 202):
                    logger.info("Teams message sent successfully")
                    return True
                else:
                    body = await resp.text()
                    logger.error(f"Teams webhook failed: {resp.status} {body}")
                    return False
    except Exception:
        logger.exception("Failed to post to Teams webhook")
        return False
