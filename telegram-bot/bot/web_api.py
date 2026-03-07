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
import mimetypes
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Optional

import aiosqlite
from aiohttp import web

import aiohttp as _aiohttp_lib

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
# Telegram notification on agent completion
# ---------------------------------------------------------------------------
_TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_CHAT_ID = os.getenv("REPORT_CHAT_ID", "")


async def _notify_telegram(title: str, preview: str, is_error: bool = False) -> None:
    """Send a Telegram notification when a GUI agent task completes."""
    if not _TG_BOT_TOKEN or not _TG_CHAT_ID:
        return
    try:
        icon = "\u274c" if is_error else "\u2705"
        text = (
            f"{icon} <b>GUI Agent {'Error' if is_error else 'Complete'}</b>\n\n"
            f"<b>Chat:</b> {title}\n"
            f"<b>Preview:</b> <code>{preview[:200]}</code>"
        )
        async with _aiohttp_lib.ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{_TG_BOT_TOKEN}/sendMessage",
                json={"chat_id": _TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=_aiohttp_lib.ClientTimeout(total=10),
            )
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")

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

    # Fetch conversation title for Telegram notification
    _conv_title = user_message[:60]
    try:
        cursor = await web_conversations._db.execute(
            "SELECT title FROM web_conversations WHERE id = ?", (conversation_id,)
        )
        row = await cursor.fetchone()
        if row and row[0]:
            _conv_title = row[0]
    except Exception:
        pass

    try:
        # Signal that processing has started
        await queue.put({"type": "thinking", "data": "Nimrod is processing your message..."})

        # Build conversation history from web store
        conv_history = await web_conversations.get_history_for_prompt(conversation_id)

        # Create orchestrator (same brain as Telegram)
        orchestrator = NimrodOrchestrator(memory=memory_store, token_tracker=token_tracker, chat_id=conversation_id)

        # Live agent event callback — streams events to SSE as they happen
        async def agent_callback(event):
            await queue.put({"type": "subagent", "data": json.dumps(event)})

        # Live tool event callback — streams tool_start/tool_done to SSE
        async def tool_callback(event):
            await queue.put({"type": "agent_tool", "data": json.dumps(event)})

        # Run the orchestration pipeline (SDK doesn't support token-level
        # streaming, so we stream the final clean text word-by-word below).
        result = await asyncio.wait_for(
            orchestrator.handle_message(
                user_message, conv_history,
                on_agent_event=agent_callback,
                on_tool_event=tool_callback,
                source="web",
            ),
            timeout=7200,  # Same 2-hour zombie safety net as Telegram
        )

        # Collect subagent log for metadata (still useful for message storage)
        subagent_log = getattr(orchestrator, "_subagent_log", None) or []

        response_text = result.text

        # Stream the clean response word-by-word via SSE chunks.
        # The SDK yields complete text blocks (no token streaming), so we
        # add real delays between words for a ChatGPT-like drip effect.
        # ~30ms per word ≈ 33 words/sec. TCP needs real time gaps to send
        # separate packets; asyncio.sleep(0) is not enough.
        words = re.split(r'(?<=\s)', response_text)
        for word in words:
            if word:
                await queue.put({"type": "chunk", "data": word})
                await asyncio.sleep(0.03)

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
                "token_summary": result.token_summary,
            }),
        })

        # Notify via Telegram
        await _notify_telegram(_conv_title, response_text[:200])

    except asyncio.TimeoutError:
        logger.error(f"Web orchestration timed out for conversation {conversation_id}")
        await queue.put({
            "type": "error",
            "data": "Orchestration timed out after 2 hours.",
        })
        await _notify_telegram(_conv_title, "Orchestration timed out after 2 hours", is_error=True)
    except Exception as e:
        logger.exception(f"Web orchestration failed for conversation {conversation_id}")
        await queue.put({
            "type": "error",
            "data": f"Orchestration error: {str(e)[:500]}",
        })
        await _notify_telegram(_conv_title, f"Error: {str(e)[:200]}", is_error=True)
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
                event = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                # Send keepalive comment
                await response.write(b": keepalive\n\n")
                continue

            if event["type"] == "_done":
                break

            sse_data = json.dumps({"type": event["type"], "data": event["data"]})
            await response.write(f"data: {sse_data}\n\n".encode("utf-8"))
            await response.drain()

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
# File Browser Endpoints
# ---------------------------------------------------------------------------

_FILES_ROOT = Path("/opt/goliath/")

_BLACKLIST_PATTERNS = [".env", ".secrets", ".git/objects", "node_modules", "venv", "__pycache__"]


def _is_safe_path(path: Path) -> bool:
    """Validate that path is within root and not blacklisted."""
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return False
    # Must be within root
    if not str(resolved).startswith(str(_FILES_ROOT.resolve())):
        return False
    # Reject blacklisted patterns
    rel = str(resolved.relative_to(_FILES_ROOT.resolve()))
    for pattern in _BLACKLIST_PATTERNS:
        if pattern in rel.split(os.sep) or rel.endswith(pattern):
            return False
    return True


def _format_size(size: int) -> str:
    """Format bytes into human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"


async def handle_list_files(request: web.Request) -> web.Response:
    """GET /api/files?path= — List directory contents."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    rel_path = request.query.get("path", "")
    if ".." in rel_path:
        return _error("path traversal not allowed", status=403)

    target = _FILES_ROOT / rel_path if rel_path else _FILES_ROOT
    if not _is_safe_path(target):
        return _error("access denied", status=403)
    if not target.is_dir():
        return _error("not a directory", status=404)

    items = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if not _is_safe_path(entry):
                continue
            try:
                st = entry.stat()
                is_dir = entry.is_dir()
                size = 0 if is_dir else st.st_size
                items.append({
                    "name": entry.name,
                    "type": "directory" if is_dir else "file",
                    "size": size,
                    "sizeFormatted": _format_size(size),
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.st_mtime)),
                    "extension": entry.suffix.lstrip(".") if not is_dir else "",
                    "path": str(entry.relative_to(_FILES_ROOT)),
                })
            except OSError:
                continue
    except PermissionError:
        return _error("permission denied", status=403)

    return _json(items)


async def handle_download_file(request: web.Request) -> web.Response:
    """GET /api/files/download?path= — Download a file."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    rel_path = request.query.get("path", "")
    if not rel_path or ".." in rel_path:
        return _error("invalid path", status=400)

    target = _FILES_ROOT / rel_path
    if not _is_safe_path(target):
        return _error("access denied", status=403)
    if not target.is_file():
        return _error("file not found", status=404)

    content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return web.FileResponse(
        target,
        headers={
            **_cors_headers(),
            "Content-Disposition": f'attachment; filename="{target.name}"',
            "Content-Type": content_type,
        },
    )


async def handle_upload_files(request: web.Request) -> web.Response:
    """POST /api/files/upload — Upload files (multipart)."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    reader = await request.multipart()
    target_path = ""
    uploaded = []

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "path":
            target_path = (await part.text()).strip()
            if ".." in target_path:
                return _error("path traversal not allowed", status=403)
        elif part.name == "files":
            if not target_path and not uploaded:
                target_path = ""
            dest_dir = _FILES_ROOT / target_path if target_path else _FILES_ROOT
            if not _is_safe_path(dest_dir):
                return _error("access denied", status=403)
            dest_dir.mkdir(parents=True, exist_ok=True)

            filename = part.filename or "unnamed"
            dest_file = dest_dir / filename
            if not _is_safe_path(dest_file):
                continue

            size = 0
            with open(dest_file, "wb") as f:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)

            uploaded.append({
                "name": filename,
                "size": size,
                "sizeFormatted": _format_size(size),
                "path": str(dest_file.relative_to(_FILES_ROOT)),
            })

    return _json({"uploaded": uploaded})


async def handle_upload_folder(request: web.Request) -> web.Response:
    """POST /api/files/upload-folder — Upload folder (multipart with relative paths)."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    reader = await request.multipart()
    target_path = ""
    uploaded = []

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "path":
            target_path = (await part.text()).strip()
            if ".." in target_path:
                return _error("path traversal not allowed", status=403)
        elif part.name == "files":
            # The relative path within the folder is sent as the filename header
            relative_name = part.filename or "unnamed"
            if ".." in relative_name:
                continue

            dest_dir = _FILES_ROOT / target_path if target_path else _FILES_ROOT
            dest_file = dest_dir / relative_name
            if not _is_safe_path(dest_file):
                continue

            dest_file.parent.mkdir(parents=True, exist_ok=True)

            size = 0
            with open(dest_file, "wb") as f:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)

            uploaded.append({
                "name": relative_name,
                "size": size,
                "sizeFormatted": _format_size(size),
                "path": str(dest_file.relative_to(_FILES_ROOT)),
            })

    return _json({"uploaded": uploaded})


async def handle_mkdir(request: web.Request) -> web.Response:
    """POST /api/files/mkdir — Create directory."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    try:
        data = await request.json()
    except Exception:
        return _error("invalid JSON body")

    dir_path = data.get("path", "").strip()
    if not dir_path or ".." in dir_path:
        return _error("invalid path", status=400)

    target = _FILES_ROOT / dir_path
    if not _is_safe_path(target):
        return _error("access denied", status=403)

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _error(f"failed to create directory: {e}", status=500)

    return _json({"success": True, "path": str(target.relative_to(_FILES_ROOT))})


async def handle_serve_file(request: web.Request) -> web.Response:
    """GET /api/files/serve?path= — Serve file inline with correct MIME type.

    Used by the FilePreviewDrawer for PDF (iframe), Excel (fetch + xlsx parse),
    and image previews. Sets Content-Disposition: inline so browsers render
    the file rather than downloading it.
    """
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    rel_path = request.query.get("path", "")
    if not rel_path or ".." in rel_path:
        return _error("invalid path", status=400)

    target = _FILES_ROOT / rel_path
    if not _is_safe_path(target):
        return _error("access denied", status=403)
    if not target.is_file():
        return _error("file not found", status=404)

    content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return web.FileResponse(
        target,
        headers={
            **_cors_headers(),
            "Content-Disposition": f'inline; filename="{target.name}"',
            "Content-Type": content_type,
        },
    )


async def handle_preview_file(request: web.Request) -> web.Response:
    """GET /api/files/preview?path= — Preview text file content (first 100KB)."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    rel_path = request.query.get("path", "")
    if not rel_path or ".." in rel_path:
        return _error("invalid path", status=400)

    target = _FILES_ROOT / rel_path
    if not _is_safe_path(target):
        return _error("access denied", status=403)
    if not target.is_file():
        return _error("file not found", status=404)

    # Check file size — refuse to preview very large files
    try:
        file_size = target.stat().st_size
    except OSError:
        return _error("cannot read file", status=500)

    max_preview = 100 * 1024  # 100KB
    truncated = file_size > max_preview

    try:
        with open(target, "r", errors="replace") as f:
            content = f.read(max_preview)
    except (OSError, UnicodeDecodeError):
        return _error("cannot read file as text", status=400)

    return _json({
        "content": content,
        "size": file_size,
        "truncated": truncated,
        "name": target.name,
        "path": rel_path,
    })


# ---------------------------------------------------------------------------
# Production Trends — POD file activity dashboard
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, date as _date_type
from zoneinfo import ZoneInfo

_CT = ZoneInfo("America/Chicago")

# Cache: {"data": ..., "expires": monotonic_time}
_production_cache: dict[str, object] = {}
_PRODUCTION_CACHE_TTL = 60  # seconds

# Track latest mtime across all pod dirs for cache-busting
_pod_last_mtime: float = 0.0


def _scan_pod_files(project_key: str) -> list[dict]:
    """Scan a project's pod/ dir and return [{date, filename, size, mtime}, ...]."""
    pod_dir = PROJECTS_DIR / project_key / "pod"
    if not pod_dir.is_dir():
        return []

    results = []
    for entry in pod_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        # Skip non-PDF files and hidden files
        if not name.lower().endswith(".pdf") or name.startswith("."):
            continue
        # Parse date from filename prefix: YYYY-MM-DD_...
        match = re.match(r"^(\d{4}-\d{2}-\d{2})_", name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            st = entry.stat()
            results.append({
                "date": file_date.isoformat(),
                "filename": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        except (ValueError, OSError):
            continue

    results.sort(key=lambda r: r["date"], reverse=True)
    return results


def _get_pod_dirs_mtime() -> float:
    """Return the latest mtime across all project pod/ directories."""
    latest = 0.0
    for key in PROJECTS:
        pod_dir = PROJECTS_DIR / key / "pod"
        if pod_dir.is_dir():
            try:
                mt = pod_dir.stat().st_mtime
                if mt > latest:
                    latest = mt
            except OSError:
                pass
    return latest


def _scan_pod_corruption() -> dict:
    """Scan all POD PDFs for corruption and return health summary.

    Returns a dict with corruption diagnostics for the frontend to display
    meaningful error messages instead of just showing empty/null data.
    """
    total_pdfs = 0
    clean_count = 0
    corrupted_count = 0
    null_prefix_count = 0
    projects_affected = set()

    for key in PROJECTS:
        pod_dir = PROJECTS_DIR / key / "pod"
        if not pod_dir.is_dir():
            continue
        for pdf in pod_dir.glob("*.pdf"):
            total_pdfs += 1
            try:
                header = pdf.read_bytes()[:20]
                has_null = header[:4] == b"null"
                repl_count = header.count(b"\xef\xbf\xbd")

                if has_null:
                    null_prefix_count += 1
                    projects_affected.add(key)
                elif repl_count > 2:
                    corrupted_count += 1
                    projects_affected.add(key)
                else:
                    clean_count += 1
            except Exception:
                corrupted_count += 1

    # Also check extraction log
    extraction_failures = 0
    if _POD_DB_PATH.exists():
        import sqlite3 as _sq3
        try:
            _conn = _sq3.connect(str(_POD_DB_PATH))
            row = _conn.execute(
                "SELECT COUNT(*) FROM pod_extraction_log WHERE status IN ('failed', 'corrupted')"
            ).fetchone()
            extraction_failures = row[0] if row else 0
            _conn.close()
        except Exception:
            pass

    is_healthy = clean_count > 0 or extraction_failures == 0
    issue = None
    if corrupted_count + null_prefix_count > 0 and clean_count == 0:
        issue = (
            f"All {total_pdfs} POD PDFs have UTF-8 corruption from the Power Automate "
            f"email relay. Binary attachment data was irreversibly mangled during "
            f"forwarding. Fix: update the PA flow to use contentBytes for attachments "
            f"instead of text encoding, or re-download clean PDFs from Office 365."
        )

    return {
        "healthy": is_healthy,
        "total_pdfs": total_pdfs,
        "clean": clean_count,
        "corrupted": corrupted_count + null_prefix_count,
        "extraction_failures": extraction_failures,
        "projects_affected": sorted(projects_affected),
        "issue": issue,
    }


def _build_production_trends() -> dict:
    """Build the full production trends response."""
    today = datetime.now(_CT).date()
    window_start = today - timedelta(days=6)  # 7-day window including today

    date_range = []
    for i in range(7):
        d = window_start + timedelta(days=i)
        date_range.append(d.isoformat())

    projects_data = []

    for key, info in sorted(PROJECTS.items(), key=lambda x: x[1]["number"]):
        pod_files = _scan_pod_files(key)

        # Build a date -> file count map
        date_counts: dict[str, int] = {}
        all_time_count = len(pod_files)
        for pf in pod_files:
            d = pf["date"]
            date_counts[d] = date_counts.get(d, 0) + 1

        # Daily data for the 7-day window
        daily = []
        for d in date_range:
            daily.append({
                "date": d,
                "count": date_counts.get(d, 0),
            })

        # Today and yesterday
        today_str = today.isoformat()
        yesterday_str = (today - timedelta(days=1)).isoformat()
        today_count = date_counts.get(today_str, 0)
        yesterday_count = date_counts.get(yesterday_str, 0)

        # Delta calculation
        delta_units = today_count - yesterday_count
        if yesterday_count > 0:
            delta_pct = round((delta_units / yesterday_count) * 100, 1)
        elif today_count > 0:
            delta_pct = 100.0
        else:
            delta_pct = 0.0

        # 7-day total
        seven_day_total = sum(d["count"] for d in daily)

        # Trend direction: compare last 3 days avg vs first 4 days avg
        if seven_day_total == 0:
            trend = "none"
        else:
            first_half = sum(d["count"] for d in daily[:4])
            second_half = sum(d["count"] for d in daily[4:])
            if second_half > first_half:
                trend = "up"
            elif second_half < first_half:
                trend = "down"
            else:
                trend = "flat"

        # Latest POD date
        latest_pod = pod_files[0]["date"] if pod_files else None

        # Days since last POD
        if latest_pod:
            days_since = (today - datetime.strptime(latest_pod, "%Y-%m-%d").date()).days
        else:
            days_since = None

        projects_data.append({
            "key": key,
            "name": info["name"],
            "number": info["number"],
            "today": today_count,
            "yesterday": yesterday_count,
            "delta_units": delta_units,
            "delta_pct": delta_pct,
            "seven_day_total": seven_day_total,
            "all_time_total": all_time_count,
            "trend": trend,
            "latest_pod_date": latest_pod,
            "days_since_last_pod": days_since,
            "daily": daily,
        })

    # Portfolio-wide summary
    portfolio_today = sum(p["today"] for p in projects_data)
    portfolio_yesterday = sum(p["yesterday"] for p in projects_data)
    portfolio_7d = sum(p["seven_day_total"] for p in projects_data)
    projects_reporting_today = sum(1 for p in projects_data if p["today"] > 0)
    projects_with_data = sum(1 for p in projects_data if p["all_time_total"] > 0)

    # Aggregate daily for chart (sum across all projects per date)
    portfolio_daily = []
    for i, d in enumerate(date_range):
        total = sum(p["daily"][i]["count"] for p in projects_data)
        portfolio_daily.append({"date": d, "count": total})

    return {
        "generated_at": _now_iso(),
        "date_range": {"start": date_range[0], "end": date_range[-1]},
        "portfolio": {
            "today": portfolio_today,
            "yesterday": portfolio_yesterday,
            "delta_units": portfolio_today - portfolio_yesterday,
            "seven_day_total": portfolio_7d,
            "projects_reporting_today": projects_reporting_today,
            "total_projects": len(PROJECTS),
            "projects_with_data": projects_with_data,
            "daily": portfolio_daily,
        },
        "projects": projects_data,
    }


async def handle_production_trends(request: web.Request) -> web.Response:
    """GET /api/production/trends — Production (POD) activity dashboard data."""
    global _pod_last_mtime

    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    try:
        now = time.monotonic()

        # Check if any pod directory has changed (cache bust)
        current_mtime = _get_pod_dirs_mtime()
        cache_valid = (
            "data" in _production_cache
            and _production_cache.get("expires", 0) > now
            and current_mtime <= _pod_last_mtime
        )

        if cache_valid:
            return _json(_production_cache["data"])

        # Rebuild
        data = _build_production_trends()
        _production_cache["data"] = data
        _production_cache["expires"] = now + _PRODUCTION_CACHE_TTL
        _pod_last_mtime = current_mtime

        return _json(data)
    except Exception as e:
        logger.exception("Failed to build production trends")
        return _error(f"internal error: {str(e)[:200]}", status=500)


# ---------------------------------------------------------------------------
# Production dashboard (extracted POD data from SQLite)
# ---------------------------------------------------------------------------
_POD_DB_PATH = REPO_ROOT / "web-platform" / "backend" / "data" / "pod_production.db"
_dashboard_cache: dict = {}
_DASHBOARD_CACHE_TTL = 60  # seconds


async def handle_production_dashboard(request: web.Request) -> web.Response:
    """GET /api/production/dashboard — Summary cards from POD extraction data."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    try:
        now = time.monotonic()

        if _dashboard_cache.get("expires", 0) > now and "data" in _dashboard_cache:
            return _json(_dashboard_cache["data"])

        empty_project = lambda k: {
            "key": k,
            "name": PROJECTS[k]["name"],
            "number": PROJECTS[k]["number"],
            "latest_date": None,
            "has_data": False,
            "activity_count": 0,
            "category_count": 0,
            "categories_summary": [],
            "overall_progress": None,
        }

        if not _POD_DB_PATH.exists():
            return _json({
                "generated_at": _now_iso(),
                "portfolio": {
                    "active_sites": 0,
                    "total_projects": len(PROJECTS),
                    "projects_with_data": 0,
                },
                "projects": [
                    empty_project(k)
                    for k in sorted(PROJECTS, key=lambda x: PROJECTS[x]["number"])
                ],
            })

        import sqlite3
        from datetime import datetime
        from zoneinfo import ZoneInfo

        ct = ZoneInfo("America/Chicago")
        today_str = datetime.now(ct).strftime("%Y-%m-%d")

        conn = sqlite3.connect(str(_POD_DB_PATH))
        conn.row_factory = sqlite3.Row

        # Get latest date per project and all activities for that date
        latest_dates = conn.execute(
            "SELECT project_key, MAX(report_date) as latest FROM pod_production GROUP BY project_key"
        ).fetchall()
        latest_map = {r["project_key"]: r["latest"] for r in latest_dates}

        projects_out = []
        active_sites = 0
        projects_with_data = 0

        for k in sorted(PROJECTS, key=lambda x: PROJECTS[x]["number"]):
            info = PROJECTS[k]
            latest = latest_map.get(k)

            if not latest:
                projects_out.append(empty_project(k))
                continue

            projects_with_data += 1

            if latest == today_str:
                active_sites += 1

            rows = conn.execute(
                """SELECT activity_category, activity_name, pct_complete
                   FROM pod_production WHERE project_key = ? AND report_date = ?""",
                (k, latest),
            ).fetchall()

            # Group by category
            cat_map: dict[str, list] = {}
            for r in rows:
                cat = r["activity_category"] or "General"
                cat_map.setdefault(cat, []).append(r["pct_complete"])

            categories_summary = []
            all_pcts = []
            for cat, pcts in sorted(cat_map.items()):
                valid_pcts = [p for p in pcts if p is not None]
                avg = round(sum(valid_pcts) / len(valid_pcts), 1) if valid_pcts else None
                categories_summary.append({
                    "category": cat,
                    "activity_count": len(pcts),
                    "avg_pct_complete": avg,
                })
                all_pcts.extend(valid_pcts)

            overall = round(sum(all_pcts) / len(all_pcts), 1) if all_pcts else None

            projects_out.append({
                "key": k,
                "name": info["name"],
                "number": info["number"],
                "latest_date": latest,
                "has_data": True,
                "activity_count": len(rows),
                "category_count": len(cat_map),
                "categories_summary": categories_summary,
                "overall_progress": overall,
            })

        conn.close()

        data = {
            "generated_at": _now_iso(),
            "portfolio": {
                "active_sites": active_sites,
                "total_projects": len(PROJECTS),
                "projects_with_data": projects_with_data,
            },
            "projects": projects_out,
        }

        _dashboard_cache["data"] = data
        _dashboard_cache["expires"] = now + _DASHBOARD_CACHE_TTL
        return _json(data)

    except Exception as e:
        logger.exception("Failed to build production dashboard")
        return _error(f"internal error: {str(e)[:200]}", status=500)


async def handle_production_project_detail(request: web.Request) -> web.Response:
    """GET /api/production/dashboard/{project_key} — Full activity detail for a project."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    project_key = request.match_info["project_key"]
    if project_key not in PROJECTS:
        return _error(f"Unknown project: {project_key}", status=404)

    try:
        info = PROJECTS[project_key]

        if not _POD_DB_PATH.exists():
            return _json({
                "key": project_key,
                "name": info["name"],
                "number": info["number"],
                "latest_date": None,
                "categories": [],
            })

        import sqlite3
        conn = sqlite3.connect(str(_POD_DB_PATH))
        conn.row_factory = sqlite3.Row

        # Get latest date for this project
        row = conn.execute(
            "SELECT MAX(report_date) as latest FROM pod_production WHERE project_key = ?",
            (project_key,),
        ).fetchone()
        latest = row["latest"] if row else None

        if not latest:
            conn.close()
            return _json({
                "key": project_key,
                "name": info["name"],
                "number": info["number"],
                "latest_date": None,
                "categories": [],
            })

        rows = conn.execute(
            """SELECT activity_category, activity_name, qty_to_date, qty_last_workday,
                      qty_completed_yesterday, total_qty, unit, pct_complete,
                      today_location, notes
               FROM pod_production WHERE project_key = ? AND report_date = ?
               ORDER BY activity_category, activity_name""",
            (project_key, latest),
        ).fetchall()
        conn.close()

        # Group by category
        cat_map: dict[str, list] = {}
        for r in rows:
            cat = r["activity_category"] or "General"
            # qty_completed_yesterday: blank/empty = 0 (not null)
            raw_yest = r["qty_completed_yesterday"]
            qty_yest = 0 if raw_yest is None else raw_yest
            cat_map.setdefault(cat, []).append({
                "activity_name": r["activity_name"],
                "qty_to_date": r["qty_to_date"],
                "qty_last_workday": r["qty_last_workday"],
                "qty_completed_yesterday": qty_yest,
                "total_qty": r["total_qty"],
                "unit": r["unit"],
                "pct_complete": r["pct_complete"],
                "today_location": r["today_location"],
                "notes": r["notes"],
            })

        categories = [
            {"category": cat, "activities": acts}
            for cat, acts in sorted(cat_map.items())
        ]

        return _json({
            "key": project_key,
            "name": info["name"],
            "number": info["number"],
            "latest_date": latest,
            "categories": categories,
        })

    except Exception as e:
        logger.exception("Failed to build project production detail")
        return _error(f"internal error: {str(e)[:200]}", status=500)


async def handle_extraction_status(request: web.Request) -> web.Response:
    """GET /api/production/extraction-status — Extraction pipeline health."""
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    try:
        if not _POD_DB_PATH.exists():
            return _json({
                "generated_at": _now_iso(),
                "summary": {
                    "total_files_processed": 0,
                    "success": 0, "failed": 0, "corrupted": 0,
                    "total_activities_extracted": 0,
                    "last_successful_extraction": None,
                },
                "recent_extractions": [],
            })

        import sqlite3

        conn = sqlite3.connect(str(_POD_DB_PATH))
        conn.row_factory = sqlite3.Row

        totals = conn.execute(
            "SELECT status, COUNT(*) as count FROM pod_extraction_log GROUP BY status"
        ).fetchall()

        recent = conn.execute(
            """SELECT source_file, project_key, report_date, status, error_message, extracted_at
               FROM pod_extraction_log ORDER BY extracted_at DESC LIMIT 20"""
        ).fetchall()

        last_row = conn.execute(
            "SELECT MAX(extracted_at) as last FROM pod_extraction_log WHERE status = 'success'"
        ).fetchone()

        act_count = conn.execute("SELECT COUNT(*) as count FROM pod_production").fetchone()

        conn.close()

        status_map = {r["status"]: r["count"] for r in totals}

        return _json({
            "generated_at": _now_iso(),
            "summary": {
                "total_files_processed": sum(r["count"] for r in totals),
                "success": status_map.get("success", 0),
                "failed": status_map.get("failed", 0),
                "corrupted": status_map.get("corrupted", 0),
                "total_activities_extracted": act_count["count"] if act_count else 0,
                "last_successful_extraction": last_row["last"] if last_row else None,
            },
            "recent_extractions": [dict(r) for r in recent],
        })
    except Exception as e:
        logger.exception("Failed to get extraction status")
        return _error(f"internal error: {str(e)[:200]}", status=500)


def _now_iso() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Chicago")).isoformat()


# ---------------------------------------------------------------------------
# POD extraction trigger
# ---------------------------------------------------------------------------
_extraction_lock = asyncio.Lock()


async def handle_run_extraction(request: web.Request) -> web.Response:
    """POST /api/production/extract — Run POD extraction on unprocessed PDFs.

    Returns immediately with status, runs extraction in background.
    Only one extraction can run at a time.
    """
    if not _check_web_auth(request):
        return _error("unauthorized", status=401)

    if _extraction_lock.locked():
        return _json({"status": "already_running", "message": "Extraction is already in progress"})

    async def _run():
        async with _extraction_lock:
            import subprocess
            import sys
            script = REPO_ROOT / "scripts" / "extract_pod_data.py"
            if not script.exists():
                logger.error(f"Extraction script not found: {script}")
                return

            try:
                env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
                result = subprocess.run(
                    [sys.executable, str(script)],
                    capture_output=True, text=True, timeout=600, env=env,
                    cwd=str(REPO_ROOT),
                )
                if result.stdout:
                    for line in result.stdout.strip().split("\n")[-15:]:
                        logger.info(f"[pod-extract] {line}")
                if result.returncode != 0 and result.stderr:
                    logger.warning(f"[pod-extract] stderr: {result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                logger.warning("[pod-extract] timed out (600s)")
            except Exception as e:
                logger.exception(f"[pod-extract] failed: {e}")

            # Bust dashboard cache so next request gets fresh data
            _dashboard_cache.clear()

    asyncio.ensure_future(_run())

    return _json({"status": "started", "message": "Extraction started in background"})


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

    # Files
    app.router.add_get("/api/files", handle_list_files)
    app.router.add_get("/api/files/download", handle_download_file)
    app.router.add_post("/api/files/upload", handle_upload_files)
    app.router.add_post("/api/files/upload-folder", handle_upload_folder)
    app.router.add_post("/api/files/mkdir", handle_mkdir)
    app.router.add_get("/api/files/preview", handle_preview_file)
    app.router.add_get("/api/files/serve", handle_serve_file)

    # Production trends (POD activity dashboard)
    app.router.add_get("/api/production/trends", handle_production_trends)
    app.router.add_get("/api/production/dashboard", handle_production_dashboard)
    app.router.add_get("/api/production/dashboard/{project_key}", handle_production_project_detail)
    app.router.add_get("/api/production/extraction-status", handle_extraction_status)
    app.router.add_post("/api/production/extract", handle_run_extraction)

    # --- Static file serving for frontend (SPA) ---
    _setup_static_serving(app)

    logger.info(
        "Web API routes registered: /api/health, /api/chat, /api/chat/stream, "
        "/api/conversations, /api/projects, /api/action-items, /api/agents, "
        "/api/memories/search, /api/files, /api/production/trends + static frontend serving"
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
