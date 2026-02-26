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
        results.append({
            "id": item["id"],
            "source": item["source"],
            "sender": item["sender"],
            "recipient": item["recipient"],
            "subject": item["subject"],
            "channel": item["channel"],
            "external_message_id": item["external_message_id"],
            "response": item["approved_response"],
        })

    return web.json_response({"items": results})


def create_webhook_app(message_queue, auth_token: str) -> web.Application:
    app = web.Application()
    app["message_queue"] = message_queue
    app["webhook_auth_token"] = auth_token

    app.router.add_get("/webhook/health", handle_health)
    app.router.add_post("/webhook/email", handle_email)
    app.router.add_post("/webhook/teams", handle_teams)
    app.router.add_get("/webhook/outbox", handle_outbox)

    return app


async def start_webhook_server(message_queue, auth_token: str, port: int = 8000):
    app = create_webhook_app(message_queue, auth_token)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server started on port {port}")
    return runner
