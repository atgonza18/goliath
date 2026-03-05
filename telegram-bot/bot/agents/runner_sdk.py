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
    HookMatcher,
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
    is_auth_error,
    attempt_auth_recovery,
)
from bot.agents.runner import AgentResult  # Reuse the exact same dataclass
from bot.config import REPO_ROOT, AGENT_MODEL
from bot.memory.token_tracker import TokenUsage

logger = logging.getLogger(__name__)

# Dedicated logger for structured tool-call events — writes to bot.log in JSON.
# Consumers can filter on logger name "goliath.tool_events" to get a clean
# stream of every tool invocation across all agent runs.
_tool_event_logger = logging.getLogger("goliath.tool_events")

# Module-level token tracker instance, set by main.py at startup.
# When None, token logging is silently skipped (never blocks agent work).
_token_tracker = None


def _build_tool_hooks(agent_name: str, on_tool_event=None) -> dict:
    """Build PreToolUse / PostToolUse hook matchers for real-time observability.

    Returns a hooks dict ready for ClaudeAgentOptions.  Each hook callback
    logs a structured JSON event via the goliath.tool_events logger so that
    tool calls are visible in real-time in bot.log (not just in the final
    ResultMessage summary).

    When ``on_tool_event`` is provided, tool events are also forwarded to
    the callback for real-time streaming to the web UI.

    The hooks are observation-only — they always return ``continue_: True``
    and never block execution.
    """

    async def _pre_tool_hook(hook_input, tool_use_id, context):
        """Log tool invocation just before Claude executes it."""
        try:
            tool_name = hook_input.get("tool_name", "unknown") if isinstance(hook_input, dict) else getattr(hook_input, "tool_name", "unknown")
            tool_input = hook_input.get("tool_input", {}) if isinstance(hook_input, dict) else getattr(hook_input, "tool_input", {})
            _tool_event_logger.info(
                f"tool_start agent={agent_name} tool={tool_name}",
                extra={
                    "agent": agent_name,
                    "operation": "tool_start",
                    "tool_name": tool_name,
                    # Truncate large inputs (e.g. file content writes) to keep logs readable
                    "tool_input_preview": str(tool_input)[:300] if tool_input else None,
                },
            )
            if on_tool_event:
                await on_tool_event({
                    "type": "tool_start",
                    "tool": tool_name,
                    "input_preview": str(tool_input)[:200] if tool_input else None,
                })
        except Exception:
            pass  # Never let hook logging crash the agent
        return {"continue_": True}

    async def _post_tool_hook(hook_input, tool_use_id, context):
        """Log tool completion after Claude receives the result."""
        try:
            tool_name = hook_input.get("tool_name", "unknown") if isinstance(hook_input, dict) else getattr(hook_input, "tool_name", "unknown")
            _tool_event_logger.info(
                f"tool_done agent={agent_name} tool={tool_name}",
                extra={
                    "agent": agent_name,
                    "operation": "tool_done",
                    "tool_name": tool_name,
                },
            )
            if on_tool_event:
                await on_tool_event({"type": "tool_done", "tool": tool_name})
        except Exception:
            pass  # Never let hook logging crash the agent
        return {"continue_": True}

    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[_pre_tool_hook])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[_post_tool_hook])],
    }


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

    # Operational timeout: kills a subagent that has been running too long.
    # Mirrors the same value as SubagentRunner (CLI runner) for consistency.
    # After timeout: SDK query is cancelled, error is logged, graceful error
    # is returned to the user so synthesis can continue with other agents.
    OPERATIONAL_TIMEOUT = 720  # 12 minutes — kills hung subagents

    # Zombie safety-net: absolute backstop, mirrors SubagentRunner.
    DEFAULT_TIMEOUT = 3600  # 1 hour — zombie safety net only

    def __init__(self):
        # Build a clean env dict once, stripping CLAUDECODE to avoid
        # nested-session errors (same as the CLI runner).
        self._env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        # Hetzner NAT drops TCP connections idle for 300+ seconds.
        # Claude Opus extended thinking causes silent API connections with no
        # data flow.  After 300s Hetzner drops the NAT state, Node.js CLI gets
        # ECONNRESET, self-terminates with SIGTERM, Python sees exit code -15.
        # LD_PRELOAD + libkeepalive forces SO_KEEPALIVE on all TCP sockets so
        # the Linux kernel sends keep-alive probes every 60s (see sysctl
        # net.ipv4.tcp_keepalive_time), keeping the NAT entry alive.
        _libkeepalive = "/opt/goliath/lib/libkeepalive.so.0"
        if os.path.exists(_libkeepalive):
            self._env["LD_PRELOAD"] = _libkeepalive

        self._retry_config = RetryConfig.from_config()
        self._circuit_breaker = get_circuit_breaker()

    async def run(
        self,
        agent: AgentDefinition,
        task_prompt: str,
        context: str = "",
        timeout: float = None,
        no_tools: bool = False,
        on_text_chunk=None,
        on_tool_event=None,
    ) -> AgentResult:
        """Execute an agent via the Claude Agent SDK with retry and circuit breaker.

        Interface is identical to ``SubagentRunner.run()`` so the
        orchestrator can swap runners transparently.
        """
        # Resolve timeout: explicit call param > agent definition > operational default.
        # Never fall back to the zombie safety net for per-run calls.
        timeout = timeout or agent.timeout or self.OPERATIONAL_TIMEOUT

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
            result = await self._run_once(agent, task_prompt, context, timeout, no_tools, on_text_chunk=on_text_chunk, on_tool_event=on_tool_event)

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
            # This handles the case where the access token expired mid-operation.
            if is_auth_error(result.error):
                logger.warning(
                    f"Subagent '{agent.name}' hit auth error — "
                    f"attempting token refresh before retry"
                )
                await attempt_auth_recovery(result.error)

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
        on_text_chunk=None,
        on_tool_event=None,
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

        # Model selection: per-agent override > global default
        # Heavy agents (constraints, construction, scheduling, cost, transcript)
        # use Opus; everything else uses Sonnet.
        effective_model = agent.model or AGENT_MODEL

        # GAP H3: Apply learned routing override if available
        try:
            from bot.agents.model_router import get_global_router
            _router = get_global_router()
            if _router and effective_model:
                from bot.memory.reliability_log import classify_task_type
                task_type = classify_task_type(task_prompt[:200] if task_prompt else "")
                effective_model = await _router.get_effective_model(
                    agent_name=agent.name,
                    task_type=task_type,
                    base_model=effective_model,
                )
        except Exception:
            pass  # Never let routing lookup crash the agent

        if effective_model:
            options.model = effective_model

        # Effort level: controls extended thinking depth per agent.
        # "max" for deep analytical work, "high" for coding/extraction,
        # None lets the SDK use its default.
        if agent.effort:
            options.effort = agent.effort

        if no_tools:
            # No tool access -- give the agent an empty tool list.
            options.tools = []
        else:
            # Full tool access -- equivalent to --dangerously-skip-permissions
            options.permission_mode = "bypassPermissions"

        # Wire up real-time tool observability hooks.
        # PreToolUse + PostToolUse callbacks log every tool call to the
        # "goliath.tool_events" structured logger.  They are observation-only
        # and never block execution.
        options.hooks = _build_tool_hooks(agent.name, on_tool_event=on_tool_event)

        logger.info(
            f"Running subagent '{agent.name}' via SDK "
            f"(model={effective_model or 'default'}, effort={agent.effort or 'default'}, "
            f"no_tools={no_tools}, safety-net={timeout}s)"
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
                                if on_text_chunk:
                                    try:
                                        await on_text_chunk(block.text)
                                    except Exception:
                                        pass  # Never let streaming callback crash the agent
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

            # --- GAP H3: Record routing outcome ---
            try:
                from bot.agents.model_router import get_global_router
                from bot.memory.reliability_log import classify_task_type
                _router = get_global_router()
                if _router:
                    _task_type = classify_task_type(task_prompt[:200] if task_prompt else "")
                    _is_error = bool(final and final.is_error)
                    await _router.record_outcome(
                        agent_name=agent.name,
                        model_used=effective_model or observed_model or "unknown",
                        success=not _is_error,
                        latency_ms=int(elapsed * 1000),
                        task_type=_task_type,
                    )
            except Exception:
                pass  # Never let outcome recording crash the agent

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
            timeout_minutes = int(timeout // 60)
            logger.error(
                f"Subagent '{agent.name}' SDK timed out after {elapsed:.1f}s "
                f"(limit={timeout}s / {timeout_minutes}min) — cancelling."
            )
            return AgentResult(
                agent_name=agent.name,
                success=False,
                output="",
                duration_seconds=elapsed,
                error=(
                    f"Agent '{agent.name}' timed out after {timeout_minutes} minutes. "
                    f"The SDK query was cancelled. This usually means the task was too "
                    f"large or the agent got stuck. Try breaking the request into smaller pieces."
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
