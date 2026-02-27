import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from bot.agents.definitions import AgentDefinition
from bot.config import REPO_ROOT

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    agent_name: str
    success: bool
    output: str
    duration_seconds: float
    error: Optional[str] = None


class SubagentRunner:
    """Invokes a Claude CLI subprocess for a specific agent definition."""

    # Safety-net timeout ONLY — catches genuinely hung zombie processes.
    # This should NEVER fire during normal operation. Claude CLI has no
    # natural timeout; it runs until the task is done or context exhausts.
    # We do NOT use this as an operational constraint — just zombie cleanup.
    # Previous values (300s, 900s) were killing productive work. Never again.
    DEFAULT_TIMEOUT = 3600  # 1 hour — zombie safety net only

    def __init__(self):
        self._env = os.environ.copy()
        self._env.pop("CLAUDECODE", None)

    async def run(
        self,
        agent: AgentDefinition,
        task_prompt: str,
        context: str = "",
        timeout: float = None,
        no_tools: bool = False,
    ) -> AgentResult:
        # Resolve timeout: explicit call param > agent definition > default 900s
        timeout = timeout or agent.timeout or self.DEFAULT_TIMEOUT

        result = await self._run_once(agent, task_prompt, context, timeout, no_tools)

        # Retry once on timeout — transient slow-starts are common with Claude CLI
        if not result.success and result.error and "Timed out" in result.error:
            logger.info(
                f"Retrying subagent '{agent.name}' after timeout "
                f"(attempt 2/2, timeout={timeout}s)"
            )
            result = await self._run_once(agent, task_prompt, context, timeout, no_tools)
            if not result.success and result.error and "Timed out" in result.error:
                # Both attempts timed out — return graceful error so orchestrator
                # can continue with partial results from other subagents
                result.error = (
                    f"Timed out after 2 attempts ({timeout}s each). "
                    f"This subagent's results are unavailable."
                )

        return result

    async def _run_once(
        self,
        agent: AgentDefinition,
        task_prompt: str,
        context: str = "",
        timeout: float = 900,
        no_tools: bool = False,
    ) -> AgentResult:
        """Execute a single Claude CLI subprocess for the given agent."""
        full_prompt = task_prompt
        if context:
            full_prompt = f"{context}\n\n---\n\nTASK:\n{task_prompt}"

        cmd = [
            "claude",
            "--print",
            "--output-format", "text",
            "--system-prompt", agent.system_prompt,
        ]

        # no_tools mode: omit --dangerously-skip-permissions so Claude has no tool access
        if not no_tools:
            cmd.append("--dangerously-skip-permissions")

        cmd.append(full_prompt)

        logger.info(f"Running subagent '{agent.name}' (safety-net={timeout}s)")
        start = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(REPO_ROOT),
                env=self._env,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )

            elapsed = time.monotonic() - start

            if process.returncode != 0:
                error_msg = stderr.decode(errors="replace").strip()
                logger.error(
                    f"Subagent '{agent.name}' failed (rc={process.returncode}): {error_msg[:200]}"
                )
                return AgentResult(
                    agent_name=agent.name,
                    success=False,
                    output="",
                    duration_seconds=elapsed,
                    error=error_msg[:1000],
                )

            result = stdout.decode(errors="replace").strip()
            logger.info(
                f"Subagent '{agent.name}' completed in {elapsed:.1f}s ({len(result)} chars)"
            )
            return AgentResult(
                agent_name=agent.name,
                success=True,
                output=result,
                duration_seconds=elapsed,
            )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            logger.error(
                f"Subagent '{agent.name}' hit ZOMBIE SAFETY NET after {elapsed:.1f}s — "
                f"this should never happen in normal operation. The process was likely "
                f"truly hung. Killing it."
            )
            try:
                process.kill()
                await process.wait()
            except Exception:
                pass
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=f"Process appeared hung and was killed after {timeout}s (zombie safety net). This is unusual — please report.",
            )

        except FileNotFoundError:
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=0.0,
                error="Claude CLI not found in PATH",
            )

        except Exception as e:
            elapsed = time.monotonic() - start
            logger.exception(f"Subagent '{agent.name}' unexpected error")
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=str(e)[:500],
            )
