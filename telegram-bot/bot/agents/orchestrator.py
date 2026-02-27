import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from bot.agents.definitions import NIMROD
from bot.agents.registry import AgentRegistry
from bot.agents.runner import SubagentRunner, AgentResult
from bot.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Limit concurrent Claude subprocesses
_semaphore = asyncio.Semaphore(3)


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


class NimrodOrchestrator:
    """
    Two-pass orchestration engine.

    Pass 1: Nimrod analyzes the user's message, decides to handle directly or
            dispatch subagents (via structured SUBAGENT_REQUEST blocks).
    Pass 2: If subagents were dispatched, Nimrod synthesizes their results.

    Memory is injected into prompts and saved from MEMORY_SAVE blocks.
    """

    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.registry = AgentRegistry()
        self.runner = SubagentRunner()
        # Activity log instrumentation (read by orchestration handler)
        self._pass1_duration: Optional[float] = None
        self._pass2_duration: Optional[float] = None
        self._subagent_log: Optional[list[dict]] = None

    async def handle_message(self, user_message: str, conversation_history: str = "") -> OrchestrationResult:
        all_file_paths: list[str] = []

        # Gather memory context
        memory_context = await self._build_memory_context(user_message)

        # --- PASS 1: Nimrod routing ---
        pass1_prompt = self._build_nimrod_prompt(user_message, memory_context, conversation_history)

        # Pass 1: Nimrod has full tool access but is prompted to delegate complex work
        p1_start = time.monotonic()
        async with _semaphore:
            nimrod_result = await self.runner.run(
                agent=NIMROD,
                task_prompt=pass1_prompt,
                no_tools=False,
            )
        self._pass1_duration = time.monotonic() - p1_start

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
            return OrchestrationResult(
                text=clean_response,
                file_paths=all_file_paths,
                restart_required=req,
                restart_reason=reason,
            )

        # --- DISPATCH SUBAGENTS ---
        logger.info(
            f"Nimrod dispatching {len(subagent_requests)} subagent(s): "
            f"{[r.agent_name for r in subagent_requests]}"
        )

        results = await self._run_subagents(subagent_requests, user_message)

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

        # Collect FILE_CREATED and RESTART_REQUIRED from subagent outputs
        restart_required = False
        restart_reason = ""
        for r in results:
            if r.success and r.output:
                all_file_paths.extend(self._parse_file_created(r.output))
                req, reason = self._parse_restart_required(r.output)
                if req:
                    restart_required = True
                    restart_reason = reason

        # --- PASS 2: Nimrod synthesis ---
        synthesis_prompt = self._build_synthesis_prompt(
            user_message, clean_response, results, memory_context, conversation_history
        )

        # Pass 2: Nimrod synthesizes, has tool access for any follow-up actions
        p2_start = time.monotonic()
        async with _semaphore:
            synthesis_result = await self.runner.run(
                agent=NIMROD,
                task_prompt=synthesis_prompt,
                no_tools=False,
            )
        self._pass2_duration = time.monotonic() - p2_start

        if not synthesis_result.success:
            # Fall back to raw subagent results
            fallback = clean_response + "\n\n---\nSubagent results:\n"
            for r in results:
                fallback += f"\n**{r.agent_name}**: {r.output[:500] if r.success else r.error}\n"
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

            followup_results = await self._run_subagents(followup_requests, user_message)

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
                )

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

        # Deduplicate file paths while preserving order
        seen = set()
        unique_files = []
        for fp in all_file_paths:
            if fp not in seen:
                seen.add(fp)
                unique_files.append(fp)

        return OrchestrationResult(
            text=self._strip_structured_blocks(synthesis_text),
            file_paths=unique_files,
            restart_required=restart_required,
            restart_reason=restart_reason,
        )

    async def _build_memory_context(self, user_message: str) -> str:
        """Build memory context block for prompt injection."""
        parts = []

        # Recent memories
        recent = await self.memory.format_for_prompt(limit=10)
        if recent and recent != "(No relevant memories found.)":
            parts.append(f"RECENT MEMORIES:\n{recent}")

        # Search-matched memories based on user message
        try:
            searched = await self.memory.format_for_prompt(
                query=user_message, limit=10
            )
            if (
                searched
                and searched != "(No relevant memories found.)"
                and searched != recent
            ):
                parts.append(f"RELEVANT MEMORIES (search-matched):\n{searched}")
        except Exception:
            # FTS match can fail on certain query strings; non-critical
            pass

        # Open action items (include IDs so Nimrod can resolve them)
        actions = await self.memory.get_action_items(resolved=False)
        if actions:
            action_lines = [f"- [id:{a.id}] [{a.created_at[:10]}] {a.summary}" for a in actions[:15]]
            parts.append(f"OPEN ACTION ITEMS ({len(actions)} total):\n" + "\n".join(action_lines))

        if not parts:
            return "PERSISTENT MEMORY:\n(No memories yet. This is the beginning.)"

        return "PERSISTENT MEMORY:\n" + "\n\n".join(parts)

    def _build_nimrod_prompt(
        self, user_message: str, memory_context: str, conversation_history: str = ""
    ) -> str:
        subagent_list = self.registry.subagent_descriptions()
        parts = [f"{memory_context}\n\n---\n\n"]
        if conversation_history:
            parts.append(f"{conversation_history}\n\n---\n\n")
        parts.append(
            f"AVAILABLE SUBAGENTS:\n{subagent_list}\n\n"
            f"---\n\n"
            f"USER MESSAGE:\n{user_message}"
        )
        return "".join(parts)

    def _build_synthesis_prompt(
        self,
        user_message: str,
        nimrod_initial: str,
        results: list[AgentResult],
        memory_context: str,
        conversation_history: str = "",
    ) -> str:
        parts = [f"{memory_context}\n\n---\n\n"]
        if conversation_history:
            parts.append(f"{conversation_history}\n\n---\n\n")
        parts.extend([
            f"The user asked: {user_message}\n\n",
            f"Your initial assessment was:\n{nimrod_initial}\n\n",
            "You dispatched subagents. Here are their results:\n\n",
        ])

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

        parts.append(
            "Synthesize these results for the user. KEEP IT SHORT — max 3-5 paragraphs. "
            "Use your Nimrod personality. Lead with the key takeaway. "
            "Use HTML formatting: <b>bold</b>, <i>italic</i>, <code>code</code>. "
            "NO markdown (no ** or # — they don't render in Telegram). "
            "If there's a lot of detail, summarize the highlights and offer to dig deeper. "
            "If any subagent failed, note it briefly. "
            "Save important findings to memory using MEMORY_SAVE blocks."
        )

        return "".join(parts)

    async def _run_subagents(
        self, requests: list[SubagentRequest], user_message: str
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

            context = "\n".join(context_parts)

            async with _semaphore:
                return await self.runner.run(
                    agent=agent_def,
                    task_prompt=req.task,
                    context=context,
                )

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

    def _parse_subagent_requests(self, text: str) -> list[SubagentRequest]:
        pattern = r"```SUBAGENT_REQUEST\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)

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

        return requests

    def _parse_memory_saves(self, text: str) -> list[MemorySaveRequest]:
        pattern = r"```MEMORY_SAVE\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)

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

        return saves

    def _parse_file_created(self, text: str) -> list[str]:
        pattern = r"```FILE_CREATED\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)

        paths = []
        for block in matches:
            fields = self._parse_block_fields(block)
            path = fields.get("path", "").strip()
            if path:
                paths.append(path)
        return paths

    def _parse_restart_required(self, text: str) -> tuple[bool, str]:
        """Check for RESTART_REQUIRED block in agent output."""
        pattern = r"```RESTART_REQUIRED\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            fields = self._parse_block_fields(match.group(1))
            reason = fields.get("reason", "Code changes applied").strip()
            return True, reason
        return False, ""

    def _parse_resolve_actions(self, text: str) -> list[int]:
        """Parse RESOLVE_ACTION blocks from agent output. Returns list of memory IDs."""
        pattern = r"```RESOLVE_ACTION\s*\n(.*?)```"
        matches = re.findall(pattern, text, re.DOTALL)

        ids = []
        for block in matches:
            fields = self._parse_block_fields(block)
            raw_id = fields.get("id", "").strip()
            if raw_id.isdigit():
                ids.append(int(raw_id))
        return ids

    async def _process_resolve_actions(self, memory_ids: list[int]) -> None:
        """Mark the given action item memory IDs as resolved."""
        for mid in memory_ids:
            try:
                await self.memory.resolve_action_item(mid)
                logger.info(f"Resolved action item #{mid}")
            except Exception:
                logger.exception(f"Failed to resolve action item #{mid}")

    def _strip_structured_blocks(self, text: str) -> str:
        text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```FILE_CREATED\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```RESTART_REQUIRED\s*\n.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"```RESOLVE_ACTION\s*\n.*?```", "", text, flags=re.DOTALL)
        return text.strip()

    @staticmethod
    def _parse_block_fields(block: str) -> dict[str, str]:
        fields = {}
        for line in block.strip().split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        return fields
