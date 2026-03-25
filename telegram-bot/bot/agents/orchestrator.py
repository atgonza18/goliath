import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import aiohttp

from bot.agents.definitions import NIMROD, CONSTRAINTS_MANAGER
from bot.agents.registry import AgentRegistry
from bot.agents.runner import get_runner, AgentResult
from bot.memory.store import MemoryStore
from bot.memory.reliability_log import classify_task_type
from bot.agents.tool_registry import get_tool_registry

# Pending constraint sync proposals are saved per-session (chat_id / conversation_id)
# so that two users running concurrently never cross-contaminate each other's sync state.
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Legacy path — kept only for backward-compatibility migration (see _pending_sync_path).
PENDING_SYNC_PATH = _DATA_DIR / "pending_constraint_sync.json"


def _pending_sync_path(chat_id) -> Path:
    """Return the per-session pending sync file path for the given chat_id.

    chat_id can be an int (Telegram) or a string (web conversation_id).
    A sanitised string form is used in the filename so it's safe for all OSes.
    Falls back to the legacy shared path when chat_id is None so that callers
    that haven't been updated yet continue to work correctly.
    """
    if chat_id is None:
        return PENDING_SYNC_PATH
    safe = re.sub(r"[^\w\-]", "_", str(chat_id))
    return _DATA_DIR / f"pending_constraint_sync_{safe}.json"

logger = logging.getLogger(__name__)


async def _send_calls_page_notification(
    chat_id, count: int, call_title: str, project_name: str
) -> None:
    """Fire a Telegram notification when constraints are pushed to the Calls page.

    Uses the Telegram Bot API directly via aiohttp to avoid threading the bot
    instance through the orchestrator.  Non-fatal — callers should catch exceptions.
    """
    from bot.config import TELEGRAM_BOT_TOKEN

    if not chat_id or not TELEGRAM_BOT_TOKEN:
        return

    s = "s" if count != 1 else ""
    text = (
        f"\U0001f4cb <b>{count}</b> constraint{s} from <b>{call_title}</b> "
        f"just hit the Calls page — {project_name}. "
        f"Review \u2192 push/deny when ready."
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        await session.post(
            url,
            json={"chat_id": int(chat_id), "text": text, "parse_mode": "HTML"},
            timeout=aiohttp.ClientTimeout(total=10),
        )


# Limit concurrent Claude subprocesses — bumped to 8 for parallel request handling.
# Multiple user messages can now run concurrently, each spawning subagents.
_semaphore = asyncio.Semaphore(8)

# ---------------------------------------------------------------------------
# Swarm state management — shared JSON file for cross-process visibility
# ---------------------------------------------------------------------------
_SWARM_STATE_PATH = _DATA_DIR / "swarm_state.json"


def _write_swarm_state(state: dict) -> None:
    """Write swarm state to shared JSON file (atomic via tmp+rename)."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _SWARM_STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.rename(_SWARM_STATE_PATH)
    except Exception:
        logger.debug("Failed to write swarm state file", exc_info=True)


def read_swarm_state() -> dict:
    """Read current swarm state. Returns idle state if file missing/corrupt."""
    try:
        if _SWARM_STATE_PATH.exists():
            return json.loads(_SWARM_STATE_PATH.read_text())
    except Exception:
        logger.debug("Failed to read swarm state file", exc_info=True)
    return {"status": "idle"}


async def _notify_telegram_swarm(message: str) -> None:
    """Fire-and-forget Telegram notification for swarm events."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("REPORT_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)):
                pass  # Fire and forget
    except Exception:
        logger.debug("Failed to send swarm Telegram notification", exc_info=True)


@dataclass
class SubagentRequest:
    agent_name: str
    task: str
    project_key: Optional[str] = None


@dataclass
class MemorySaveRequest:
    category: str
    summary: str
    detail: Optional[str] = None
    project_key: Optional[str] = None
    tags: Optional[str] = None


@dataclass
class OrchestrationResult:
    text: str
    file_paths: list[str]
    restart_required: bool = False
    restart_reason: str = ""
    token_summary: Optional[dict] = None


class NimrodOrchestrator:
    """
    Two-pass orchestration engine.

    Pass 1: Nimrod analyzes the user's message, decides to handle directly or
            dispatch subagents (via structured SUBAGENT_REQUEST blocks).
    Pass 2: If subagents were dispatched, Nimrod synthesizes their results.

    Memory is injected into prompts and saved from MEMORY_SAVE blocks.
    """

    def __init__(self, memory: MemoryStore, token_tracker=None, experience_replay=None, chat_id=None, reliability_log=None, tiered_memory=None):
        self.memory = memory
        self.registry = AgentRegistry()
        self.runner = get_runner()
        self._token_tracker = token_tracker
        self._experience_replay = experience_replay
        self._reliability_log = reliability_log  # ReliabilityLogStore or None
        self._tiered_memory = tiered_memory  # TieredMemoryStore or None (GAP H2)
        # Per-session identity — scopes pending constraint sync state so two users
        # cannot interfere with each other's approval/rejection flow.
        self._chat_id = chat_id  # int for Telegram, str for web, None for legacy callers
        # Activity log instrumentation (read by orchestration handler)
        self._pass1_duration: Optional[float] = None
        self._pass2_duration: Optional[float] = None
        self._subagent_log: Optional[list[dict]] = None
        # Token aggregation across the full orchestration run
        self._run_tokens: dict = {
            "total_input": 0,
            "total_output": 0,
            "total_cost": 0.0,
            "agents": [],
        }

    async def handle_message(self, user_message: str, conversation_history: str = "", on_agent_event: Optional[Callable] = None, on_text_chunk: Optional[Callable] = None, on_tool_event: Optional[Callable] = None, source: str = "telegram") -> OrchestrationResult:
        all_file_paths: list[str] = []

        # --- FAST PATH: Constraint sync approval/rejection ---
        sync_result = await self._check_constraint_sync_approval(user_message)
        if sync_result is not None:
            return sync_result

        # Gather memory context
        memory_context = await self._build_memory_context(user_message)

        # --- PASS 1: Nimrod routing ---
        pass1_prompt = await self._build_nimrod_prompt(user_message, memory_context, conversation_history, source=source)

        if on_agent_event:
            await on_agent_event({"type": "pass", "pass": 1, "status": "start"})

        # Pass 1: Nimrod has full tool access but is prompted to delegate complex work
        # Stream text chunks for Pass 1 only if it might be a direct answer
        p1_start = time.monotonic()
        async with _semaphore:
            nimrod_result = await self.runner.run(
                agent=NIMROD,
                task_prompt=pass1_prompt,
                no_tools=False,
                on_text_chunk=on_text_chunk,
            )
        self._pass1_duration = time.monotonic() - p1_start
        await self._track_agent_tokens(NIMROD.name)

        if on_agent_event:
            await on_agent_event({"type": "pass", "pass": 1, "status": "complete", "duration": round(self._pass1_duration, 1)})

        if not nimrod_result.success:
            return OrchestrationResult(
                text=f"Nimrod hit an error: {nimrod_result.error}",
                file_paths=[],
            )

        nimrod_text = nimrod_result.output

        # Parse structured blocks from Nimrod's response
        subagent_requests = self._parse_subagent_requests(nimrod_text)
        memory_saves = self._parse_memory_saves(nimrod_text)
        resolve_requests = self._parse_resolve_actions(nimrod_text)
        all_file_paths.extend(self._parse_file_created(nimrod_text))
        clean_response = self._strip_structured_blocks(nimrod_text)

        # Save any memories from Pass 1
        await self._process_memory_saves(memory_saves)
        # Resolve any action items from Pass 1
        await self._process_resolve_actions(resolve_requests)

        # If no subagent requests, return Nimrod's direct response
        if not subagent_requests:
            req, reason = self._parse_restart_required(nimrod_text)
            token_summary = self._build_token_summary()
            if token_summary:
                logger.info(
                    f"Orchestration token usage (direct): "
                    f"{token_summary['total_tokens']} tokens "
                    f"(${token_summary['total_cost_usd']:.4f})"
                )
            return OrchestrationResult(
                text=clean_response,
                file_paths=all_file_paths,
                restart_required=req,
                restart_reason=reason,
                token_summary=token_summary,
            )

        # --- DISPATCH SUBAGENTS ---
        agent_names = [r.agent_name for r in subagent_requests]
        is_swarm = len(subagent_requests) >= 2
        swarm_id = str(uuid.uuid4())[:8] if is_swarm else None

        logger.info(
            f"Nimrod dispatching {len(subagent_requests)} subagent(s): {agent_names}"
            + (f" [swarm:{swarm_id}]" if swarm_id else "")
        )

        # --- SWARM: emit start event + write state + Telegram notification ---
        if is_swarm:
            swarm_state = {
                "status": "active",
                "swarm_id": swarm_id,
                "agents": agent_names,
                "count": len(agent_names),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "completed_at": None,
                "duration_ms": None,
                "agent_status": {name: {"status": "running"} for name in agent_names},
            }
            _write_swarm_state(swarm_state)

            if on_agent_event:
                await on_agent_event({
                    "type": "swarm_started",
                    "swarm_id": swarm_id,
                    "agents": agent_names,
                    "count": len(agent_names),
                })

            # Telegram notification: swarm dispatched
            agent_list_str = ", ".join(agent_names)
            asyncio.create_task(_notify_telegram_swarm(
                f"⚡ <b>Swarm launched</b> — {len(agent_names)} agents running in parallel:\n"
                f"<code>{agent_list_str}</code>"
            ))

        swarm_start_t = time.monotonic()
        results = await self._run_subagents(subagent_requests, user_message, on_agent_event=on_agent_event, on_tool_event=on_tool_event)

        # --- SWARM: emit completion event + write state + Telegram notification ---
        if is_swarm:
            swarm_duration_ms = int((time.monotonic() - swarm_start_t) * 1000)
            succeeded = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)

            # Read the latest per-agent status (updated as each agent completed)
            latest_agent_status = read_swarm_state().get("agent_status", {})
            swarm_complete_state = {
                "status": "completed",
                "swarm_id": swarm_id,
                "agents": agent_names,
                "count": len(agent_names),
                "started_at": swarm_state["started_at"],
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "duration_ms": swarm_duration_ms,
                "succeeded": succeeded,
                "failed": failed,
                "agent_status": latest_agent_status,
            }
            _write_swarm_state(swarm_complete_state)

            if on_agent_event:
                await on_agent_event({
                    "type": "swarm_completed",
                    "swarm_id": swarm_id,
                    "duration_ms": swarm_duration_ms,
                    "succeeded": succeeded,
                    "failed": failed,
                })

            # Telegram notification: swarm complete
            status_emoji = "✅" if failed == 0 else "⚠️"
            asyncio.create_task(_notify_telegram_swarm(
                f"{status_emoji} <b>Swarm complete</b> — all {len(agent_names)} agents finished in "
                f"<b>{swarm_duration_ms / 1000:.1f}s</b>. Results synthesized."
                + (f"\n⚠️ <b>{failed}</b> agent(s) failed" if failed > 0 else "")
            ))

            # After 10 seconds, reset to idle so the indicator clears
            async def _reset_swarm_state():
                await asyncio.sleep(10)
                _write_swarm_state({"status": "idle"})

            asyncio.create_task(_reset_swarm_state())

        # Track token usage for each dispatched subagent
        for r in results:
            await self._track_agent_tokens(r.agent_name)

        # Record subagent results for activity log
        self._subagent_log = [
            {
                "agent": r.agent_name,
                "success": r.success,
                "duration": round(r.duration_seconds, 1),
                "error": r.error,
            }
            for r in results
        ]

        # Log partial failures so they're visible in the bot log
        failed = [r for r in results if not r.success]
        succeeded = [r for r in results if r.success]
        if failed:
            logger.warning(
                f"Partial subagent failure: {len(failed)}/{len(results)} failed — "
                f"{[f'{r.agent_name}: {r.error}' for r in failed]}"
            )

        # Collect FILE_CREATED and RESTART_REQUIRED from subagent outputs
        restart_required = False
        restart_reason = ""
        constraints_sync_data = None
        for r in results:
            if r.success and r.output:
                all_file_paths.extend(self._parse_file_created(r.output))
                req, reason = self._parse_restart_required(r.output)
                if req:
                    restart_required = True
                    restart_reason = reason
                # Check for CONSTRAINTS_SYNC from transcript_processor
                if r.agent_name == "transcript_processor":
                    constraints_sync_data = self._parse_constraints_sync(r.output)

        # --- CONSTRAINTS SYNC PROPOSAL: compare against ConstraintsPro, propose changes ---
        # Instead of auto-pushing, we generate a read-only proposal and save it
        # for the user to approve before anything gets written to ConstraintsPro.
        #
        # IMPORTANT: We await the proposal BEFORE Pass 2 synthesis so that the
        # dedup results are available for Nimrod to present an integrated, clean
        # summary. The user sees "here's what we'd push" with duplicates already
        # caught — no separate manual cross-reference step needed.
        sync_proposal_text = ""
        if constraints_sync_data:
            logger.info(
                f"Transcript contained {len(constraints_sync_data['constraints'])} "
                f"constraint(s) — running automatic ConstraintsPro cross-reference"
            )

            # Push extracted constraints to Convex pendingConstraintSyncs table
            # so they appear in the ConstraintsPro Calls review page.
            try:
                from bot.services.convex_client import push_pending_constraint_syncs
                import time as _time

                raw_constraints = constraints_sync_data["constraints"]
                project_name = constraints_sync_data.get("project", "Unknown")

                # Map transcript_processor's constraint format to Convex format
                convex_constraints = []
                for c in raw_constraints:
                    is_resolved = c.get("resolved", False)
                    proposed_action = "CLOSE" if is_resolved else "NEW"

                    # Use discipline directly from transcript extraction
                    # (the prompt now asks for specific ConstraintsPro disciplines)
                    VALID_DISCIPLINES = {
                        "Safety", "Quality", "Civil", "Modules",
                        "AG Electrical", "UG Electrical", "Piles",
                        "Environmental", "Engineering", "Commissioning",
                        "Racking", "Logistics", "BESS", "Substation",
                        "Procurement", "Other",
                    }
                    raw_discipline = c.get("discipline") or c.get("category") or "Other"
                    discipline = raw_discipline if raw_discipline in VALID_DISCIPLINES else "Other"

                    # Map priority
                    priority_raw = c.get("priority", "MEDIUM").upper()
                    priority = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}.get(priority_raw, "medium")

                    # Build notes from status_discussed + commitments
                    notes_parts = []
                    if c.get("status_discussed"):
                        notes_parts.append(c["status_discussed"])
                    if c.get("commitments"):
                        notes_parts.append(f"Commitments: {c['commitments']}")
                    notes = " | ".join(notes_parts) if notes_parts else None

                    convex_constraints.append({
                        "proposedAction": proposed_action,
                        "constraintData": {
                            "description": c.get("description", ""),
                            "discipline": discipline,
                            "priority": priority,
                            "owner": c.get("owner"),
                            "notes": notes,
                        },
                    })

                if convex_constraints:
                    # Use the bot_id from the original transcript queue if available,
                    # otherwise generate a call ID from timestamp
                    call_id = getattr(self, '_current_call_id', None) or f"call-{int(_time.time())}"
                    call_title = getattr(self, '_current_call_title', None) or f"Meeting — {project_name}"

                    call_date_ms = int(_time.time() * 1000)
                    await push_pending_constraint_syncs(
                        call_id=call_id,
                        call_title=call_title,
                        project_name=project_name,
                        call_date=call_date_ms,
                        constraints=convex_constraints,
                    )
                    n_constraints = len(convex_constraints)
                    logger.info(
                        f"Pushed {n_constraints} constraint(s) to Convex Calls page "
                        f"for review (project={project_name})"
                    )

                    # --- Feature: Telegram notification on Calls push ---
                    # Fire a notification so the user knows constraints
                    # are ready for review on the Calls page.
                    try:
                        await _send_calls_page_notification(
                            chat_id=self._chat_id,
                            count=n_constraints,
                            call_title=call_title,
                            project_name=project_name,
                        )
                    except Exception:
                        logger.debug("Calls page notification failed (non-fatal)", exc_info=True)

                    # --- Feature: Save raw transcript to Convex ---
                    try:
                        from bot.services.convex_client import save_call_transcript
                        await save_call_transcript(
                            call_id=call_id,
                            call_title=call_title,
                            project_name=project_name,
                            call_date=call_date_ms,
                            raw_transcript=user_message,
                        )
                    except Exception:
                        logger.debug("Transcript save to Convex failed (non-fatal)", exc_info=True)

            except Exception:
                logger.exception("Failed to push constraints to Convex Calls page (non-fatal)")
            if on_agent_event:
                await on_agent_event({"type": "agent_start", "agent": "constraints_manager (auto cross-ref)", "task": "Cross-referencing extracted constraints against ConstraintsPro"})

            try:
                proposal_result = await self._propose_constraints_sync(constraints_sync_data)
                if proposal_result and proposal_result.success and proposal_result.output:
                    sync_proposal_text = self._parse_sync_proposal(proposal_result.output) or ""
                    # Record in activity log
                    if self._subagent_log is not None:
                        self._subagent_log.append({
                            "agent": "constraints_manager (auto cross-ref)",
                            "success": proposal_result.success,
                            "duration": round(proposal_result.duration_seconds, 1),
                            "error": proposal_result.error,
                        })
                    logger.info("ConstraintsPro auto cross-reference completed successfully")
                elif proposal_result and not proposal_result.success:
                    logger.warning(f"ConstraintsPro auto cross-reference failed: {proposal_result.error}")
                    sync_proposal_text = (
                        "\n\n<b>ConstraintsPro Cross-Reference</b>: "
                        "Could not auto-compare against ConstraintsPro. Constraints from this "
                        "transcript were saved locally but not deduped."
                    )
            except Exception:
                logger.exception("ConstraintsPro auto cross-reference raised an exception")
                sync_proposal_text = ""

            if on_agent_event:
                await on_agent_event({"type": "agent_complete", "agent": "constraints_manager (auto cross-ref)", "success": bool(sync_proposal_text), "duration": 0})

        # --- PASS 2: Nimrod synthesis (proceeds even if some subagents failed) ---
        # If we have sync proposal results, inject them into the synthesis prompt
        # so Nimrod can present an integrated, deduped constraint summary.
        synthesis_prompt = self._build_synthesis_prompt(
            user_message, clean_response, results, memory_context, conversation_history,
            sync_proposal_context=sync_proposal_text,
        )

        if on_agent_event:
            await on_agent_event({"type": "pass", "pass": 2, "status": "start"})

        # Pass 2: Nimrod synthesizes, has tool access for any follow-up actions
        p2_start = time.monotonic()
        async with _semaphore:
            synthesis_result = await self.runner.run(
                agent=NIMROD,
                task_prompt=synthesis_prompt,
                no_tools=False,
                on_text_chunk=on_text_chunk,
            )
        self._pass2_duration = time.monotonic() - p2_start
        await self._track_agent_tokens(NIMROD.name)

        if on_agent_event:
            await on_agent_event({"type": "pass", "pass": 2, "status": "complete", "duration": round(self._pass2_duration, 1)})

        if not synthesis_result.success:
            # Fall back to raw subagent results (use HTML, not Markdown)
            fallback = clean_response + "\n\n---\nSubagent results:\n"
            for r in results:
                status = r.output[:500] if r.success else f"FAILED: {r.error}"
                fallback += f"\n<b>{r.agent_name}</b>: {status}\n"
            return OrchestrationResult(text=fallback, file_paths=all_file_paths)

        synthesis_text = synthesis_result.output

        # Parse and save memories from synthesis pass
        synthesis_memories = self._parse_memory_saves(synthesis_text)
        await self._process_memory_saves(synthesis_memories)
        synthesis_resolves = self._parse_resolve_actions(synthesis_text)
        await self._process_resolve_actions(synthesis_resolves)
        all_file_paths.extend(self._parse_file_created(synthesis_text))

        # Check synthesis output for restart signal too
        syn_restart, syn_reason = self._parse_restart_required(synthesis_text)
        if syn_restart:
            restart_required = True
            restart_reason = syn_reason

        # --- FOLLOW-UP SUBAGENT DISPATCH (from synthesis pass) ---
        # Multi-step workflows (e.g., probing questions) may need Nimrod to
        # dispatch additional subagents after seeing the first round of results.
        # If the synthesis output contains SUBAGENT_REQUEST blocks, run them
        # and do a final synthesis pass.
        followup_requests = self._parse_subagent_requests(synthesis_text)
        if followup_requests:
            logger.info(
                f"Nimrod dispatching {len(followup_requests)} follow-up subagent(s) from synthesis: "
                f"{[r.agent_name for r in followup_requests]}"
            )
            clean_synthesis = self._strip_structured_blocks(synthesis_text)

            followup_results = await self._run_subagents(followup_requests, user_message, on_agent_event=on_agent_event, on_tool_event=on_tool_event)

            # Track token usage for follow-up subagents
            for r in followup_results:
                await self._track_agent_tokens(r.agent_name)

            # Record follow-up subagent results in activity log
            if self._subagent_log is not None:
                self._subagent_log.extend([
                    {
                        "agent": r.agent_name,
                        "success": r.success,
                        "duration": round(r.duration_seconds, 1),
                        "error": r.error,
                    }
                    for r in followup_results
                ])

            # Collect files and restart signals from follow-up subagents
            for r in followup_results:
                if r.success and r.output:
                    all_file_paths.extend(self._parse_file_created(r.output))
                    req, reason = self._parse_restart_required(r.output)
                    if req:
                        restart_required = True
                        restart_reason = reason

            # --- FINAL SYNTHESIS: Nimrod compiles everything ---
            final_prompt = self._build_synthesis_prompt(
                user_message, clean_synthesis, followup_results,
                memory_context, conversation_history
            )
            async with _semaphore:
                final_result = await self.runner.run(
                    agent=NIMROD,
                    task_prompt=final_prompt,
                    no_tools=False,
                    on_text_chunk=on_text_chunk,
                )
            await self._track_agent_tokens(NIMROD.name)

            if final_result.success:
                synthesis_text = final_result.output
                # Parse memories/files from final synthesis
                final_memories = self._parse_memory_saves(synthesis_text)
                await self._process_memory_saves(final_memories)
                final_resolves = self._parse_resolve_actions(synthesis_text)
                await self._process_resolve_actions(final_resolves)
                all_file_paths.extend(self._parse_file_created(synthesis_text))
                fin_restart, fin_reason = self._parse_restart_required(synthesis_text)
                if fin_restart:
                    restart_required = True
                    restart_reason = fin_reason

        # NOTE: ConstraintsPro cross-reference now runs BEFORE Pass 2 synthesis
        # and is injected into the synthesis prompt, so Nimrod presents an integrated
        # deduped summary. No separate post-synthesis await needed.

        # Deduplicate file paths while preserving order
        seen = set()
        unique_files = []
        for fp in all_file_paths:
            if fp not in seen:
                seen.add(fp)
                unique_files.append(fp)

        final_text = self._strip_structured_blocks(synthesis_text)

        # Build token summary for this orchestration run
        token_summary = self._build_token_summary()
        if token_summary:
            agent_parts = ", ".join(
                f"{a['name']}={a['input_tokens']+a['output_tokens']}tok"
                for a in token_summary["agents"]
            )
            logger.info(
                f"Orchestration token usage: "
                f"{token_summary['total_tokens']} tokens "
                f"(${token_summary['total_cost_usd']:.4f}) "
                f"[{agent_parts}]"
            )

        return OrchestrationResult(
            text=final_text,
            file_paths=unique_files,
            restart_required=restart_required,
            restart_reason=restart_reason,
            token_summary=token_summary,
        )

    async def _build_memory_context(self, user_message: str) -> str:
        """Build memory context block for prompt injection.

        Fully wrapped in error handling — memory failures must never crash the
        orchestrator. Returns a sensible default string if everything fails.
        """
        parts = []

        # Recent memories
        try:
            recent = await self.memory.format_for_prompt(limit=10)
            if recent and recent not in ("(No relevant memories found.)", "(Memory unavailable.)"):
                parts.append(f"RECENT MEMORIES:\n{recent}")
        except Exception:
            logger.exception("Failed to fetch recent memories for prompt context")
            recent = None

        # Search-matched memories based on user message
        try:
            searched = await self.memory.format_for_prompt(
                query=user_message, limit=10
            )
            if (
                searched
                and searched not in ("(No relevant memories found.)", "(Memory unavailable.)")
                and searched != recent
            ):
                parts.append(f"RELEVANT MEMORIES (search-matched):\n{searched}")
        except Exception:
            # FTS match can fail on certain query strings; non-critical
            logger.debug("FTS search failed during memory context build", exc_info=True)

        # Open action items (include IDs so Nimrod can resolve them)
        try:
            actions = await self.memory.get_action_items(resolved=False)
            if actions:
                action_lines = [f"- [id:{a.id}] [{a.created_at[:10]}] {a.summary}" for a in actions[:15]]
                parts.append(f"OPEN ACTION ITEMS ({len(actions)} total):\n" + "\n".join(action_lines))
            elif hasattr(actions, "success") and not actions.success:
                parts.append("OPEN ACTION ITEMS: unavailable (memory error)")
        except Exception:
            logger.exception("Failed to fetch action items for prompt context")

        if not parts:
            return "PERSISTENT MEMORY:\n(No memories yet. This is the beginning.)"

        return "PERSISTENT MEMORY:\n" + "\n\n".join(parts)

    async def _build_nimrod_prompt(
        self, user_message: str, memory_context: str, conversation_history: str = "", source: str = "telegram"
    ) -> str:
        subagent_list = self.registry.subagent_descriptions()
        source_context = {
            "web": (
                "INTERFACE: Web GUI (browser dashboard at goliath web platform). "
                "The user is on the web interface — NOT Telegram. "
                "Do NOT reference Telegram features (inline keyboards, /commands, reply markup). "
                "You can use rich markdown formatting (headers, tables, code blocks). "
                "File links and long-form responses work well here."
            ),
            "telegram": (
                "INTERFACE: Telegram. "
                "The user is chatting via Telegram. "
                "Keep responses concise for mobile. Use Telegram-compatible HTML formatting."
            ),
        }.get(source, f"INTERFACE: {source}")
        parts = [f"{source_context}\n\n---\n\n{memory_context}\n\n---\n\n"]

        # Inject lessons from experience replay (self-improvement V3)
        if self._experience_replay:
            try:
                lessons_block = await self._experience_replay.get_lessons_for_prompt_injection(limit=3)
                if lessons_block:
                    parts.append(f"{lessons_block}\n\n---\n\n")
            except Exception:
                logger.debug("Failed to inject experience replay lessons", exc_info=True)

        # Inject cross-project semantic patterns (GAP H2)
        if self._tiered_memory:
            try:
                semantic_block = await self._tiered_memory.format_semantic_for_prompt(limit=5)
                if semantic_block:
                    parts.append(f"{semantic_block}\n\n---\n\n")
            except Exception:
                logger.debug("Failed to inject semantic memories", exc_info=True)

        # GAP H4: Inject runtime tool descriptions for Nimrod
        try:
            tool_reg = get_tool_registry()
            tool_desc = tool_reg.tool_descriptions_for_agent("nimrod")
            if tool_desc:
                parts.append(f"{tool_desc}\n\n---\n\n")
        except Exception:
            logger.debug("Failed to inject tool registry descriptions", exc_info=True)

        # Inject pending constraint sync status if one exists
        pending_sync = self.get_pending_sync_summary()
        if pending_sync:
            parts.append(f"SYSTEM STATE:\n{pending_sync}\n\n---\n\n")

        if conversation_history:
            parts.append(f"{conversation_history}\n\n---\n\n")
        parts.append(
            f"AVAILABLE SUBAGENTS:\n{subagent_list}\n\n"
            f"---\n\n"
            f"USER MESSAGE:\n{user_message}"
        )
        return "".join(parts)

    # Maximum total characters of subagent output to pass into the synthesis
    # prompt.  If all agents' combined output exceeds this, each agent's output
    # is trimmed proportionally so the most important content (first N chars per
    # agent) is kept and token overflow is avoided silently.
    _SYNTHESIS_OUTPUT_BUDGET = 8000  # chars (~2000 tokens, leaves room for the rest of the prompt)

    @staticmethod
    def _compress_agent_outputs(results: list[AgentResult], budget: int) -> list[AgentResult]:
        """Intelligently compress subagent outputs to fit within a character budget.

        Strategy:
        1. Measure total chars across successful results.
        2. If under budget — return unchanged (no compression needed).
        3. If over budget — allocate budget proportionally by agent output length
           (larger agents give up more chars), keep the leading portion of each
           output (which typically contains the key findings before verbose detail).
        4. Append a "[...truncated for synthesis — N chars omitted...]" suffix on
           any trimmed output so Nimrod knows it's seeing a compressed view.
        5. Failed agents are never modified.
        """
        successful = [r for r in results if r.success and r.output]
        total_chars = sum(len(r.output) for r in successful)

        if total_chars <= budget:
            return results  # No compression needed

        logger.info(
            f"Synthesis output budget exceeded: {total_chars} chars across "
            f"{len(successful)} agent(s) — compressing to {budget} chars to "
            f"prevent token overflow."
        )

        # Build a lookup of compressed outputs keyed by agent_name.
        # Proportion: each agent gets (its_share / total) * budget chars.
        compressed: dict[str, str] = {}
        for r in successful:
            share = len(r.output) / total_chars
            alloc = max(500, int(share * budget))  # minimum 500 chars per agent
            if len(r.output) > alloc:
                omitted = len(r.output) - alloc
                compressed[r.agent_name] = (
                    r.output[:alloc]
                    + f"\n[...truncated for synthesis — {omitted} chars omitted...]"
                )
            else:
                compressed[r.agent_name] = r.output

        # Return new AgentResult objects with replaced output (originals untouched)
        from dataclasses import replace as dc_replace
        return [
            dc_replace(r, output=compressed[r.agent_name]) if r.agent_name in compressed else r
            for r in results
        ]

    def _build_synthesis_prompt(
        self,
        user_message: str,
        nimrod_initial: str,
        results: list[AgentResult],
        memory_context: str,
        conversation_history: str = "",
        sync_proposal_context: str = "",
    ) -> str:
        # Compress subagent outputs if they would overflow the synthesis prompt.
        results = self._compress_agent_outputs(results, self._SYNTHESIS_OUTPUT_BUDGET)

        parts = [f"{memory_context}\n\n---\n\n"]
        if conversation_history:
            parts.append(f"{conversation_history}\n\n---\n\n")
        parts.extend([
            f"The user asked: {user_message}\n\n",
            f"Your initial assessment was:\n{nimrod_initial}\n\n",
            "You dispatched subagents. Here are their results:\n\n",
        ])

        failed_agents = []
        for r in results:
            if r.success:
                parts.append(
                    f"--- {r.agent_name} (completed in {r.duration_seconds:.1f}s) ---\n"
                    f"{r.output}\n\n"
                )
            else:
                parts.append(
                    f"--- {r.agent_name} (FAILED) ---\nError: {r.error}\n\n"
                )
                failed_agents.append(r.agent_name)

        # Inject ConstraintsPro cross-reference results if available
        if sync_proposal_context:
            parts.append(
                "--- AUTOMATIC CONSTRAINTSPRO CROSS-REFERENCE (already completed) ---\n"
                "The system automatically compared extracted constraints against what already "
                "exists in ConstraintsPro. Here are the dedup results:\n\n"
                f"{sync_proposal_context}\n\n"
                "IMPORTANT: Present this information INTEGRATED into your transcript summary — "
                "not as a separate section tacked on at the end. The user should see a clean "
                "'here is what we would push to ConstraintsPro' summary that shows:\n"
                "- NEW constraints that don't exist yet (would be created)\n"
                "- EXISTING constraints that would get updated with meeting notes\n"
                "- RESOLVED constraints that would be closed\n"
                "- DUPLICATES that were caught and will be skipped\n\n"
                "The dedup is ALREADY DONE — the user does not need to manually cross-reference. "
                "Tell them to say <b>\"push it\"</b> to sync the changes to ConstraintsPro, "
                "or <b>\"skip sync\"</b> to discard. Keep it simple.\n\n"
            )

        parts.append(
            "Synthesize these results for the user. KEEP IT SHORT — max 3-5 paragraphs. "
            "Use your Nimrod personality. Lead with the key takeaway. "
            "Use HTML formatting: <b>bold</b>, <i>italic</i>, <code>code</code>. "
            "NO markdown (no ** or # — they don't render in Telegram). "
            "If there's a lot of detail, summarize the highlights and offer to dig deeper. "
        )

        # Give Nimrod explicit guidance on partial failures
        if failed_agents:
            parts.append(
                f"IMPORTANT: The following subagent(s) failed or timed out: "
                f"{', '.join(failed_agents)}. Inform the user briefly about what "
                f"data is missing and offer to retry if they want. Continue with "
                f"whatever results you DO have — do NOT abandon the response. "
            )

        parts.append(
            "Save important findings to memory using MEMORY_SAVE blocks."
        )

        return "".join(parts)

    async def _run_subagents(
        self, requests: list[SubagentRequest], user_message: str, on_agent_event: Optional[Callable] = None, on_tool_event: Optional[Callable] = None
    ) -> list[AgentResult]:
        async def _run_one(req: SubagentRequest) -> AgentResult:
            agent_def = self.registry.get_subagent(req.agent_name)
            if not agent_def:
                return AgentResult(
                    agent_name=req.agent_name,
                    success=False,
                    output="",
                    duration_seconds=0.0,
                    error=f"Unknown agent: {req.agent_name}",
                )

            if on_agent_event:
                await on_agent_event({"type": "agent_start", "agent": req.agent_name, "task": req.task[:200]})

            # Build context for the subagent
            context_parts = []
            if req.project_key:
                context_parts.append(f"Project: {req.project_key}")
                # Inject project-relevant memories
                try:
                    mem = await self.memory.format_for_prompt(
                        query=req.task, project_key=req.project_key, limit=5
                    )
                    if mem != "(No relevant memories found.)":
                        context_parts.append(f"Relevant memories:\n{mem}")
                except Exception:
                    pass

            # GAP H4: Inject runtime tool descriptions from the ToolRegistry
            try:
                tool_reg = get_tool_registry()
                tool_desc = tool_reg.tool_descriptions_for_agent(req.agent_name)
                if tool_desc:
                    context_parts.append(tool_desc)
            except Exception:
                pass  # Never let tool registry lookup crash dispatch

            context = "\n".join(context_parts)

            # Per-agent tool event wrapper: tags each event with the agent name
            async def _agent_tool_cb(event, _name=req.agent_name):
                event["agent"] = _name
                await on_tool_event(event)

            start_t = time.monotonic()
            async with _semaphore:
                result = await self.runner.run(
                    agent=agent_def,
                    task_prompt=req.task,
                    context=context,
                    on_tool_event=_agent_tool_cb if on_tool_event else None,
                )

            elapsed_ms = int((time.monotonic() - start_t) * 1000)

            # --- Reliability logging (fire-and-forget, never blocks) ---
            if self._reliability_log is not None:
                try:
                    task_type = classify_task_type(req.task)
                    await self._reliability_log.log_call(
                        agent_name=req.agent_name,
                        success=result.success,
                        latency_ms=elapsed_ms,
                        task_type=task_type,
                        user_id=str(self._chat_id or ""),
                        error=result.error if not result.success else None,
                        model_used=getattr(agent_def, "model", None),
                    )
                except Exception:
                    pass  # Never let reliability logging crash agent dispatch

            if on_agent_event:
                await on_agent_event({
                    "type": "agent_complete",
                    "agent": req.agent_name,
                    "success": result.success,
                    "duration": round(elapsed_ms / 1000, 1),
                })

            # Update per-agent status in swarm state file for GUI polling
            try:
                current_state = read_swarm_state()
                if current_state.get("status") == "active" and "agent_status" in current_state:
                    current_state["agent_status"][req.agent_name] = {
                        "status": "completed" if result.success else "failed",
                        "duration_ms": elapsed_ms,
                    }
                    _write_swarm_state(current_state)
            except Exception:
                pass  # Never let state bookkeeping crash agent dispatch

            return result

        results = await asyncio.gather(
            *[_run_one(req) for req in requests],
            return_exceptions=True,
        )

        final = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final.append(
                    AgentResult(
                        agent_name=requests[i].agent_name,
                        success=False,
                        output="",
                        duration_seconds=0.0,
                        error=str(result),
                    )
                )
            else:
                final.append(result)

        return final

    async def _track_agent_tokens(self, agent_name: str) -> None:
        """Query the most recent token record for an agent and add to run totals.

        Called after each runner.run() completes. Uses token_tracker.get_by_agent()
        to fetch the latest record. Never raises — token tracking failures are
        silently logged and ignored.
        """
        if self._token_tracker is None:
            return
        try:
            records = await self._token_tracker.get_by_agent(agent_name, limit=1)
            if not records:
                return
            rec = records[0]
            self._run_tokens["total_input"] += rec.get("input_tokens", 0) or 0
            self._run_tokens["total_output"] += rec.get("output_tokens", 0) or 0
            self._run_tokens["total_cost"] += rec.get("cost_usd", 0.0) or 0.0
            self._run_tokens["agents"].append({
                "name": agent_name,
                "input_tokens": rec.get("input_tokens", 0) or 0,
                "output_tokens": rec.get("output_tokens", 0) or 0,
                "cost_usd": rec.get("cost_usd", 0.0) or 0.0,
                "duration_ms": rec.get("duration_ms", 0) or 0,
            })
        except Exception:
            logger.debug(f"Failed to track tokens for '{agent_name}'", exc_info=True)

    def _build_token_summary(self) -> Optional[dict]:
        """Build a token summary dict for the orchestration run.

        Returns None if no tokens were tracked.
        """
        tokens = self._run_tokens
        if tokens["total_input"] == 0 and tokens["total_output"] == 0:
            return None
        return {
            "total_input": tokens["total_input"],
            "total_output": tokens["total_output"],
            "total_tokens": tokens["total_input"] + tokens["total_output"],
            "total_cost_usd": round(tokens["total_cost"], 4),
            "agents": tokens["agents"],
        }

    async def _process_memory_saves(self, saves: list[MemorySaveRequest]) -> None:
        for save in saves:
            try:
                await self.memory.save(
                    category=save.category,
                    summary=save.summary,
                    detail=save.detail,
                    project_key=save.project_key,
                    source="nimrod",
                    tags=save.tags,
                )
            except Exception:
                logger.exception(f"Failed to save memory: {save.summary[:80]}")

    # --- Parsing methods ---

    @staticmethod
    def _find_blocks(text: str, block_type: str) -> list[str]:
        """Extract structured block contents with robust pattern matching.

        Handles both well-formed blocks (```TYPE\\n...```) and blocks where
        the agent forgot the closing backticks (matches until next block or EOF).
        Wraps in try/except so malformed output never crashes the orchestrator.
        """
        try:
            # Primary pattern: well-formed fenced blocks
            pattern = rf"```{block_type}\s*\n(.*?)```"
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                return matches

            # Fallback: block without closing backticks — grab until next
            # structured block or end of text (greedy but bounded)
            fallback = rf"```{block_type}\s*\n(.*?)(?=```[A-Z_]|\Z)"
            matches = re.findall(fallback, text, re.DOTALL)
            if matches:
                logger.warning(
                    f"Block parser: found {len(matches)} {block_type} block(s) "
                    f"with missing closing backticks — recovered via fallback pattern"
                )
            return matches

        except Exception:
            logger.exception(f"Block parser failed for block_type={block_type}")
            return []

    def _parse_subagent_requests(self, text: str) -> list[SubagentRequest]:
        try:
            matches = self._find_blocks(text, "SUBAGENT_REQUEST")
            requests = []
            for block in matches:
                fields = self._parse_block_fields(block)
                agent = fields.get("agent", "").strip()
                task = fields.get("task", "").strip()
                if agent and task:
                    proj = fields.get("project", "").strip()
                    requests.append(
                        SubagentRequest(
                            agent_name=agent,
                            task=task,
                            project_key=proj if proj and proj != "null" else None,
                        )
                    )
                elif agent or task:
                    logger.warning(
                        f"Malformed SUBAGENT_REQUEST: agent={agent!r}, task={task!r} "
                        f"(both required) — skipping this block"
                    )
            return requests
        except Exception:
            logger.exception("_parse_subagent_requests crashed — returning empty list")
            return []

    def _parse_memory_saves(self, text: str) -> list[MemorySaveRequest]:
        try:
            matches = self._find_blocks(text, "MEMORY_SAVE")
            saves = []
            for block in matches:
                fields = self._parse_block_fields(block)
                category = fields.get("category", "").strip()
                summary = fields.get("summary", "").strip()
                if category and summary:
                    proj = fields.get("project", "").strip()
                    saves.append(
                        MemorySaveRequest(
                            category=category,
                            summary=summary,
                            detail=fields.get("detail", "").strip() or None,
                            project_key=proj if proj and proj != "null" else None,
                            tags=fields.get("tags", "").strip() or None,
                        )
                    )
                elif category or summary:
                    logger.warning(
                        f"Malformed MEMORY_SAVE: category={category!r}, summary={summary!r} "
                        f"(both required) — skipping"
                    )
            return saves
        except Exception:
            logger.exception("_parse_memory_saves crashed — returning empty list")
            return []

    def _parse_file_created(self, text: str) -> list[str]:
        try:
            matches = self._find_blocks(text, "FILE_CREATED")
            paths = []
            for block in matches:
                fields = self._parse_block_fields(block)
                path = fields.get("path", "").strip()
                if path:
                    paths.append(path)
            return paths
        except Exception:
            logger.exception("_parse_file_created crashed — returning empty list")
            return []

    def _parse_restart_required(self, text: str) -> tuple[bool, str]:
        """Check for RESTART_REQUIRED block in agent output."""
        try:
            matches = self._find_blocks(text, "RESTART_REQUIRED")
            if matches:
                fields = self._parse_block_fields(matches[0])
                reason = fields.get("reason", "Code changes applied").strip()
                return True, reason
            return False, ""
        except Exception:
            logger.exception("_parse_restart_required crashed — returning False")
            return False, ""

    def _parse_resolve_actions(self, text: str) -> list[int]:
        """Parse RESOLVE_ACTION blocks from agent output. Returns list of memory IDs."""
        try:
            matches = self._find_blocks(text, "RESOLVE_ACTION")
            ids = []
            for block in matches:
                fields = self._parse_block_fields(block)
                raw_id = fields.get("id", "").strip()
                if raw_id.isdigit():
                    ids.append(int(raw_id))
                elif raw_id:
                    logger.warning(f"Malformed RESOLVE_ACTION id: {raw_id!r} — not a digit")
            return ids
        except Exception:
            logger.exception("_parse_resolve_actions crashed — returning empty list")
            return []

    async def _process_resolve_actions(self, memory_ids: list[int]) -> None:
        """Mark the given action item memory IDs as resolved."""
        for mid in memory_ids:
            try:
                await self.memory.resolve_action_item(mid)
                logger.info(f"Resolved action item #{mid}")
            except Exception:
                logger.exception(f"Failed to resolve action item #{mid}")

    # --- Constraints Sync Pipeline (Human-in-the-Loop) ---

    # Patterns that indicate user wants to approve/reject the pending sync
    _SYNC_APPROVE_PATTERNS = [
        r"approve\s*(the\s+)?constraint\s*sync",
        r"push\s*(the\s+)?constraints",
        r"sync\s*(the\s+)?constraints",
        r"approve\s*(the\s+)?sync",
        r"yes[,.]?\s*(push|sync|approve)",
        r"go\s+ahead\s+(with\s+)?(the\s+)?(sync|push|constraints)",
        r"looks\s+good[,.]?\s*(push|sync|approve)",
        r"^push\s*it\s*$",
        r"^push\s*it[.!]?\s*$",
        r"^push$",
        r"^do\s*it$",
        r"^send\s*it$",
        r"^ship\s*it$",
        r"^yes[,.]?\s*push\s*it",
        r"^lgtm",
        r"looks\s+good[,.]?\s*push\s*it",
        r"push\s+it\s+to\s+constraintspro",
        r"go\s+ahead\s+and\s+push",
    ]
    _SYNC_REJECT_PATTERNS = [
        r"reject\s*(the\s+)?constraint\s*sync",
        r"discard\s*(the\s+)?constraint\s*sync",
        r"reject\s*(the\s+)?sync",
        r"don'?t\s+(push|sync)\s*(the\s+)?constraints",
        r"cancel\s*(the\s+)?sync",
        r"skip\s*(the\s+)?sync",
        r"^skip$",
        r"^skip\s*it$",
        r"^nah$",
        r"^no[,.]?\s*skip",
        r"^don'?t\s+push",
        r"^discard$",
    ]

    async def _check_constraint_sync_approval(
        self, user_message: str
    ) -> Optional[OrchestrationResult]:
        """Check if the user message is approving or rejecting a pending constraint sync.

        Returns an OrchestrationResult if handled, None if this isn't a sync approval message.
        """
        if not self.has_pending_sync():
            return None

        msg_lower = user_message.lower().strip()

        # Check for approval
        for pattern in self._SYNC_APPROVE_PATTERNS:
            if re.search(pattern, msg_lower):
                logger.info("User approved constraint sync — executing")
                p1_start = time.monotonic()
                result = await self.execute_approved_sync()
                self._pass1_duration = time.monotonic() - p1_start

                if result and result.success:
                    summary = self._parse_sync_summary(result.output)
                    self._subagent_log = [{
                        "agent": "constraints_manager (approved sync)",
                        "success": True,
                        "duration": round(result.duration_seconds, 1),
                        "error": None,
                    }]
                    text = summary or (
                        "<b>ConstraintsPro Sync Complete</b>\n"
                        "Constraints have been pushed to ConstraintsPro."
                    )
                    return OrchestrationResult(text=text, file_paths=[])
                else:
                    error = result.error if result else "No pending sync data"
                    return OrchestrationResult(
                        text=(
                            f"<b>ConstraintsPro Sync Failed</b>\n"
                            f"Something went wrong: {error}\n"
                            f"The pending sync has been preserved — you can try again."
                        ),
                        file_paths=[],
                    )

        # Check for rejection
        for pattern in self._SYNC_REJECT_PATTERNS:
            if re.search(pattern, msg_lower):
                logger.info("User rejected constraint sync — discarding")
                self.discard_pending_sync()
                return OrchestrationResult(
                    text=(
                        "<b>Constraint sync discarded.</b>\n"
                        "No changes were pushed to ConstraintsPro."
                    ),
                    file_paths=[],
                )

        # Not a sync approval/rejection — continue with normal flow
        return None

    def _parse_constraints_sync(self, text: str) -> Optional[dict]:
        """Parse CONSTRAINTS_SYNC block from transcript_processor output.

        Returns dict with 'project' and 'constraints' (list) or None.
        """
        try:
            blocks = self._find_blocks(text, "CONSTRAINTS_SYNC")
            if not blocks:
                return None
            block = blocks[0]
        except Exception:
            logger.exception("_parse_constraints_sync failed to find block")
            return None

        # Extract project field (first line typically)
        project = ""
        project_match = re.search(r"^project:\s*(.+)$", block, re.MULTILINE)
        if project_match:
            project = project_match.group(1).strip()

        # Extract constraints JSON — find everything after "constraints:" up to end of block.
        # The JSON may be on a single line or span multiple lines.
        constraints_match = re.search(r"constraints:\s*(\[.*)", block, re.DOTALL)
        if not constraints_match:
            return None

        raw_constraints = constraints_match.group(1).strip()
        if not raw_constraints:
            return None

        try:
            constraints = json.loads(raw_constraints)
            if not isinstance(constraints, list) or not constraints:
                return None
        except json.JSONDecodeError:
            logger.warning("Failed to parse CONSTRAINTS_SYNC JSON — skipping sync")
            return None

        return {"project": project, "constraints": constraints}

    async def _propose_constraints_sync(
        self, sync_data: dict, source_description: str = "meeting transcript"
    ) -> Optional[AgentResult]:
        """Dispatch constraints_manager in READ-ONLY mode to generate a sync proposal.

        This compares extracted constraints against existing ConstraintsPro data and
        produces a proposal of what would be created/updated/closed. The proposal is
        saved to a JSON file for the user to approve before any writes happen.
        """
        constraints = sync_data["constraints"]
        project = sync_data.get("project", "unknown")

        constraints_json = json.dumps(constraints, indent=2)

        prompt = f"""\
You are the AUTOMATIC DEDUP ENGINE for the transcript-to-ConstraintsPro pipeline. \
Your job is critical: the user relies on your analysis to avoid creating duplicates. \
This is a READ-ONLY analysis — DO NOT create, update, or modify anything. You produce \
a PROPOSAL of what WOULD change if the user approves.

STEP 1: Call `projects_list` to get all project IDs and names.

STEP 2: For each constraint below, find the matching project by name/key and call \
`constraints_list_by_project` to get ALL existing constraints for that project. \
For each existing constraint that seems even remotely related, call \
`constraints_get_with_notes` to see the full detail and notes history — this is \
essential for accurate semantic matching.

STEP 3: DEDUPLICATION ANALYSIS — THIS IS YOUR PRIMARY VALUE. For each extracted \
constraint, compare against EVERY existing constraint in the project:
- Match by SEMANTIC SIMILARITY, not exact text. If an existing constraint covers the same \
issue (same blocker, same material, same vendor problem, same subcontractor issue, \
same permit/inspection, same equipment delay, etc.), it is a MATCH — even if worded \
differently.
- Be AGGRESSIVE about matching. Two constraints about "waiting on DC cable delivery" and \
"DC collection cable PO delayed" are the SAME constraint. Don't create duplicates.
- Classify each extracted constraint as one of:
  a) MATCH_UPDATE — Matches an existing constraint; would add meeting notes and/or update priority
  b) MATCH_RESOLVE — Matches an existing constraint AND extracted has resolved=true; would close it
  c) NEW — No existing match and not resolved; would create a new constraint
  d) SKIP — Resolved but no existing match (nothing to do), OR duplicate of another \
     extracted constraint that is already being handled

STEP 4: For each classified constraint, note:
- The extracted description, priority, owner, category
- If MATCH: the existing ConstraintsPro constraint title and ID
- If MATCH: what would change (notes to add, priority change, status change)
- If NEW: what would be created (title, priority, category, owner, need-by date)

STEP 5: SECOND-PASS DEDUP CHECK — Review your own proposal for internal duplicates. \
If two extracted constraints would both create NEW entries that describe the same issue, \
merge them into one CREATE and mark the other as SKIP.

DO NOT use any write tools (constraints_create, constraints_update, constraints_update_status, \
constraints_add_note, constraints_bulk_import). READ ONLY.

EXTRACTED CONSTRAINTS FROM {source_description.upper()}:
{constraints_json}

After your analysis, output a SYNC_PROPOSAL block with a JSON array of proposed actions:

```SYNC_PROPOSAL
project: {project}
meeting_date: {time.strftime('%Y-%m-%d')}
source: {source_description}
actions: [{{"action": "CREATE|UPDATE|RESOLVE|SKIP", "description": "...", "priority": "HIGH|MEDIUM|LOW", "owner": "...", "category": "...", "need_by_date": "YYYY-MM-DD or null", "existing_title": "title of matching constraint or null", "existing_id": "ConstraintsPro ID or null", "notes_to_add": "meeting notes text or null", "priority_change": "OLD -> NEW or null", "reason": "brief explanation of why this action"}}]
```

The JSON must be valid and on a single line after "actions: ".
"""

        logger.info(
            f"Dispatching constraints_manager for sync PROPOSAL (read-only) — "
            f"{len(constraints)} constraint(s) from {source_description}, "
            f"project={project}"
        )

        async with _semaphore:
            result = await self.runner.run(
                agent=CONSTRAINTS_MANAGER,
                task_prompt=prompt,
                context=f"Project: {project}" if project and project != "unknown" else "",
                timeout=300,
            )

        if result.success:
            logger.info(
                f"Constraints sync proposal generated in {result.duration_seconds:.1f}s"
            )
            # Parse and save the proposal for later approval
            self._save_pending_sync(result.output, sync_data)
        else:
            logger.error(
                f"Constraints sync proposal failed: {result.error}"
            )

        return result

    def _save_pending_sync(self, proposal_output: str, original_sync_data: dict) -> None:
        """Save the pending sync proposal to a JSON file for later approval."""
        proposal = self._parse_sync_proposal_data(proposal_output)
        if not proposal:
            logger.warning("Could not parse SYNC_PROPOSAL — saving raw sync data only")
            proposal = {"actions": [], "raw_output": proposal_output[:3000]}

        pending = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "project": original_sync_data.get("project", "unknown"),
            "original_constraints": original_sync_data["constraints"],
            "proposal": proposal,
        }

        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            sync_path = _pending_sync_path(self._chat_id)
            with open(sync_path, "w") as f:
                json.dump(pending, f, indent=2)
            logger.info(f"Saved pending constraint sync to {sync_path} (chat_id={self._chat_id})")
        except Exception:
            logger.exception("Failed to save pending constraint sync")

    def _parse_sync_proposal_data(self, text: str) -> Optional[dict]:
        """Parse SYNC_PROPOSAL block and return structured data."""
        try:
            blocks = self._find_blocks(text, "SYNC_PROPOSAL")
            if not blocks:
                return None
            block = blocks[0]
        except Exception:
            logger.exception("_parse_sync_proposal_data failed to find block")
            return None

        fields = self._parse_block_fields(block)

        raw_actions = fields.get("actions", "").strip()
        actions = []
        if raw_actions:
            try:
                actions = json.loads(raw_actions)
                if not isinstance(actions, list):
                    actions = []
            except json.JSONDecodeError:
                logger.warning("Failed to parse SYNC_PROPOSAL actions JSON")

        return {
            "project": fields.get("project", ""),
            "meeting_date": fields.get("meeting_date", ""),
            "source": fields.get("source", ""),
            "actions": actions,
        }

    def _parse_sync_proposal(self, text: str) -> Optional[str]:
        """Extract SYNC_PROPOSAL block and format as an HTML notification for the user.

        The output is designed to be integrated into Nimrod's synthesis response,
        presenting an already-deduped constraint summary that the user can approve
        with a simple "push it".
        """
        proposal = self._parse_sync_proposal_data(text)
        if not proposal or not proposal.get("actions"):
            # Even without a structured proposal, check if a per-chat pending sync was saved
            if _pending_sync_path(self._chat_id).exists():
                return (
                    "<b>ConstraintsPro Sync Ready</b>\n"
                    "Constraints from this transcript have been cross-referenced against "
                    "ConstraintsPro (duplicates already caught). "
                    "Say <b>\"push it\"</b> to sync, or <b>\"skip\"</b> to discard."
                )
            return None

        actions = proposal["actions"]
        creates = [a for a in actions if a.get("action") == "CREATE"]
        updates = [a for a in actions if a.get("action") == "UPDATE"]
        resolves = [a for a in actions if a.get("action") == "RESOLVE"]
        skips = [a for a in actions if a.get("action") == "SKIP"]

        total_changes = len(creates) + len(updates) + len(resolves)
        if total_changes == 0:
            return (
                "<b>ConstraintsPro Sync</b>: No changes needed — "
                "all constraints from this transcript already match ConstraintsPro."
            )

        parts = [
            f"<b>ConstraintsPro — Ready to Push</b> "
            f"({total_changes} change{'s' if total_changes != 1 else ''}, "
            f"already deduped)"
        ]

        if creates:
            parts.append(f"\n<b>NEW ({len(creates)}):</b>")
            for c in creates:
                desc = (c.get("description") or "")[:80]
                prio = c.get("priority", "?")
                owner = c.get("owner", "unassigned")
                parts.append(f"  - [{prio}] {desc} (owner: {owner})")

        if updates:
            parts.append(f"\n<b>UPDATE ({len(updates)} existing):</b>")
            for u in updates:
                existing = u.get("existing_title") or u.get("description", "")
                existing = existing[:80]
                prio_change = u.get("priority_change")
                detail = "+ meeting notes" + (f", priority {prio_change}" if prio_change else "")
                parts.append(f"  - {existing} ({detail})")

        if resolves:
            parts.append(f"\n<b>CLOSE ({len(resolves)}):</b>")
            for r in resolves:
                existing = (r.get("existing_title") or r.get("description", ""))[:80]
                parts.append(f"  - {existing}")

        if skips:
            parts.append(f"\n<i>Caught {len(skips)} duplicate{'s' if len(skips) != 1 else ''} — already in ConstraintsPro, skipped.</i>")

        parts.append(
            "\nSay <b>\"push it\"</b> to sync these to ConstraintsPro, "
            "or <b>\"skip\"</b> to discard."
        )

        return "\n".join(parts)

    async def execute_approved_sync(self) -> Optional[AgentResult]:
        """Execute a previously proposed constraint sync after user approval.

        Reads the pending sync data from disk and dispatches constraints_manager
        with WRITE permissions to actually create/update/close constraints.
        """
        sync_path = _pending_sync_path(self._chat_id)
        if not sync_path.exists():
            logger.warning(f"No pending constraint sync found (chat_id={self._chat_id}, path={sync_path})")
            return None

        try:
            with open(sync_path, "r") as f:
                pending = json.load(f)
        except Exception:
            logger.exception("Failed to read pending constraint sync")
            return None

        constraints = pending.get("original_constraints", [])
        project = pending.get("project", "unknown")
        proposal = pending.get("proposal", {})
        actions = proposal.get("actions", [])
        source = proposal.get("source", "meeting transcript")

        if not constraints:
            logger.warning("Pending sync has no constraints")
            return None

        constraints_json = json.dumps(constraints, indent=2)
        actions_json = json.dumps(actions, indent=2) if actions else "[]"

        prompt = f"""\
The user has APPROVED syncing constraints from a {source} to ConstraintsPro. \
Execute the following changes:

STEP 1: Call `projects_list` to get all project IDs and names.

STEP 2: For each constraint below, find the matching project and call \
`constraints_list_by_project` to get existing constraints.

STEP 3: Execute the APPROVED ACTIONS. Here is what was proposed and approved:

PROPOSED ACTIONS (from the comparison analysis):
{actions_json}

ORIGINAL EXTRACTED CONSTRAINTS:
{constraints_json}

For each action:
- CREATE: Use `constraints_create` with the specified fields. Add a note: \
"Synced from {source} ({time.strftime('%Y-%m-%d')}). Discussion: [status_discussed]"
- UPDATE: Use `constraints_add_note` to add meeting notes. If priority_change is specified, \
use `constraints_update` to change priority.
- RESOLVE: Use `constraints_update_status` to set status to "Resolved". Add a closing note: \
"Confirmed resolved in {source} ({time.strftime('%Y-%m-%d')}). [status_discussed]"
- SKIP: Do nothing.

IMPORTANT:
- This is an AUTHORIZED WRITE operation — the user explicitly approved these changes.
- NEVER create duplicates. Match against existing constraints by semantic similarity.
- If the proposed actions list is empty, fall back to the original constraints and do your \
own deduplication analysis before creating/updating.

NOTE-LEVEL DEDUP (CRITICAL — prevents duplicate notes if pipeline runs twice or overlaps \
with a manual push):
- Before calling `constraints_add_note` on ANY constraint, first call \
`constraints_get_with_notes` for that constraint and check ALL existing notes.
- If any note from TODAY (same calendar date) already covers the same topic (shares 3+ key \
terms like constraint name, vendor, material, action, status keywords), SKIP the note add.
- Log every skip as: "DEDUP_SKIP: [constraint title] — same-day note already exists"
- When in doubt, SKIP rather than duplicate. A skipped note is harmless; a duplicate clutters the log.

After processing, output a sync summary:

```SYNC_SUMMARY
created: <number of new constraints created>
updated: <number of existing constraints updated with notes>
closed: <number of constraints marked resolved>
skipped: <number skipped for any reason>
details: <brief description of what was synced>
```
"""

        logger.info(
            f"Executing APPROVED constraints sync — "
            f"{len(constraints)} constraint(s), project={project}"
        )

        async with _semaphore:
            result = await self.runner.run(
                agent=CONSTRAINTS_MANAGER,
                task_prompt=prompt,
                context=f"Project: {project}" if project and project != "unknown" else "",
                timeout=300,
            )

        if result.success:
            logger.info(
                f"Approved constraints sync completed in {result.duration_seconds:.1f}s"
            )
            # Clean up the per-chat pending file
            try:
                sync_path.unlink(missing_ok=True)
                logger.info(f"Cleaned up pending constraint sync file: {sync_path}")
            except Exception:
                logger.exception("Failed to clean up pending sync file")
        else:
            logger.error(
                f"Approved constraints sync failed: {result.error}"
            )

        return result

    def discard_pending_sync(self) -> bool:
        """Discard a pending constraint sync proposal (user rejected it)."""
        sync_path = _pending_sync_path(self._chat_id)
        if sync_path.exists():
            try:
                sync_path.unlink()
                logger.info(f"Discarded pending constraint sync (chat_id={self._chat_id})")
                return True
            except Exception:
                logger.exception("Failed to discard pending sync")
        return False

    def has_pending_sync(self) -> bool:
        """Check if there is a pending constraint sync awaiting approval for this session."""
        return _pending_sync_path(self._chat_id).exists()

    def get_pending_sync_summary(self) -> Optional[str]:
        """Get a brief summary of the pending sync for Nimrod's context (session-scoped)."""
        sync_path = _pending_sync_path(self._chat_id)
        if not sync_path.exists():
            return None
        try:
            with open(sync_path, "r") as f:
                pending = json.load(f)
            project = pending.get("project", "unknown")
            created_at = pending.get("created_at", "unknown")
            constraints = pending.get("original_constraints", [])
            actions = pending.get("proposal", {}).get("actions", [])
            creates = len([a for a in actions if a.get("action") == "CREATE"])
            updates = len([a for a in actions if a.get("action") == "UPDATE"])
            resolves = len([a for a in actions if a.get("action") == "RESOLVE"])
            return (
                f"PENDING CONSTRAINT SYNC (from {created_at}, project: {project}): "
                f"{len(constraints)} constraints extracted — "
                f"proposal: {creates} new, {updates} updates, {resolves} to resolve. "
                f"Already deduped — awaiting user to say 'push it' or 'skip'."
            )
        except Exception:
            return "PENDING CONSTRAINT SYNC: File exists but could not be read."

    def _parse_sync_summary(self, text: str) -> Optional[str]:
        """Extract SYNC_SUMMARY block from constraints_manager output
        and format it as an HTML notification for the user."""
        try:
            blocks = self._find_blocks(text, "SYNC_SUMMARY")
            if not blocks:
                return None
            fields = self._parse_block_fields(blocks[0])
        except Exception:
            logger.exception("_parse_sync_summary failed")
            return None
        created = fields.get("created", "0")
        updated = fields.get("updated", "0")
        closed = fields.get("closed", "0")
        details = fields.get("details", "")

        # Only report if something actually happened
        total_actions = 0
        for val in [created, updated, closed]:
            try:
                total_actions += int(val)
            except ValueError:
                pass

        if total_actions == 0:
            return None

        parts = ["<b>ConstraintsPro Sync Complete</b>"]
        if created and created != "0":
            parts.append(f"  Created: {created} new")
        if updated and updated != "0":
            parts.append(f"  Updated: {updated} existing")
        if closed and closed != "0":
            parts.append(f"  Closed: {closed} resolved")
        if details:
            parts.append(f"  {details}")

        return "\n".join(parts)

    def _strip_structured_blocks(self, text: str) -> str:
        text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```FILE_CREATED\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```RESTART_REQUIRED\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```RESOLVE_ACTION\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```CONSTRAINTS_SYNC\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```SYNC_PROPOSAL\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```SYNC_SUMMARY\s*\n.*?```", "", text, flags=re.DOTALL)
        return text.strip()

    @staticmethod
    def _parse_block_fields(block: str) -> dict[str, str]:
        """Parse key: value fields from a structured block.

        HIGH-SEVERITY ENGINE GAP FIX: The old implementation broke on multi-line
        values (e.g., task fields spanning several lines) because it treated every
        line with a colon as a new key. The new version:
          1. Recognises known field keys and only splits on those.
          2. Accumulates subsequent non-key lines as continuation of the previous value.
          3. Falls back to the simple key:value parser for unknown formats.
          4. Wraps in try/except so malformed blocks never crash the orchestrator.

        Known field keys (case-insensitive): agent, task, project, category,
        summary, detail, tags, path, description, reason, id, action,
        created, updated, closed, details.
        """
        _KNOWN_KEYS = {
            "agent", "task", "project", "category", "summary", "detail",
            "tags", "path", "description", "reason", "id", "action",
            "created", "updated", "closed", "details",
        }
        try:
            fields: dict[str, str] = {}
            current_key: str | None = None
            current_value_lines: list[str] = []

            for line in block.strip().split("\n"):
                # Check if this line starts a new known field
                if ":" in line:
                    candidate_key, _, candidate_value = line.partition(":")
                    candidate_key_stripped = candidate_key.strip().lower()

                    if candidate_key_stripped in _KNOWN_KEYS:
                        # Save previous field
                        if current_key is not None:
                            fields[current_key] = "\n".join(current_value_lines).strip()
                        current_key = candidate_key.strip()
                        current_value_lines = [candidate_value.strip()]
                        continue

                # If no known key matched, this is a continuation line
                if current_key is not None:
                    current_value_lines.append(line)
                else:
                    # No current key yet — try simple key:value as fallback
                    if ":" in line:
                        key, _, value = line.partition(":")
                        fields[key.strip()] = value.strip()

            # Save the last field
            if current_key is not None:
                fields[current_key] = "\n".join(current_value_lines).strip()

            return fields

        except Exception:
            # Absolute last resort: never let parsing crash the orchestrator
            logger.exception("_parse_block_fields failed on block — returning empty")
            return {}
