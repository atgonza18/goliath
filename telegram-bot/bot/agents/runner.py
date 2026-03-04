import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from bot.agents.definitions import AgentDefinition
from bot.agents.resilience import (
    RetryConfig,
    compute_backoff,
    get_circuit_breaker,
    is_transient_error,
    is_auth_error,
    attempt_auth_recovery,
)
from bot.config import REPO_ROOT, AGENT_MODEL
from bot.memory.token_tracker import TokenUsage

logger = logging.getLogger(__name__)

# Module-level token tracker instance, set by main.py at startup.
# When None, token logging is silently skipped.
_token_tracker = None


def set_token_tracker(tracker) -> None:
    """Called once at bot startup to enable token tracking."""
    global _token_tracker
    _token_tracker = tracker


@dataclass
class AgentResult:
    agent_name: str
    success: bool
    output: str
    duration_seconds: float
    error: Optional[str] = None


def get_runner():
    """Get the configured agent runner backend.

    Returns SubagentRunnerSDK when AGENT_RUNNER_BACKEND=sdk,
    otherwise returns the legacy CLI-based SubagentRunner.
    Lazy-imports to avoid breaking if the SDK package is absent.
    """
    from bot.config import AGENT_RUNNER_BACKEND
    if AGENT_RUNNER_BACKEND == "sdk":
        from bot.agents.runner_sdk import SubagentRunnerSDK
        return SubagentRunnerSDK()
    return SubagentRunner()


class SubagentRunner:
    """Invokes a Claude CLI subprocess for a specific agent definition.

    Resilience features:
    - Exponential backoff with jitter on transient errors
    - Circuit breaker: backs off for 5 min after 3 consecutive failures
    - Configurable via env vars (see config.py)
    """

    # Safety-net timeout ONLY — catches genuinely hung zombie processes.
    # This should NEVER fire during normal operation. Claude CLI has no
    # natural timeout; it runs until the task is done or context exhausts.
    # We do NOT use this as an operational constraint — just zombie cleanup.
    # Previous values (300s, 900s) were killing productive work. Never again.
    DEFAULT_TIMEOUT = 3600  # 1 hour — zombie safety net only

    def __init__(self):
        self._env = os.environ.copy()
        self._env.pop("CLAUDECODE", None)
        self._retry_config = RetryConfig.from_config()
        self._circuit_breaker = get_circuit_breaker()

    async def run(
        self,
        agent: AgentDefinition,
        task_prompt: str,
        context: str = "",
        timeout: float = None,
        no_tools: bool = False,
    ) -> AgentResult:
        # Resolve timeout: explicit call param > agent definition > default
        timeout = timeout or agent.timeout or self.DEFAULT_TIMEOUT

        # --- Circuit breaker check ---
        allowed, wait_secs = await self._circuit_breaker.check(agent.name)
        if not allowed:
            logger.warning(
                f"Circuit breaker OPEN for '{agent.name}': "
                f"skipping call, {wait_secs:.0f}s remaining in cooldown."
            )
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=0.0,
                error=(
                    f"Circuit breaker open: '{agent.name}' has failed repeatedly. "
                    f"Cooling down for {wait_secs:.0f}s before retrying."
                ),
            )

        # --- Retry loop with exponential backoff ---
        rc = self._retry_config
        last_result: AgentResult | None = None

        for attempt in range(rc.max_attempts):
            result = await self._run_once(agent, task_prompt, context, timeout, no_tools)

            if result.success:
                await self._circuit_breaker.record_success(agent.name)
                return result

            last_result = result

            # Classify the error
            if not is_transient_error(result.error):
                logger.info(
                    f"Subagent '{agent.name}' failed with permanent error "
                    f"(attempt {attempt + 1}/{rc.max_attempts}): {result.error}"
                )
                await self._circuit_breaker.record_failure(agent.name)
                return result

            # If this is an auth error, attempt token refresh before retry.
            if is_auth_error(result.error):
                logger.warning(
                    f"Subagent '{agent.name}' hit auth error — "
                    f"attempting token refresh before retry"
                )
                await attempt_auth_recovery(result.error)

            # Transient error — retry if attempts remain
            if attempt + 1 < rc.max_attempts:
                delay = compute_backoff(
                    attempt, rc.base_delay, rc.max_delay, rc.jitter
                )
                logger.info(
                    f"Subagent '{agent.name}' transient failure "
                    f"(attempt {attempt + 1}/{rc.max_attempts}): {result.error}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.warning(
                    f"Subagent '{agent.name}' exhausted all {rc.max_attempts} attempts. "
                    f"Last error: {result.error}"
                )

        # All attempts exhausted
        await self._circuit_breaker.record_failure(agent.name)
        if last_result is not None:
            last_result.error = (
                f"Failed after {rc.max_attempts} attempts. "
                f"Last error: {last_result.error}"
            )
        return last_result

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

        # Model selection: per-agent override > global default
        effective_model = agent.model or AGENT_MODEL
        if effective_model:
            cmd.extend(["--model", effective_model])

        # no_tools mode: omit --dangerously-skip-permissions so Claude has no tool access
        if not no_tools:
            cmd.append("--dangerously-skip-permissions")

        cmd.append(full_prompt)

        logger.info(f"Running subagent '{agent.name}' (model={effective_model or 'default'}, safety-net={timeout}s)")
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

            # --- Token tracking (CLI: duration only, no token counts) ---
            await self._log_cli_usage(
                agent_name=agent.name,
                elapsed_ms=int(elapsed * 1000),
                task_prompt=task_prompt,
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

    @staticmethod
    async def _log_cli_usage(
        agent_name: str,
        elapsed_ms: int,
        task_prompt: str = "",
    ) -> None:
        """Log a CLI call. Token counts are unavailable in text output mode.

        Still valuable: tracks call frequency, duration, and agent name so
        we can identify which agents to migrate to the SDK for full tracking.
        """
        if _token_tracker is None:
            return
        try:
            usage = TokenUsage(
                agent_name=agent_name,
                duration_ms=elapsed_ms,
                task_summary=task_prompt[:200] if task_prompt else None,
                backend="cli",
            )
            await _token_tracker.log_usage(usage)
            logger.debug(
                f"Token usage logged for '{agent_name}' (CLI, no token counts, "
                f"{elapsed_ms}ms)"
            )
        except Exception:
            logger.debug(f"Failed to log token usage for '{agent_name}'", exc_info=True)
