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
        timeout = timeout or agent.timeout

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

        logger.info(f"Running subagent '{agent.name}' (timeout={timeout}s)")
        start = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(REPO_ROOT),
                env=self._env,
            )

            if timeout:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            else:
                stdout, stderr = await process.communicate()

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
            logger.warning(f"Subagent '{agent.name}' timed out after {elapsed:.1f}s")
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
                error=f"Timed out after {timeout}s",
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
