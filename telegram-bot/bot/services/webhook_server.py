"""Lightweight aiohttp webhook server for Power Automate integration."""

import logging
import time
from collections import defaultdict
from aiohttp import web

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter for webhook endpoints (defence against abuse/replay attacks)
# ---------------------------------------------------------------------------
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 30  # max requests per window per IP


def _check_rate_limit(request) -> bool:
    """Return True if request is within rate limits, False if blocked."""
    ip = request.remote or "unknown"
    now = time.monotonic()
    bucket = _rate_limit_buckets[ip]
    # Prune old entries
    bucket[:] = [t for t in bucket if now - t < _RATE_LIMIT_WINDOW]
    if len(bucket) >= _RATE_LIMIT_MAX:
        return False
    bucket.append(now)
    return True


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
    """Legacy Recall.ai webhook endpoint — kept for backward compatibility.

    We no longer rely on inbound webhooks from Recall.ai.  All transcript
    processing is driven by outbound polling (RecallTranscriptPoller every
    2 minutes).  This endpoint simply accepts and logs the event without
    triggering any processing.  The poller will pick it up on its next cycle.
    """
    # Accept the request and log for observability, but don't process
    try:
        data = await request.json()
        event = data.get("event", "unknown")
        bot_id = data.get("data", {}).get("bot", {}).get("id", "")
        logger.info(
            f"Recall webhook received (no-op): event={event}, bot_id={bot_id[:8] if bot_id else 'N/A'} "
            f"— transcript processing handled by outbound poller"
        )
    except Exception:
        pass

    return web.json_response({"received": True, "note": "polling-mode"}, status=200)


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
    recall_webhook_secret: str = None,  # Deprecated — kept for call-site compat
    app_builder_store=None,
) -> web.Application:
    app = web.Application()
    app["message_queue"] = message_queue
    app["webhook_auth_token"] = auth_token
    # NOTE: recall_webhook_secret is no longer used — we poll outbound
    # instead of receiving inbound webhooks.  The parameter is kept for
    # backward compatibility with existing call sites.
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
        if app_builder_store:
            app["app_builder_store"] = app_builder_store

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
    recall_webhook_secret: str = None,
    app_builder_store=None,
):
    app = create_webhook_app(
        message_queue,
        auth_token,
        recall_service=recall_service,
        memory=memory,
        web_conversations=web_conversations,
        token_tracker=token_tracker,
        recall_webhook_secret=recall_webhook_secret,
        app_builder_store=app_builder_store,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server started on port {port}")
    return runner
