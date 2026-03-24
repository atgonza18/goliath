"""Lightweight aiohttp webhook server for Power Automate integration."""

import hashlib
import hmac
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
    """Handle Recall.ai webhook callbacks (bot status changes, transcript ready).

    Recall.ai sends POST requests when bot status changes (joining, in_call,
    done, fatal) and when transcripts are ready. We update our DB and trigger
    transcript processing when appropriate.

    Security layers (HIGH-SEVERITY ENGINE GAP FIX):
      1. Rate limiting — max 30 requests/min per IP
      2. HMAC signature verification if RECALL_WEBHOOK_SECRET is configured
      3. Bot ID validation — reject callbacks for unknown bot IDs
    """
    # Layer 1: Rate limiting
    if not _check_rate_limit(request):
        logger.warning(f"Recall webhook rate-limited: {request.remote}")
        return web.json_response({"error": "rate limited"}, status=429)

    # Always read the raw body first — needed for both HMAC verification
    # and JSON parsing regardless of whether a secret is configured.
    raw_body = await request.read()

    # Layer 2: HMAC signature verification (if secret is configured)
    # Recall.ai sends the signature as "sha256=<hex_digest>" in the
    # X-Recall-Signature header.  We must strip the "sha256=" prefix before
    # comparing, or every webhook is rejected with 403.
    recall_secret = request.app.get("recall_webhook_secret")
    if recall_secret:
        sig_header = request.headers.get("X-Recall-Signature", "")
        # Strip "sha256=" prefix if present (Recall.ai v2 webhook format)
        sig_value = sig_header.removeprefix("sha256=")
        expected_sig = hmac.new(
            recall_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        if not sig_value or not hmac.compare_digest(sig_value, expected_sig):
            logger.warning(
                f"Recall webhook HMAC mismatch from {request.remote} "
                f"(got={sig_header[:30]}..., expected=sha256={expected_sig[:20]}...)"
            )
            return web.json_response({"error": "invalid signature"}, status=403)

    try:
        import json as _json
        data = _json.loads(raw_body)
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    event = data.get("event", "unknown")
    bot_data = data.get("data", {}).get("bot", {})
    bot_id = bot_data.get("id", "")

    logger.info(f"Recall webhook: event={event}, bot_id={bot_id}")

    # Layer 3: Bot ID validation — only accept callbacks for bots we created
    recall_service = request.app.get("recall_service")
    if recall_service and bot_id:
        try:
            known_bot = await recall_service._db.execute(
                "SELECT id FROM recall_bots WHERE bot_id = ? LIMIT 1",
                (bot_id,),
            )
            if not await known_bot.fetchone():
                logger.warning(
                    f"Recall webhook rejected: unknown bot_id={bot_id} "
                    f"from {request.remote}"
                )
                return web.json_response({"error": "unknown bot"}, status=404)
        except Exception:
            # DB check failed — allow through but log (fail-open for resilience)
            logger.debug("Could not verify bot_id against DB — allowing through", exc_info=True)

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

        # ── CRITICAL: When the bot is done, kick off transcript processing ─────
        # The webhook is the fastest and most reliable delivery path.
        # We fire a background task immediately rather than waiting for the
        # 2-minute cron poller to notice the completed bot.
        #
        # The RecallTranscriptPoller dedup tracker ensures that even if both
        # the webhook trigger AND the cron poller run, the transcript is only
        # processed once.
        if event in ("bot.done", "bot.call_ended"):
            try:
                # Look up the chat_id so we can route results to the right user
                cursor = await recall_service._db.execute(
                    "SELECT chat_id FROM recall_bots WHERE bot_id = ? LIMIT 1",
                    (bot_id,),
                )
                row = await cursor.fetchone()
                chat_id = (row["chat_id"] if row else None) or 0

                import asyncio as _asyncio
                from bot.services.recall_transcript_poller import RecallTranscriptPoller
                _asyncio.create_task(
                    _webhook_trigger_transcript(recall_service, bot_id, chat_id)
                )
                logger.info(
                    f"Recall webhook: queued transcript fetch for bot {bot_id[:8]} "
                    f"(event={event}, chat_id={chat_id})"
                )
            except Exception:
                logger.exception(
                    f"Failed to queue webhook-triggered transcript fetch for {bot_id[:8]}"
                )

    return web.json_response({"received": True}, status=200)


async def _webhook_trigger_transcript(recall_service, bot_id: str, chat_id: int) -> None:
    """Background task: fetch and process transcript after a bot.done webhook.

    Uses RecallTranscriptPoller for dedup-safe processing — if the cron poller
    or the in-memory per-bot poller already handled this bot, this is a no-op.

    On failure, sends a Telegram notification so the user knows to pull the
    transcript manually rather than waiting in silence.
    """
    import asyncio as _asyncio
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # Small delay: Recall.ai sometimes sends bot.done slightly before the
    # transcript S3 object is ready.  15 seconds gives the recording pipeline
    # time to finish before we attempt the first download.
    await _asyncio.sleep(15)

    try:
        from bot.services.recall_transcript_poller import RecallTranscriptPoller
        poller = RecallTranscriptPoller(recall_service, recall_service._db)

        # Dedup check — bail out if already handled
        if poller.is_processed(bot_id):
            _log.info(
                f"Recall webhook trigger: bot {bot_id[:8]} already in dedup tracker — skipping"
            )
            return

        result = await poller._check_and_process_bot(bot_id, chat_id)
        _log.info(
            f"Recall webhook trigger: transcript processing result for "
            f"bot {bot_id[:8]}: {result}"
        )
    except Exception:
        _log.exception(
            f"Recall webhook trigger: unexpected error processing bot {bot_id[:8]}"
        )
        # Notify the user that post-call processing failed so they don't
        # wait in silence.  They can pull the transcript manually.
        if chat_id:
            try:
                await recall_service._queue_notification(
                    chat_id,
                    f"⚠️ Post-call processing failed for bot "
                    f"{bot_id[:8]} — pull transcript manually with "
                    f"/meetings or ask me to re-process it.",
                )
            except Exception:
                _log.debug(
                    "Failed to queue failure notification for webhook trigger",
                    exc_info=True,
                )


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
    recall_webhook_secret: str = None,
    app_builder_store=None,
) -> web.Application:
    app = web.Application()
    app["message_queue"] = message_queue
    app["webhook_auth_token"] = auth_token
    if recall_webhook_secret:
        app["recall_webhook_secret"] = recall_webhook_secret
        logger.info("Recall webhook HMAC verification enabled")
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
