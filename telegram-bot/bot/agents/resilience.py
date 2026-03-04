"""Retry, backoff, and circuit breaker for agent runners.

Provides:
- Exponential backoff with jitter for transient errors
- Circuit breaker: after N consecutive failures, back off for a cooldown
  period instead of hammering the API
- Error classification: transient vs. permanent

Used by both SubagentRunner (CLI) and SubagentRunnerSDK.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

# SDK exception types that are transient (worth retrying)
_TRANSIENT_SDK_EXCEPTIONS: tuple[str, ...] = (
    "CLIConnectionError",
    "CLIJSONDecodeError",
    "ProcessError",
)

# SDK AssistantMessage error types that are transient
_TRANSIENT_MESSAGE_ERRORS: set[str] = {"rate_limit", "server_error"}

# Substrings in error messages that indicate transient failures
_TRANSIENT_ERROR_SUBSTRINGS: tuple[str, ...] = (
    "Timed out",
    "timed out",
    "timeout",
    "rate limit",
    "rate_limit",
    "429",
    "502",
    "503",
    "504",
    "server_error",
    "ECONNREFUSED",
    "ECONNRESET",
    "EPIPE",
    "Connection refused",
    "Connection reset",
    "Broken pipe",
    "zombie safety net",
    # CLI process failures — always worth retrying.  The bundled Claude CLI
    # can exit 1 (or get SIGKILLed → -9) for transient reasons (API hiccup,
    # resource pressure, etc.).  The SDK wraps these as a generic Exception
    # with "Command failed with exit code N" — treat them all as transient.
    "Command failed with exit code",
    "exit code",
    "Check stderr output for details",
)

# Substrings that indicate auth/token expiry — recoverable via token refresh.
# These are NOT permanent: a token refresh + retry should fix them.
_AUTH_ERROR_SUBSTRINGS: tuple[str, ...] = (
    "authentication_error",
    "authentication_failed",
    "OAuth token has expired",
    "token has expired",
    "401",
    "unauthorized",
    "Unauthorized",
    "invalid_token",
    "token_expired",
)

# Substrings that indicate permanent failures (never retry)
_PERMANENT_ERROR_SUBSTRINGS: tuple[str, ...] = (
    "CLI not found",
    "not found in PATH",
    "billing_error",
    "invalid_request",
    "Unknown agent",
)


def is_transient_error(error_msg: str | None, exception: Exception | None = None) -> bool:
    """Determine if an error is transient and worth retrying.

    Args:
        error_msg: The error string from AgentResult.error
        exception: The original exception, if available

    Returns:
        True if the error is transient (retry makes sense)
    """
    if error_msg is None and exception is None:
        return False

    # Check exception type name against known transient SDK exceptions
    if exception is not None:
        exc_type = type(exception).__name__
        if exc_type in _TRANSIENT_SDK_EXCEPTIONS:
            return True

    if error_msg:
        # Permanent errors are never transient
        for substr in _PERMANENT_ERROR_SUBSTRINGS:
            if substr in error_msg:
                return False

        # Check for transient error patterns
        for substr in _TRANSIENT_ERROR_SUBSTRINGS:
            if substr in error_msg:
                return True

    # Auth errors are transient IF we can refresh the token
    if error_msg:
        for substr in _AUTH_ERROR_SUBSTRINGS:
            if substr in error_msg:
                logger.info(f"Auth error detected — will attempt token refresh before retry: {error_msg[:200]}")
                return True

    # DEFAULT TO TRANSIENT: If an error doesn't match any permanent pattern,
    # retry it.  The cost of a wasted retry is low; the cost of giving up on
    # a transient failure and bricking the bot for 5 min is high.  Only
    # explicitly-permanent errors (billing, CLI-not-found) skip retry.
    if error_msg:
        logger.info(f"Unknown error pattern — defaulting to TRANSIENT (will retry): {error_msg[:200]}")
        return True

    return False


def is_auth_error(error_msg: str | None) -> bool:
    """Check if an error is specifically an auth/token error.

    Used by runners to trigger token refresh before retrying.
    """
    if not error_msg:
        return False
    for substr in _AUTH_ERROR_SUBSTRINGS:
        if substr in error_msg:
            return True
    return False


async def attempt_auth_recovery(error_msg: str) -> bool:
    """Attempt to recover from an auth error by refreshing the OAuth token.

    Called by runners between retries when an auth error is detected.
    If refresh succeeds, the next retry should work with the new token.

    Returns:
        True if recovery succeeded (caller should retry).
        False if recovery failed (caller may still retry — the token
        refresh might succeed on a later attempt).
    """
    if not is_auth_error(error_msg):
        return False

    try:
        from bot.services.token_health import get_token_health_monitor
        monitor = get_token_health_monitor()
        logger.warning("Auth error detected — triggering reactive token refresh")
        success = await monitor.try_refresh_now(reason="reactive_401")
        if success:
            logger.info("Reactive token refresh succeeded — retry should work")
        else:
            logger.warning("Reactive token refresh failed — retry may still fail")
        return success
    except Exception as e:
        logger.error(f"Auth recovery failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Backoff calculation
# ---------------------------------------------------------------------------

def compute_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    """Compute exponential backoff delay with optional jitter.

    Uses the "full jitter" algorithm from AWS:
    delay = random(0, min(max_delay, base_delay * 2^attempt))

    Args:
        attempt: Zero-based attempt number (0 = first retry)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        jitter: Whether to add random jitter

    Returns:
        Delay in seconds before the next retry
    """
    exp_delay = min(max_delay, base_delay * (2 ** attempt))
    if jitter:
        return random.uniform(0, exp_delay)
    return exp_delay


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

@dataclass
class _CircuitState:
    """Per-agent circuit breaker state."""
    consecutive_failures: int = 0
    last_failure_time: float = 0.0
    cooldown_until: float = 0.0


class CircuitBreaker:
    """Per-agent circuit breaker.

    After `failure_threshold` consecutive failures for a given agent,
    the circuit "opens" and rejects calls for `cooldown_seconds`.
    After the cooldown, one call is allowed through ("half-open").
    If it succeeds, the circuit resets. If it fails, the cooldown restarts.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._states: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def check(self, agent_name: str) -> tuple[bool, float]:
        """Check if the circuit allows a call.

        Returns:
            (allowed, wait_seconds) -- if not allowed, wait_seconds is how
            long until the cooldown expires.
        """
        async with self._lock:
            state = self._states.get(agent_name)
            if state is None:
                return True, 0.0

            now = time.monotonic()
            if state.cooldown_until > now:
                remaining = state.cooldown_until - now
                return False, remaining

            # Cooldown expired -- allow one attempt (half-open)
            return True, 0.0

    async def record_success(self, agent_name: str) -> None:
        """Record a successful call. Resets the circuit."""
        async with self._lock:
            if agent_name in self._states:
                del self._states[agent_name]

    async def record_failure(self, agent_name: str) -> None:
        """Record a failed call. May open the circuit."""
        async with self._lock:
            if agent_name not in self._states:
                self._states[agent_name] = _CircuitState()

            state = self._states[agent_name]
            state.consecutive_failures += 1
            state.last_failure_time = time.monotonic()

            if state.consecutive_failures >= self.failure_threshold:
                state.cooldown_until = time.monotonic() + self.cooldown_seconds
                logger.warning(
                    f"Circuit breaker OPEN for '{agent_name}': "
                    f"{state.consecutive_failures} consecutive failures. "
                    f"Cooling down for {self.cooldown_seconds}s."
                )

    def get_status(self) -> dict[str, dict]:
        """Return circuit breaker status for all tracked agents."""
        now = time.monotonic()
        status = {}
        for name, state in self._states.items():
            if state.cooldown_until > now:
                circuit_state = "open"
            elif state.consecutive_failures > 0:
                circuit_state = "half-open"
            else:
                circuit_state = "closed"
            status[name] = {
                "state": circuit_state,
                "consecutive_failures": state.consecutive_failures,
                "cooldown_remaining": max(0, state.cooldown_until - now),
            }
        return status


# ---------------------------------------------------------------------------
# Module-level singleton (shared across both runners)
# ---------------------------------------------------------------------------

# Initialized lazily on first import; runners access via get_circuit_breaker()
_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get or create the module-level circuit breaker singleton."""
    global _circuit_breaker
    if _circuit_breaker is None:
        from bot.config import (
            RETRY_CIRCUIT_BREAKER_THRESHOLD,
            RETRY_CIRCUIT_BREAKER_COOLDOWN,
        )
        _circuit_breaker = CircuitBreaker(
            failure_threshold=RETRY_CIRCUIT_BREAKER_THRESHOLD,
            cooldown_seconds=RETRY_CIRCUIT_BREAKER_COOLDOWN,
        )
    return _circuit_breaker


@dataclass
class RetryConfig:
    """Retry configuration loaded from bot.config."""
    max_attempts: int = 3
    base_delay: float = 2.0
    max_delay: float = 30.0
    jitter: bool = True

    @classmethod
    def from_config(cls) -> "RetryConfig":
        """Load retry config from bot.config module."""
        from bot.config import (
            RETRY_MAX_ATTEMPTS,
            RETRY_BASE_DELAY,
            RETRY_MAX_DELAY,
            RETRY_JITTER,
        )
        return cls(
            max_attempts=RETRY_MAX_ATTEMPTS,
            base_delay=RETRY_BASE_DELAY,
            max_delay=RETRY_MAX_DELAY,
            jitter=RETRY_JITTER,
        )
