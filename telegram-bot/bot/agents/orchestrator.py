import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

from bot.agents.definitions import NIMROD, CONSTRAINTS_MANAGER
from bot.agents.registry import AgentRegistry
from bot.agents.runner import SubagentRunner, AgentResult
from bot.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Limit concurrent Claude subprocesses — bumped to 8 for parallel request handling.
# Multiple user messages can now run concurrently, each spawning subagents.
_semaphore = asyncio.Semaphore(8)


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

        # --- CONSTRAINTS SYNC: auto-dispatch if transcript had constraints ---
        # Runs concurrently with synthesis to save time.
        sync_task = None
        if constraints_sync_data:
            logger.info(
                f"Transcript contained {len(constraints_sync_data['constraints'])} "
                f"constraint(s) — launching ConstraintsPro sync"
            )
            sync_task = asyncio.create_task(
                self._dispatch_constraints_sync(constraints_sync_data)
            )

        # --- PASS 2: Nimrod synthesis (proceeds even if some subagents failed) ---
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

        # --- AWAIT CONSTRAINTS SYNC (if running) ---
        sync_summary_text = ""
        if sync_task is not None:
            try:
                sync_result = await sync_task
                if sync_result and sync_result.success and sync_result.output:
                    summary = self._parse_sync_summary(sync_result.output)
                    if summary:
                        sync_summary_text = f"\n\n---\n{summary}"
                    # Record sync in activity log
                    if self._subagent_log is not None:
                        self._subagent_log.append({
                            "agent": "constraints_manager (auto-sync)",
                            "success": sync_result.success,
                            "duration": round(sync_result.duration_seconds, 1),
                            "error": sync_result.error,
                        })
                    logger.info("ConstraintsPro sync completed successfully")
                elif sync_result and not sync_result.success:
                    logger.warning(f"ConstraintsPro sync failed: {sync_result.error}")
                    sync_summary_text = (
                        "\n\n---\n<b>ConstraintsPro Sync</b>: "
                        "Sync attempted but failed. Constraints from this transcript "
                        "were not pushed to ConstraintsPro."
                    )
            except Exception:
                logger.exception("ConstraintsPro sync task raised an exception")

        # Deduplicate file paths while preserving order
        seen = set()
        unique_files = []
        for fp in all_file_paths:
            if fp not in seen:
                seen.add(fp)
                unique_files.append(fp)

        final_text = self._strip_structured_blocks(synthesis_text) + sync_summary_text

        return OrchestrationResult(
            text=final_text,
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

    # --- Constraints Sync Pipeline ---

    def _parse_constraints_sync(self, text: str) -> Optional[dict]:
        """Parse CONSTRAINTS_SYNC block from transcript_processor output.

        Returns dict with 'project' and 'constraints' (list) or None.
        """
        pattern = r"```CONSTRAINTS_SYNC\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return None

        block = match.group(1)

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

    async def _dispatch_constraints_sync(
        self, sync_data: dict, source_description: str = "meeting transcript"
    ) -> Optional[AgentResult]:
        """Auto-dispatch constraints_manager to sync extracted constraints to ConstraintsPro.

        This handles:
        - Deduplication against existing ConstraintsPro data
        - Creating new constraints
        - Updating existing constraints with meeting notes
        - Closing resolved constraints
        - Priority assessment (HIGH/MEDIUM/LOW)
        """
        constraints = sync_data["constraints"]
        project = sync_data.get("project", "unknown")

        constraints_json = json.dumps(constraints, indent=2)

        prompt = f"""\
You are syncing constraints extracted from a {source_description} to ConstraintsPro. \
Follow these steps carefully:

STEP 1: Call `projects_list` to get all project IDs and names.

STEP 2: For each constraint below, find the matching project by name/key and call \
`constraints_list_by_project` to get ALL existing constraints for that project.

STEP 3: DEDUPLICATION — For each extracted constraint, compare against existing constraints:
- Match by SEMANTIC SIMILARITY, not exact text. If an existing constraint covers the same \
issue (same blocker, same material, same vendor problem, etc.), it is a MATCH even if worded differently.
- If you find a match:
  a) If the extracted constraint has resolved=true, update the existing constraint's status \
to "Resolved" using `constraints_update_status`.
  b) Otherwise, add a note using `constraints_add_note` with the meeting discussion: \
"[Meeting Update] {{status_discussed}}. Commitments: {{commitments}}"
  c) If the extracted priority differs from the existing priority AND the extracted priority \
is higher, update the priority using `constraints_update`.
- If NO match exists AND resolved is false, CREATE a new constraint (Step 4).

STEP 4: For NEW constraints (no existing match, not resolved), create using `constraints_create`:
- Match project ID from Step 1
- title: Concise title (first ~80 chars of description)
- description: Full description
- priority: Assess as HIGH (blocks critical path, safety risk, or imminent deadline), \
MEDIUM (impacts schedule within 2-4 weeks), or LOW (tracking item, no immediate impact). \
Use the priority from the transcript as a starting point but adjust based on your assessment.
- category: As specified (CONSTRUCTION/PROCUREMENT/ENGINEERING/PERMITTING/OTHER)
- needByDate: As specified (or omit if null)
- owner: As specified
Then add a note: "Auto-synced from {source_description}. Discussion: {{status_discussed}}"

STEP 5: For constraints marked resolved=true that MATCHED an existing constraint, \
update status to "Resolved" and add a closing note: \
"Confirmed resolved in {source_description}. {{status_discussed}}"

EXTRACTED CONSTRAINTS TO SYNC:
{constraints_json}

IMPORTANT:
- This is an AUTHORIZED WRITE operation — you are explicitly instructed to create/update/close.
- NEVER create duplicates. When in doubt, add a note to the existing constraint instead.
- Prioritize accuracy: HIGH = critical path/safety/imminent, MEDIUM = schedule impact, LOW = tracking.

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
            f"Auto-dispatching constraints_manager for sync — "
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
                f"Constraints sync completed in {result.duration_seconds:.1f}s"
            )
        else:
            logger.error(
                f"Constraints sync failed: {result.error}"
            )

        return result

    def _parse_sync_summary(self, text: str) -> Optional[str]:
        """Extract SYNC_SUMMARY block from constraints_manager output
        and format it as an HTML notification for the user."""
        pattern = r"```SYNC_SUMMARY\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return None

        fields = self._parse_block_fields(match.group(1))
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

        parts = ["<b>ConstraintsPro Sync</b> (auto from transcript)"]
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
        text = re.sub(r"```SYNC_SUMMARY\s*\n.*?```", "", text, flags=re.DOTALL)
        return text.strip()

    @staticmethod
    def _parse_block_fields(block: str) -> dict[str, str]:
        fields = {}
        for line in block.strip().split("\n"):
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        return fields
