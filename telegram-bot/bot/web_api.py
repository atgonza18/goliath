"""Web Platform API for the Goliath system.

Adds REST + SSE endpoints to the existing aiohttp webhook server, powering
a React frontend for Goliath's construction operations platform.

Usage:
    from bot.web_api import setup_web_routes
    setup_web_routes(app, memory_store, db_connection)
"""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

import aiosqlite
from aiohttp import web

from bot.agents.definitions import ALL_AGENTS, SUBAGENTS, NIMROD
from bot.agents.orchestrator import NimrodOrchestrator
from bot.config import PROJECTS, PROJECTS_DIR, PROJECT_SUBFOLDERS, REPO_ROOT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Server boot time (for uptime calculation)
# ---------------------------------------------------------------------------
_BOOT_TIME = time.monotonic()
_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
_WEB_API_KEY = os.getenv("WEB_API_KEY", "")


def _check_web_auth(request: web.Request) -> bool:
    """Validate Bearer token if WEB_API_KEY is set. No key = open (dev mode)."""
    if not _WEB_API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {_WEB_API_KEY}"


def _cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }


def _json(data, *, status: int = 200) -> web.Response:
    """Return a JSON response with CORS headers."""
    return web.json_response(data, status=status, headers=_cors_headers())


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"error": message}, status=status)


# ---------------------------------------------------------------------------
# Web Conversation Store (SQLite)
# ---------------------------------------------------------------------------

WEB_CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
);

CREATE TABLE IF NOT EXISTS web_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES web_conversations(id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
);

CREATE INDEX IF NOT EXISTS idx_webmsg_conv
    ON web_messages(conversation_id, created_at);
"""


class WebConversationStore:
    """Minimal conversation store for web-originated chats."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db

    async def initialize(self) -> None:
        await self._db.executescript(WEB_CONVERSATION_SCHEMA)
        await self._db.commit()
        logger.info("Web conversation store initialized")

    async def create_conversation(self, conversation_id: str, title: str) -> None:
        await self._db.execute(
            "INSERT INTO web_conversations (id, title) VALUES (?, ?)",
            (conversation_id, title),
        )
        await self._db.commit()

    async def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> int:
        meta_json = json.dumps(metadata) if metadata else None
        cursor = await self._db.execute(
            "INSERT INTO web_messages (conversation_id, role, content, metadata) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, meta_json),
        )
        await self._db.execute(
            "UPDATE web_conversations SET updated_at = strftime('%Y-%m-%dT%H:%M:%S','now') "
            "WHERE id = ?",
            (conversation_id,),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def list_conversations(self, limit: int = 50) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "  (SELECT COUNT(*) FROM web_messages m WHERE m.conversation_id = c.id) as message_count "
            "FROM web_conversations c "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "title": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "message_count": row[4],
            }
            for row in rows
        ]

    async def get_messages(self, conversation_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT role, content, metadata, created_at FROM web_messages "
            "WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "role": row[0],
                "content": row[1],
                "metadata": json.loads(row[2]) if row[2] else None,
                "timestamp": row[3],
            }
            for row in rows
        ]

    async def conversation_exists(self, conversation_id: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM web_conversations WHERE id = ?",
            (conversation_id,),
        )
        return (await cursor.fetchone()) is not None

    async def get_history_for_prompt(self, conversation_id: str, max_turns: int = 20) -> str:
        """Format recent messages as a prompt-injectable conversation history block."""
        cursor = await self._db.execute(
            "SELECT role, content FROM web_messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC LIMIT ?",
            (conversation_id, max_turns),
        )
        rows = await cursor.fetchall()
        if not rows:
            return ""
        rows = list(reversed(rows))
        lines = []
        for role, content in rows:
            label = "User" if role == "user" else "Nimrod"
            truncated = content[:2000] + "..." if len(content) > 2000 else content
            lines.append(f"{label}: {truncated}")
        return "CONVERSATION HISTORY (recent):\n" + "\n\n".join(lines)


# ---------------------------------------------------------------------------
# In-memory SSE stream registry
# ---------------------------------------------------------------------------
# Maps conversation_id -> asyncio.Queue of SSE events.
# Each event is a dict: {"type": str, "data": str}
# A sentinel {"type": "_done"} signals end of stream.
_streams: dict[str, asyncio.Queue] = {}


def _get_or_create_stream(conversation_id: str) -> asyncio.Queue:
    if conversation_id not in _streams:
        _streams[conversation_id] = asyncio.Queue()
    return _streams[conversation_id]


def _cleanup_stream(conversation_id: str) -> None:
    _streams.pop(conversation_id, None)


# ---------------------------------------------------------------------------
# Orchestration bridge — run Nimrod pipeline, push results to SSE queue
# ---------------------------------------------------------------------------

async def _run_web_orchestration(
    conversation_id: str,
    user_message: str,
    memory_store,
    web_conversations: WebConversationStore,
    token_tracker=None,
):
    """Run Nimrod orchestration for a web chat and push SSE events."""
    queue = _get_or_create_stream(conversation_id)

    try:
        # Signal that processing has started
        await queue.put({"type": "thinking", "data": "Nimrod is processing your message..."})

        # Build conversation history from web store
        conv_history = await web_conversations.get_history_for_prompt(conversation_id)

        # Create orchestrator (same brain as Telegram)
        orchestrator = NimrodOrchestrator(memory=memory_store, token_tracker=token_tracker)

        # Run the pipeline
        result = await asyncio.wait_for(
            orchestrator.handle_message(user_message, conv_history),
            timeout=7200,  # Same 2-hour zombie safety net as Telegram
        )

        # Emit subagent dispatches if any occurred
        subagent_log = getattr(orchestrator, "_subagent_log", None) or []
        for entry in subagent_log:
            await queue.put({
                "type": "subagent",
                "data": json.dumps({
                    "agent": entry["agent"],
                    "success": entry["success"],
                    "duration": entry["duration"],
                }),
            })

        response_text = result.text

        # Stream response in chunks to simulate streaming
        # Break into ~200 char chunks for smooth UX
        chunk_size = 200
        for i in range(0, len(response_text), chunk_size):
            chunk = response_text[i : i + chunk_size]
            await queue.put({"type": "chunk", "data": chunk})
            # Tiny yield to let the SSE consumer keep up
            await asyncio.sleep(0.01)

        # Save assistant response
        metadata = {
            "subagents": subagent_log,
            "file_paths": result.file_paths,
        }
        if result.token_summary:
            metadata["token_summary"] = result.token_summary

        await web_conversations.add_message(
            conversation_id, "assistant", response_text, metadata=metadata
        )

        # Send complete event with the full response
        await queue.put({
            "type": "complete",
            "data": json.dumps({
                "text": response_text,
                "file_paths": result.file_paths,
                "subagents": subagent_log,
            }),
        })

    except asyncio.TimeoutError:
        logger.error(f"Web orchestration timed out for conversation {conversation_id}")
        await queue.put({
            "type": "error",
            "data": "Orchestration timed out after 2 hours.",
        })
    except Exception as e:
        logger.exception(f"Web orchestration failed for conversation {conversation_id}")
        await queue.put({
            "type": "error",
            "data": f"Orchestration error: {str(e)[:500]}",
        })
    finally:
        # Signal stream end
        await queue.put({"type": "_done", "data": ""})


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def handle_options(request: web.Request) -> web.Response:
    """Handle CORS preflight requests for all /api/* routes."""
    return web.Response(status=204, headers=_cors_headers())


async def handle_api_health(request: web.Request) -> web.Response:
    """GET /api/health — Health check."""
    uptime = time.monotonic() - _BOOT_TIME
    return _json({
        "status": "ok",
        "uptime": round(uptime, 1),
        "version": _VERSION,
    })


async def handle_chat(request: web.Request) -> web.Response:
    """POST /api/chat — Send a message to Nimrod."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    try:
        data = await request.json()
    except Exception:
        return _error("invalid JSON body")

    message = data.get("message", "").strip()
    if not message:
        return _error("message is required")

    conversation_id = data.get("conversation_id") or str(uuid.uuid4())

    memory_store = request.app["memory"]
    web_conversations: WebConversationStore = request.app["web_conversations"]
    token_tracker = request.app.get("token_tracker")

    # Create conversation if new
    if not await web_conversations.conversation_exists(conversation_id):
        title = message[:100] + ("..." if len(message) > 100 else "")
        await web_conversations.create_conversation(conversation_id, title)

    # Save user message
    await web_conversations.add_message(conversation_id, "user", message)

    # Kick off orchestration as a background task
    asyncio.create_task(
        _run_web_orchestration(
            conversation_id,
            message,
            memory_store,
            web_conversations,
            token_tracker=token_tracker,
        )
    )

    return _json({
        "conversation_id": conversation_id,
        "stream_url": f"/api/chat/stream/{conversation_id}",
    })


async def handle_chat_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/chat/stream/{conversation_id} — SSE stream for response."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    conversation_id = request.match_info["conversation_id"]
    queue = _get_or_create_stream(conversation_id)

    headers = _cors_headers()
    headers["Content-Type"] = "text/event-stream"
    headers["Cache-Control"] = "no-cache"
    headers["Connection"] = "keep-alive"

    response = web.StreamResponse(status=200, headers=headers)
    await response.prepare(request)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=300)
            except asyncio.TimeoutError:
                # Send keepalive comment
                await response.write(b": keepalive\n\n")
                continue

            if event["type"] == "_done":
                break

            sse_data = json.dumps({"type": event["type"], "data": event["data"]})
            await response.write(f"data: {sse_data}\n\n".encode("utf-8"))

    except (ConnectionResetError, asyncio.CancelledError):
        logger.debug(f"SSE client disconnected for conversation {conversation_id}")
    finally:
        _cleanup_stream(conversation_id)

    return response


async def handle_list_conversations(request: web.Request) -> web.Response:
    """GET /api/conversations — List past web conversations."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    web_conversations: WebConversationStore = request.app["web_conversations"]
    conversations = await web_conversations.list_conversations()
    return _json(conversations)


async def handle_get_conversation(request: web.Request) -> web.Response:
    """GET /api/conversations/{id} — Get messages for a conversation."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    conversation_id = request.match_info["id"]
    web_conversations: WebConversationStore = request.app["web_conversations"]

    if not await web_conversations.conversation_exists(conversation_id):
        return _error("conversation not found", status=404)

    messages = await web_conversations.get_messages(conversation_id)
    return _json(messages)


async def handle_list_projects(request: web.Request) -> web.Response:
    """GET /api/projects — List all 12 solar projects."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    projects = []
    for key, info in PROJECTS.items():
        folder_path = PROJECTS_DIR / key
        exists = folder_path.is_dir()
        file_count = 0
        if exists:
            file_count = sum(1 for _ in folder_path.rglob("*") if _.is_file())

        projects.append({
            "key": key,
            "name": info["name"],
            "number": info["number"],
            "folder_path": str(folder_path),
            "folder_exists": exists,
            "file_count": file_count,
        })

    # Sort by project number
    projects.sort(key=lambda p: p["number"])
    return _json(projects)


async def handle_get_project(request: web.Request) -> web.Response:
    """GET /api/projects/{key} — Get detailed project info."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    key = request.match_info["key"]
    if key not in PROJECTS:
        return _error("project not found", status=404)

    info = PROJECTS[key]
    folder_path = PROJECTS_DIR / key
    exists = folder_path.is_dir()

    # Gather subfolder info
    subfolders = []
    if exists:
        for sf_name in PROJECT_SUBFOLDERS:
            sf_path = folder_path / sf_name
            sf_exists = sf_path.is_dir()
            sf_files = 0
            if sf_exists:
                sf_files = sum(1 for _ in sf_path.rglob("*") if _.is_file())
            subfolders.append({
                "name": sf_name,
                "path": str(sf_path),
                "exists": sf_exists,
                "file_count": sf_files,
            })

    # Recent files (last 20 modified files in this project)
    recent_files = []
    if exists:
        all_files = sorted(
            (f for f in folder_path.rglob("*") if f.is_file() and not f.name.startswith(".")),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for f in all_files[:20]:
            try:
                stat = f.stat()
                recent_files.append({
                    "name": f.name,
                    "path": str(f),
                    "relative_path": str(f.relative_to(folder_path)),
                    "size": stat.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(stat.st_mtime)),
                })
            except OSError:
                continue

    # Constraint count from memory store
    constraint_count = 0
    memory_store = request.app.get("memory")
    if memory_store:
        try:
            result = await memory_store.search("", project_key=key, category="constraint")
            constraint_count = len(result)
        except Exception:
            logger.debug(f"Failed to count constraints for project {key}", exc_info=True)

    return _json({
        "key": key,
        "name": info["name"],
        "number": info["number"],
        "folder_path": str(folder_path),
        "folder_exists": exists,
        "subfolders": subfolders,
        "recent_files": recent_files,
        "constraint_count": constraint_count,
    })


async def handle_list_action_items(request: web.Request) -> web.Response:
    """GET /api/action-items — List open action items."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    memory_store = request.app.get("memory")
    if not memory_store:
        return _error("memory store not initialized", status=503)

    project_filter = request.query.get("project")

    try:
        result = await memory_store.get_action_items(resolved=False)
        if not result.success:
            return _error(f"failed to fetch action items: {result.error}", status=500)

        items = []
        for m in result.memories:
            # Apply project filter if specified
            if project_filter and m.project_key != project_filter:
                continue
            items.append({
                "id": m.id,
                "date": m.created_at,
                "summary": m.summary,
                "detail": m.detail,
                "project": m.project_key,
                "tags": m.tags,
                "source": m.source,
            })

        return _json(items)
    except Exception as e:
        logger.exception("Failed to list action items")
        return _error(f"internal error: {str(e)[:200]}", status=500)


async def handle_resolve_action_item(request: web.Request) -> web.Response:
    """POST /api/action-items/{id}/resolve — Resolve an action item."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    memory_store = request.app.get("memory")
    if not memory_store:
        return _error("memory store not initialized", status=503)

    try:
        item_id = int(request.match_info["id"])
    except (ValueError, KeyError):
        return _error("invalid action item id")

    try:
        await memory_store.resolve_action_item(item_id)
        return _json({"success": True})
    except Exception as e:
        logger.exception(f"Failed to resolve action item {item_id}")
        return _error(f"failed to resolve: {str(e)[:200]}", status=500)


async def handle_list_agents(request: web.Request) -> web.Response:
    """GET /api/agents — List available agents with descriptions."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    agents = []
    for key, agent_def in ALL_AGENTS.items():
        agents.append({
            "name": agent_def.name,
            "display_name": agent_def.display_name,
            "description": agent_def.description,
            "is_subagent": key != "nimrod",
            "can_write_files": agent_def.can_write_files,
        })

    return _json(agents)


async def handle_search_memories(request: web.Request) -> web.Response:
    """GET /api/memories/search — Search memories via FTS5."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    memory_store = request.app.get("memory")
    if not memory_store:
        return _error("memory store not initialized", status=503)

    q = request.query.get("q", "").strip()
    category = request.query.get("category")
    project = request.query.get("project")
    limit = min(int(request.query.get("limit", "50")), 200)

    if not q and not category and not project:
        return _error("at least one of q, category, or project is required")

    try:
        result = await memory_store.search(
            query=q,
            limit=limit,
            project_key=project,
            category=category,
        )

        if not result.success:
            return _json({
                "results": [],
                "error": result.error,
                "degraded": result.degraded,
            })

        memories = []
        for m in result.memories:
            memories.append({
                "id": m.id,
                "created_at": m.created_at,
                "category": m.category,
                "project_key": m.project_key,
                "summary": m.summary,
                "detail": m.detail,
                "source": m.source,
                "tags": m.tags,
                "resolved": bool(m.resolved),
            })

        return _json({
            "results": memories,
            "count": len(memories),
            "degraded": result.degraded,
        })
    except Exception as e:
        logger.exception("Memory search failed")
        return _error(f"search error: {str(e)[:200]}", status=500)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def setup_web_routes(app: web.Application) -> None:
    """Register all /api/* routes on the given aiohttp Application.

    Call this during server startup after the app has been created and
    memory/web_conversations stores have been attached to app[...].
    """
    # CORS preflight handler for all /api paths
    app.router.add_route("OPTIONS", "/api/{tail:.*}", handle_options)

    # Health
    app.router.add_get("/api/health", handle_api_health)

    # Chat
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/api/chat/stream/{conversation_id}", handle_chat_stream)

    # Conversations
    app.router.add_get("/api/conversations", handle_list_conversations)
    app.router.add_get("/api/conversations/{id}", handle_get_conversation)

    # Projects
    app.router.add_get("/api/projects", handle_list_projects)
    app.router.add_get("/api/projects/{key}", handle_get_project)

    # Action items
    app.router.add_get("/api/action-items", handle_list_action_items)
    app.router.add_post("/api/action-items/{id}/resolve", handle_resolve_action_item)

    # Agents
    app.router.add_get("/api/agents", handle_list_agents)

    # Memory search
    app.router.add_get("/api/memories/search", handle_search_memories)

    # --- Static file serving for frontend (SPA) ---
    _setup_static_serving(app)

    logger.info(
        "Web API routes registered: /api/health, /api/chat, /api/chat/stream, "
        "/api/conversations, /api/projects, /api/action-items, /api/agents, "
        "/api/memories/search + static frontend serving"
    )


# ---------------------------------------------------------------------------
# Static file serving for the React frontend (SPA)
# ---------------------------------------------------------------------------
# Serves the built frontend from /opt/goliath/web-platform/frontend/dist/
# No Nginx needed — aiohttp handles everything.

_FRONTEND_DIST = REPO_ROOT / "web-platform" / "frontend" / "dist"

# MIME types for common static assets
_MIME_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".json": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".map": "application/json",
}


async def _handle_static_file(request: web.Request) -> web.Response:
    """Serve a static file from the frontend dist directory."""
    # Get the path from the request
    rel_path = request.match_info.get("path", "")

    # Security: prevent directory traversal
    if ".." in rel_path:
        return web.Response(status=403, text="Forbidden")

    file_path = _FRONTEND_DIST / rel_path

    if file_path.is_file():
        suffix = file_path.suffix.lower()
        content_type = _MIME_TYPES.get(suffix, "application/octet-stream")

        content = file_path.read_bytes()

        headers = {}
        # Cache assets aggressively (they have content hashes in filenames)
        if rel_path.startswith("assets/"):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            headers["Cache-Control"] = "no-cache"

        return web.Response(body=content, content_type=content_type, headers=headers)

    # Not found — return index.html for SPA client-side routing
    return await _handle_spa_fallback(request)


async def _handle_spa_fallback(request: web.Request) -> web.Response:
    """Serve index.html for SPA client-side routing (React Router)."""
    index_path = _FRONTEND_DIST / "index.html"
    if not index_path.is_file():
        return web.Response(
            status=503,
            text="Frontend not built. Run: cd /opt/goliath/web-platform/frontend && npm run build",
        )

    content = index_path.read_bytes()
    return web.Response(
        body=content,
        content_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


async def _handle_root(request: web.Request) -> web.Response:
    """Serve index.html at the root path /."""
    return await _handle_spa_fallback(request)


def _setup_static_serving(app: web.Application) -> None:
    """Register static file routes on the aiohttp app.

    These MUST be registered AFTER the /api/* and /webhook/* routes
    so those take priority. The catch-all route handles SPA navigation.
    """
    if not _FRONTEND_DIST.is_dir():
        logger.warning(
            f"Frontend dist directory not found at {_FRONTEND_DIST}. "
            "Static serving disabled. Build frontend: "
            "cd /opt/goliath/web-platform/frontend && npm run build"
        )
        return

    # Serve root path
    app.router.add_get("/", _handle_root)

    # Serve static files with exact paths (assets, favicon, etc.)
    app.router.add_get("/{path:.*}", _handle_static_file)

    logger.info(f"Static frontend serving enabled from {_FRONTEND_DIST}")
