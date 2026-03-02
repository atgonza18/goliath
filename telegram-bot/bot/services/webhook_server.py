"""Lightweight aiohttp webhook server for Power Automate integration."""

import logging
from aiohttp import web

logger = logging.getLogger(__name__)


def _check_auth(request, token: str) -> bool:
    if not token:
        return True  # no token configured = open (dev mode)
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {token}"


async def handle_health(request):
    return web.json_response({"status": "ok"})


async def handle_email(request):
    token = request.app["webhook_auth_token"]
    if not _check_auth(request, token):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    queue = request.app["message_queue"]
    queue_id = await queue.enqueue(
        source="email",
        sender=data.get("from"),
        recipient=data.get("to"),
        subject=data.get("subject"),
        body=data.get("body"),
        external_message_id=data.get("message_id"),
    )

    logger.info(f"Webhook received email, queued as id={queue_id}")
    return web.json_response({"queued": True, "id": queue_id}, status=202)


async def handle_teams(request):
    token = request.app["webhook_auth_token"]
    if not _check_auth(request, token):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    queue = request.app["message_queue"]
    queue_id = await queue.enqueue(
        source="teams",
        sender=data.get("from"),
        body=data.get("body"),
        channel=data.get("channel"),
        is_dm=data.get("is_dm", False),
        external_message_id=data.get("message_id"),
    )

    logger.info(f"Webhook received Teams message, queued as id={queue_id}")
    return web.json_response({"queued": True, "id": queue_id}, status=202)


async def handle_recall_webhook(request):
    """Handle Recall.ai webhook callbacks (bot status changes, transcript ready).

    Recall.ai sends POST requests when bot status changes (joining, in_call,
    done, fatal) and when transcripts are ready. We update our DB and trigger
    transcript processing when appropriate.

    Note: Recall.ai webhooks don't use our auth token — they use their own
    signing mechanism. For now we accept all POSTs to this endpoint since
    it's a unique path and the data is non-sensitive (just status updates).
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    event = data.get("event", "unknown")
    bot_data = data.get("data", {}).get("bot", {})
    bot_id = bot_data.get("id", "")

    logger.info(f"Recall webhook: event={event}, bot_id={bot_id}")

    recall_service = request.app.get("recall_service")
    if recall_service and bot_id:
        try:
            # Update bot status in DB
            status_map = {
                "bot.joining_call": "joining",
                "bot.in_waiting_room": "waiting_room",
                "bot.in_call_not_recording": "in_call",
                "bot.in_call_recording": "recording",
                "bot.call_ended": "call_ended",
                "bot.done": "done",
                "bot.fatal": "fatal",
            }
            new_status = status_map.get(event)
            if new_status:
                await recall_service._db.execute(
                    """UPDATE recall_bots
                       SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%S','now')
                       WHERE bot_id = ?""",
                    (new_status, bot_id),
                )
                await recall_service._db.commit()
        except Exception:
            logger.exception(f"Failed to update Recall bot status for {bot_id}")

    return web.json_response({"received": True}, status=200)


async def handle_outbox(request):
    token = request.app["webhook_auth_token"]
    if not _check_auth(request, token):
        return web.json_response({"error": "unauthorized"}, status=401)

    queue = request.app["message_queue"]
    source = request.query.get("source")
    items = await queue.get_outbox(source=source)

    # Shape the response for Power Automate consumption
    results = []
    for item in items:
        entry = {
            "id": item["id"],
            "source": item["source"],
            "sender": item["sender"],
            "recipient": item["recipient"],
            "subject": item["subject"],
            "channel": item["channel"],
            "external_message_id": item["external_message_id"],
            "response": item["approved_response"],
        }
        # Include CC recipients if present (reply-all behavior)
        if item.get("cc"):
            entry["cc"] = item["cc"]
        results.append(entry)

    return web.json_response({"items": results})


def create_webhook_app(
    message_queue,
    auth_token: str,
    recall_service=None,
    memory=None,
    web_conversations=None,
    token_tracker=None,
) -> web.Application:
    app = web.Application()
    app["message_queue"] = message_queue
    app["webhook_auth_token"] = auth_token
    if recall_service:
        app["recall_service"] = recall_service

    app.router.add_get("/webhook/health", handle_health)
    app.router.add_post("/webhook/email", handle_email)
    app.router.add_post("/webhook/teams", handle_teams)
    app.router.add_get("/webhook/outbox", handle_outbox)
    app.router.add_post("/webhook/recall", handle_recall_webhook)

    # --- Web Platform API routes ---
    if memory and web_conversations:
        app["memory"] = memory
        app["web_conversations"] = web_conversations
        if token_tracker:
            app["token_tracker"] = token_tracker

        from bot.web_api import setup_web_routes
        setup_web_routes(app)
        logger.info("Web platform API routes registered on webhook server")
    else:
        logger.info("Web platform API not registered (memory or web_conversations not provided)")

    return app


async def start_webhook_server(
    message_queue,
    auth_token: str,
    port: int = 8000,
    recall_service=None,
    memory=None,
    web_conversations=None,
    token_tracker=None,
):
    app = create_webhook_app(
        message_queue,
        auth_token,
        recall_service=recall_service,
        memory=memory,
        web_conversations=web_conversations,
        token_tracker=token_tracker,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server started on port {port}")
    return runner
