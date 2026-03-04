"""OAuth Token Health Monitor — proactive refresh, monitoring, and Telegram re-auth.

This module solves the March 2, 2026 outage where the bot was unreachable for 3+ hours
because the OAuth access token expired and Anthropic's refresh endpoint was down.

Three layers of defense:
  1. PROACTIVE REFRESH: Auto-refresh the access token when it's within 2 hours of
     expiring. Runs every 30 minutes via the scheduler. The token never reaches
     its actual expiration if Anthropic's endpoint is healthy.

  2. REACTIVE REFRESH: When the resilience layer detects a 401/auth error,
     it calls try_refresh_now() immediately — don't wait for the scheduled check.

  3. MANUAL RE-AUTH: /reauth command sends an OAuth URL to Telegram. User taps it,
     logs in, gets a code, pastes it back. Bot exchanges the code for fresh tokens.
     This is the nuclear option when both the refresh token and access token are dead.

Notifications:
  - Token refreshed successfully (quiet — logged, not messaged)
  - Token expiring soon + refresh failed → ALERT to Telegram
  - Token expired → CRITICAL ALERT with /reauth instructions
  - Refresh endpoint down → WARNING with retry schedule
"""

import asyncio
import json
import logging
import os
import time
import secrets
import hashlib
import base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")

# Claude OAuth endpoints (reverse-engineered from Claude Code CLI)
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# Credentials file location
CREDENTIALS_PATH = Path(os.path.expanduser("~/.claude/.credentials.json"))

# Refresh thresholds
REFRESH_AHEAD_SECONDS = 7200  # Refresh when token expires within 2 hours
CRITICAL_THRESHOLD_SECONDS = 1800  # 30 min — send critical alert
CHECK_INTERVAL_SECONDS = 1800  # Check every 30 minutes

# Retry settings for refresh attempts
MAX_REFRESH_RETRIES = 3
REFRESH_RETRY_DELAY = 30  # seconds between retries

# PKCE state for /reauth flow
_pending_reauth: dict = {}  # Stores {code_verifier, state} for active reauth flow


class TokenHealthMonitor:
    """Monitors and manages Claude OAuth token lifecycle.

    Attributes:
        bot: Telegram Bot instance for sending notifications
        chat_id: Telegram chat ID for sending alerts
        _last_alert_time: Prevents alert spam (max 1 alert per 15 min)
        _consecutive_refresh_failures: Tracks refresh endpoint health
        _last_successful_refresh: When we last successfully refreshed
    """

    def __init__(self, bot=None, chat_id: Optional[int] = None):
        self.bot = bot
        self.chat_id = chat_id
        self._last_alert_time: float = 0
        self._consecutive_refresh_failures: int = 0
        self._last_successful_refresh: Optional[datetime] = None
        self._refresh_lock = asyncio.Lock()

    def set_bot(self, bot, chat_id: int):
        """Set bot and chat_id after initialization (for late binding)."""
        self.bot = bot
        self.chat_id = chat_id

    # ------------------------------------------------------------------
    # Credential I/O
    # ------------------------------------------------------------------

    def _read_credentials(self) -> Optional[dict]:
        """Read the Claude credentials file."""
        try:
            if not CREDENTIALS_PATH.exists():
                logger.error(f"Credentials file not found: {CREDENTIALS_PATH}")
                return None
            data = json.loads(CREDENTIALS_PATH.read_text())
            return data.get("claudeAiOauth")
        except Exception as e:
            logger.error(f"Failed to read credentials: {e}")
            return None

    def _write_credentials(self, oauth_data: dict) -> bool:
        """Write updated OAuth data back to credentials file.

        Preserves the full file structure, only updates claudeAiOauth.
        Creates a backup before writing.
        """
        try:
            # Read current file
            current = {}
            if CREDENTIALS_PATH.exists():
                current = json.loads(CREDENTIALS_PATH.read_text())

            # Backup
            backup_path = CREDENTIALS_PATH.with_suffix(".json.bak")
            if CREDENTIALS_PATH.exists():
                backup_path.write_text(CREDENTIALS_PATH.read_text())

            # Update
            current["claudeAiOauth"] = oauth_data
            CREDENTIALS_PATH.write_text(json.dumps(current))
            logger.info("Credentials file updated successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to write credentials: {e}")
            return False

    # ------------------------------------------------------------------
    # Token status
    # ------------------------------------------------------------------

    def get_token_status(self) -> dict:
        """Get current token health status.

        Returns:
            {
                "healthy": bool,
                "access_token_prefix": str (first 20 chars),
                "expires_at": datetime or None,
                "expires_in_seconds": int,
                "expires_in_human": str,
                "status": "healthy" | "expiring_soon" | "critical" | "expired" | "unknown",
                "has_refresh_token": bool,
                "consecutive_refresh_failures": int,
                "last_successful_refresh": datetime or None,
            }
        """
        creds = self._read_credentials()
        if not creds:
            return {
                "healthy": False,
                "status": "unknown",
                "error": "Cannot read credentials file",
            }

        expires_at_ms = creds.get("expiresAt", 0)
        expires_at = datetime.fromtimestamp(expires_at_ms / 1000, tz=CT) if expires_at_ms else None
        now = datetime.now(CT)

        if expires_at:
            expires_in = (expires_at - now).total_seconds()
        else:
            expires_in = -1

        # Determine status
        if expires_in < 0:
            status = "expired"
            healthy = False
        elif expires_in < CRITICAL_THRESHOLD_SECONDS:
            status = "critical"
            healthy = False
        elif expires_in < REFRESH_AHEAD_SECONDS:
            status = "expiring_soon"
            healthy = True  # Still working, but needs refresh
        else:
            status = "healthy"
            healthy = True

        # Human-readable time remaining
        if expires_in > 0:
            hours = int(expires_in // 3600)
            minutes = int((expires_in % 3600) // 60)
            human = f"{hours}h {minutes}m"
        else:
            human = "EXPIRED"

        access_token = creds.get("accessToken", "")
        return {
            "healthy": healthy,
            "access_token_prefix": access_token[:20] + "..." if access_token else "none",
            "expires_at": expires_at,
            "expires_in_seconds": int(expires_in),
            "expires_in_human": human,
            "status": status,
            "has_refresh_token": bool(creds.get("refreshToken")),
            "consecutive_refresh_failures": self._consecutive_refresh_failures,
            "last_successful_refresh": self._last_successful_refresh,
        }

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    async def try_refresh_now(self, reason: str = "scheduled") -> bool:
        """Attempt to refresh the access token immediately.

        Thread-safe via asyncio lock. If another refresh is in progress,
        waits for it rather than double-refreshing.

        Args:
            reason: Why we're refreshing ("scheduled", "reactive_401", "manual")

        Returns:
            True if refresh succeeded, False otherwise.
        """
        async with self._refresh_lock:
            return await self._do_refresh(reason)

    async def _do_refresh(self, reason: str) -> bool:
        """Internal refresh implementation. Must be called under _refresh_lock."""
        creds = self._read_credentials()
        if not creds:
            logger.error("Cannot refresh — credentials file unreadable")
            return False

        refresh_token = creds.get("refreshToken")
        if not refresh_token:
            logger.error("Cannot refresh — no refresh token in credentials")
            await self._send_alert(
                "CRITICAL: No refresh token found. Manual re-auth required.\n"
                "Send /reauth to get a login link.",
                critical=True,
            )
            return False

        # Attempt refresh with retries
        for attempt in range(MAX_REFRESH_RETRIES):
            try:
                logger.info(
                    f"Token refresh attempt {attempt + 1}/{MAX_REFRESH_RETRIES} "
                    f"(reason: {reason})"
                )
                result = await self._call_token_endpoint(refresh_token)

                if result:
                    # Update credentials with new tokens
                    new_creds = dict(creds)  # Copy existing
                    new_creds["accessToken"] = result["access_token"]
                    if "refresh_token" in result:
                        new_creds["refreshToken"] = result["refresh_token"]
                    if "expires_in" in result:
                        new_creds["expiresAt"] = int(
                            (time.time() + result["expires_in"]) * 1000
                        )

                    if self._write_credentials(new_creds):
                        self._consecutive_refresh_failures = 0
                        self._last_successful_refresh = datetime.now(CT)
                        logger.info(
                            f"Token refreshed successfully (reason: {reason}). "
                            f"New expiry: {new_creds.get('expiresAt')}"
                        )

                        # Quiet notification — only log, don't spam Telegram
                        # unless we were in a failure streak
                        if self._consecutive_refresh_failures > 0:
                            await self._send_alert(
                                f"Token refresh recovered after "
                                f"{self._consecutive_refresh_failures} failures. "
                                f"All systems nominal.",
                                critical=False,
                            )
                        return True
                    else:
                        logger.error("Token refresh succeeded but failed to write credentials")
                        return False

            except Exception as e:
                logger.warning(
                    f"Token refresh attempt {attempt + 1} failed: {e}"
                )
                if attempt < MAX_REFRESH_RETRIES - 1:
                    await asyncio.sleep(REFRESH_RETRY_DELAY)

        # All retries failed
        self._consecutive_refresh_failures += 1
        logger.error(
            f"Token refresh FAILED after {MAX_REFRESH_RETRIES} attempts "
            f"(consecutive failures: {self._consecutive_refresh_failures})"
        )

        status = self.get_token_status()
        if status["status"] in ("expired", "critical"):
            await self._send_alert(
                f"CRITICAL: Token is {status['status'].upper()} "
                f"(expires in: {status['expires_in_human']}) and refresh endpoint "
                f"is not responding.\n\n"
                f"Refresh failures: {self._consecutive_refresh_failures}\n"
                f"Send /reauth to manually re-authenticate.",
                critical=True,
            )
        elif status["status"] == "expiring_soon":
            await self._send_alert(
                f"WARNING: Token expires in {status['expires_in_human']} and "
                f"auto-refresh failed ({self._consecutive_refresh_failures} "
                f"consecutive failures).\n"
                f"Will keep retrying. If this persists, send /reauth.",
                critical=False,
            )

        return False

    async def _call_token_endpoint(self, refresh_token: str) -> Optional[dict]:
        """Call Anthropic's OAuth token endpoint to refresh the access token.

        Uses aiohttp for async HTTP. Falls back to subprocess curl if aiohttp
        is not available.

        Returns:
            Dict with access_token, refresh_token, expires_in on success.
            None on failure.
        """
        import aiohttp

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TOKEN_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info("Token endpoint returned 200 OK")
                        return data
                    else:
                        body = await resp.text()
                        logger.error(
                            f"Token endpoint returned {resp.status}: {body[:500]}"
                        )
                        return None
        except asyncio.TimeoutError:
            logger.error("Token endpoint timed out (30s)")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Token endpoint connection error: {e}")
            return None

    # ------------------------------------------------------------------
    # Scheduled health check (called by scheduler)
    # ------------------------------------------------------------------

    async def scheduled_check(self, scheduler=None) -> None:
        """Periodic health check — runs every 30 minutes via scheduler.

        Logic:
        1. Read token expiry
        2. If expiring within REFRESH_AHEAD_SECONDS → attempt refresh
        3. If refresh fails → send appropriate alert
        4. If token is healthy → do nothing (quiet)
        """
        status = self.get_token_status()
        logger.info(
            f"Token health check: status={status['status']}, "
            f"expires_in={status.get('expires_in_human', 'unknown')}, "
            f"has_refresh={status.get('has_refresh_token', False)}"
        )

        if status["status"] == "healthy":
            # All good — check if we should proactively refresh anyway
            # (within 2 hours of expiry but not yet "expiring_soon")
            expires_in = status.get("expires_in_seconds", 0)
            if expires_in < REFRESH_AHEAD_SECONDS:
                logger.info("Token expiring within 2 hours — proactive refresh")
                await self.try_refresh_now(reason="proactive")
            return

        if status["status"] in ("expiring_soon", "critical", "expired"):
            logger.warning(f"Token status: {status['status']} — attempting refresh")
            success = await self.try_refresh_now(reason="scheduled")

            if not success and status["status"] == "expired":
                # Token is dead and we can't refresh. Send critical alert.
                await self._send_alert(
                    "CRITICAL: OAuth token is EXPIRED and auto-refresh failed.\n\n"
                    "The bot cannot process messages until the token is refreshed.\n"
                    "Send /reauth to manually re-authenticate from Telegram.",
                    critical=True,
                )

    # ------------------------------------------------------------------
    # /reauth flow — manual OAuth from Telegram
    # ------------------------------------------------------------------

    def generate_reauth_url(self) -> tuple[str, str]:
        """Generate an OAuth authorization URL for manual re-auth.

        Uses PKCE (Proof Key for Code Exchange) for security since we're
        a public client (no client secret).

        Returns:
            (auth_url, state) — the URL to send to the user, and the state
            parameter for verification.
        """
        global _pending_reauth

        # Generate PKCE code verifier and challenge
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()

        state = secrets.token_urlsafe(32)

        # Store for later exchange
        _pending_reauth = {
            "code_verifier": code_verifier,
            "state": state,
            "created_at": time.time(),
        }

        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": "https://claude.ai/oauth/callback",
            "scope": "user:inference user:profile user:sessions:claude_code",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{AUTHORIZE_URL}?{urlencode(params)}"
        return auth_url, state

    async def exchange_auth_code(self, code: str) -> bool:
        """Exchange an authorization code for tokens (final step of /reauth).

        Args:
            code: The authorization code from the OAuth callback.

        Returns:
            True if exchange succeeded and credentials updated.
        """
        global _pending_reauth

        if not _pending_reauth:
            logger.error("No pending reauth session — user needs to start /reauth first")
            return False

        # Check if the reauth session is expired (15 min max)
        if time.time() - _pending_reauth["created_at"] > 900:
            _pending_reauth = {}
            logger.error("Reauth session expired (>15 min)")
            return False

        code_verifier = _pending_reauth["code_verifier"]

        import aiohttp

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
            "redirect_uri": "https://claude.ai/oauth/callback",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TOKEN_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        creds = self._read_credentials() or {}
                        creds["accessToken"] = data["access_token"]
                        if "refresh_token" in data:
                            creds["refreshToken"] = data["refresh_token"]
                        if "expires_in" in data:
                            creds["expiresAt"] = int(
                                (time.time() + data["expires_in"]) * 1000
                            )
                        if "scope" in data:
                            creds["scopes"] = data["scope"].split()

                        if self._write_credentials(creds):
                            self._consecutive_refresh_failures = 0
                            self._last_successful_refresh = datetime.now(CT)
                            _pending_reauth = {}
                            logger.info("Manual re-auth succeeded — new tokens written")
                            return True
                        else:
                            logger.error("Re-auth: token exchange OK but write failed")
                            return False
                    else:
                        body = await resp.text()
                        logger.error(f"Re-auth token exchange failed: {resp.status}: {body[:500]}")
                        return False
        except Exception as e:
            logger.error(f"Re-auth token exchange error: {e}")
            return False

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    async def _send_alert(self, message: str, critical: bool = False) -> None:
        """Send a Telegram alert about token health.

        Rate-limited: max 1 alert per 15 minutes (unless critical).
        Critical alerts always send.
        """
        now = time.time()

        if not critical and (now - self._last_alert_time < 900):
            logger.info(f"Alert suppressed (rate limit): {message[:100]}")
            return

        if not self.bot or not self.chat_id:
            logger.warning(f"Cannot send alert (no bot/chat_id): {message[:100]}")
            return

        prefix = "🔴 <b>TOKEN ALERT</b>" if critical else "⚠️ <b>Token Warning</b>"
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"{prefix}\n\n{message}",
                parse_mode="HTML",
            )
            self._last_alert_time = now
            logger.info(f"Token alert sent: {message[:100]}")
        except Exception as e:
            logger.error(f"Failed to send token alert: {e}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_monitor: Optional[TokenHealthMonitor] = None


def get_token_health_monitor() -> TokenHealthMonitor:
    """Get or create the module-level TokenHealthMonitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = TokenHealthMonitor()
    return _monitor


def set_token_health_monitor(monitor: TokenHealthMonitor) -> None:
    """Set the module-level monitor (called from main.py during init)."""
    global _monitor
    _monitor = monitor
