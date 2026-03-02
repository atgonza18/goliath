"""Agent runner using the Claude Agent SDK.

Drop-in replacement for SubagentRunner (runner.py) that uses the Agent SDK
instead of ``claude --print`` subprocess calls. This enables:

- Full multi-step tool access (Read, Write, Bash, etc.)
- Token/cost tracking via ResultMessage
- Session management and resumption

Activated by setting the ``AGENT_RUNNER_BACKEND=sdk`` environment variable.
When the env var is absent or set to ``cli``, the legacy SubagentRunner is used.
"""

import asyncio
import logging
import os
import time

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    ResultMessage,
    AssistantMessage,
    TextBlock,
    CLINotFoundError,
    CLIConnectionError,
    CLIJSONDecodeError,
    ProcessError,
    ClaudeSDKError,
)

from bot.agents.definitions import AgentDefinition
from bot.agents.resilience import (
    RetryConfig,
    compute_backoff,
    get_circuit_breaker,
    is_transient_error,
)
from bot.agents.runner import AgentResult  # Reuse the exact same dataclass
from bot.config import REPO_ROOT
from bot.memory.token_tracker import TokenUsage

logger = logging.getLogger(__name__)

# Module-level token tracker instance, set by main.py at startup.
# When None, token logging is silently skipped (never blocks agent work).
_token_tracker = None


def set_token_tracker(tracker) -> None:
    """Called once at bot startup to enable token tracking."""
    global _token_tracker
    _token_tracker = tracker


class SubagentRunnerSDK:
    """Invokes the Claude Agent SDK for a specific agent definition.

    Drop-in replacement for SubagentRunner that uses the Agent SDK
    instead of ``claude --print`` subprocess calls.

    Resilience features:
    - Exponential backoff with jitter on transient errors
    - Circuit breaker: backs off for 5 min after 3 consecutive failures
    - Configurable via env vars (see config.py)
    """

    # Safety-net timeout ONLY -- catches genuinely hung zombie processes.
    # Mirrors the same constant from SubagentRunner for consistency.
    DEFAULT_TIMEOUT = 3600  # 1 hour -- zombie safety net only

    def __init__(self):
        # Build a clean env dict once, stripping CLAUDECODE to avoid
        # nested-session errors (same as the CLI runner).
        self._env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
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
        """Execute an agent via the Claude Agent SDK with retry and circuit breaker.

        Interface is identical to ``SubagentRunner.run()`` so the
        orchestrator can swap runners transparently.
        """
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

            # Transient error -- retry if attempts remain
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
        """Execute a single Claude Agent SDK call for the given agent."""
        full_prompt = task_prompt
        if context:
            full_prompt = f"{context}\n\n---\n\nTASK:\n{task_prompt}"

        # Build SDK options ------------------------------------------------
        options = ClaudeAgentOptions(
            system_prompt=agent.system_prompt,
            cwd=str(REPO_ROOT),
            env=self._env,
        )

        if no_tools:
            # No tool access -- give the agent an empty tool list.
            options.tools = []
        else:
            # Full tool access -- equivalent to --dangerously-skip-permissions
            options.permission_mode = "bypassPermissions"

        logger.info(
            f"Running subagent '{agent.name}' via SDK "
            f"(no_tools={no_tools}, safety-net={timeout}s)"
        )
        start = time.monotonic()

        try:
            result_text = ""
            observed_model = None

            async def _execute():
                nonlocal result_text, observed_model
                final_result = None
                texts: list[str] = []

                async for message in query(prompt=full_prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        if observed_model is None and message.model:
                            observed_model = message.model
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                texts.append(block.text)
                    elif isinstance(message, ResultMessage):
                        final_result = message

                # Prefer the authoritative ResultMessage.result when present;
                # fall back to concatenated assistant text blocks otherwise.
                if final_result and final_result.result:
                    result_text = final_result.result
                elif texts:
                    result_text = "\n".join(texts)

                return final_result

            final = await asyncio.wait_for(_execute(), timeout=timeout)
            elapsed = time.monotonic() - start

            # --- Token tracking (fire-and-forget, never blocks) ---
            if final is not None:
                await self._log_token_usage(
                    agent_name=agent.name,
                    result_message=final,
                    model=observed_model,
                    task_prompt=task_prompt,
                )

            # Check for SDK-reported errors
            if final and final.is_error:
                logger.error(
                    f"Subagent '{agent.name}' SDK reported error: "
                    f"{result_text[:200]}"
                )
                return AgentResult(
                    agent_name=agent.name,
                    success=False,
                    output="",
                    duration_seconds=elapsed,
                    error=result_text[:1000],
                )

            logger.info(
                f"Subagent '{agent.name}' completed via SDK in {elapsed:.1f}s "
                f"({len(result_text)} chars)"
            )
            return AgentResult(
                agent_name=agent.name,
                success=True,
                output=result_text,
                duration_seconds=elapsed,
            )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            logger.error(
                f"Subagent '{agent.name}' SDK timeout after {elapsed:.1f}s"
            )
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=(
                    f"Timed out after {timeout}s (zombie safety net)."
                ),
            )

        except CLINotFoundError:
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=0.0,
                error="Claude CLI not found in PATH",
            )

        except (CLIConnectionError, CLIJSONDecodeError, ProcessError) as e:
            elapsed = time.monotonic() - start
            logger.exception(f"Subagent '{agent.name}' SDK transport error")
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=str(e)[:500],
            )

        except ClaudeSDKError as e:
            elapsed = time.monotonic() - start
            logger.exception(f"Subagent '{agent.name}' SDK error")
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=str(e)[:500],
            )

        except Exception as e:
            elapsed = time.monotonic() - start
            error_str = str(e)[:500]
            # The SDK's query.py raises generic Exception for CLI process
            # failures (e.g. "Command failed with exit code 1").  Log at
            # WARNING instead of EXCEPTION to avoid scary tracebacks for
            # what is really a transient CLI failure.
            if "exit code" in error_str or "Command failed" in error_str:
                logger.warning(
                    f"Subagent '{agent.name}' CLI process failure "
                    f"(transient, will retry): {error_str[:200]}"
                )
            else:
                logger.exception(f"Subagent '{agent.name}' unexpected error")
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=error_str,
            )

    @staticmethod
    async def _log_token_usage(
        agent_name: str,
        result_message,
        model: str = None,
        task_prompt: str = "",
    ) -> None:
        """Log token usage from a ResultMessage. Never raises."""
        if _token_tracker is None:
            return
        try:
            usage = TokenUsage.from_sdk_result(
                agent_name=agent_name,
                result_message=result_message,
                model=model,
                task_summary=task_prompt[:200] if task_prompt else None,
            )
            await _token_tracker.log_usage(usage)
            logger.debug(
                f"Token usage logged for '{agent_name}': "
                f"{usage.input_tokens}in/{usage.output_tokens}out "
                f"(${usage.cost_usd or 0:.4f})"
            )
        except Exception:
            logger.debug(f"Failed to log token usage for '{agent_name}'", exc_info=True)
