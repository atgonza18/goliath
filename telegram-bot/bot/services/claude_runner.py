import asyncio
import logging
import os

from bot.config import REPO_ROOT

logger = logging.getLogger(__name__)

# Timeout for Claude CLI operations (seconds)
CLAUDE_TIMEOUT = 300  # 5 minutes


async def run_claude_analysis(user_prompt: str, timeout: float = CLAUDE_TIMEOUT) -> str:
    """
    Run a Claude CLI analysis asynchronously.

    Uses ``claude --print`` for non-interactive, single-shot output.
    The Claude instance reads Claude.md from the repo root automatically
    and has access to the entire project file tree.
    """
    full_prompt = (
        f"The user (a DSC analyst) asks via Telegram:\n\n"
        f"{user_prompt}\n\n"
        f"Respond concisely (Telegram has a 4096 char message limit). "
        f"Use markdown formatting. Be specific with data and file references."
    )

    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--output-format", "text",
        full_prompt,
    ]

    logger.info(f"Invoking Claude CLI (timeout={timeout}s)")
    logger.debug(f"Prompt: {full_prompt[:200]}...")

    # Clear CLAUDECODE env var so the subprocess doesn't think it's nested
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        if process.returncode != 0:
            error_msg = stderr.decode(errors="replace").strip()
            logger.error(f"Claude CLI failed (rc={process.returncode}): {error_msg}")
            return f"Claude analysis failed:\n```\n{error_msg[:1000]}\n```"

        result = stdout.decode(errors="replace").strip()
        if not result:
            return "Claude returned an empty response. Try rephrasing your question."

        logger.info(f"Claude response: {len(result)} chars")
        return result

    except asyncio.TimeoutError:
        logger.warning(f"Claude CLI timed out after {timeout}s")
        try:
            process.kill()
            await process.wait()
        except Exception:
            pass
        raise

    except FileNotFoundError:
        logger.error("Claude CLI not found in PATH")
        return "Error: Claude CLI (`claude`) is not available in this environment."

    except Exception as e:
        logger.exception("Unexpected error running Claude CLI")
        return f"Unexpected error: {str(e)[:500]}"
