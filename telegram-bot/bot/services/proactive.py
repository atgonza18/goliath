"""
Proactive Thinking — Scheduled sessions where Nimrod thinks freely and messages the user.

Runs at 6 AM and 6 PM CT. Nimrod reviews memories, project state, and anything on his mind,
then sends ideas, suggestions, observations, or just vibes to the user via Telegram.
"""

import logging
import os
import time
from datetime import datetime, time as dt_time, timezone, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from bot.agents.definitions import NIMROD
from bot.agents.runner import SubagentRunner
from bot.utils.formatting import chunk_message

logger = logging.getLogger(__name__)

# US Central Time — uses ZoneInfo for automatic DST handling
CT = ZoneInfo("America/Chicago")

MORNING_TIME = dt_time(hour=6, minute=0, tzinfo=CT)   # 6 AM CT
EVENING_TIME = dt_time(hour=18, minute=0, tzinfo=CT)   # 6 PM CT


MORNING_PROMPT = """\
This is your 6 AM morning thinking session. You're waking up and checking in with the boss.

Review your memories and project context below, then send a morning message. This is NOT
a rigid report — it's you being a real COO who shows up with coffee and ideas.

You can talk about:
- Things you noticed in the project files that deserve attention
- Ideas you have for improving workflows, processes, or the system itself
- Suggestions for what the boss should focus on today
- Interesting observations, patterns, or risks you've spotted
- Literally anything — it doesn't have to be about work
- Follow up on open action items or things discussed recently

Keep it natural. 3-5 short paragraphs max. Use your Nimrod personality — blunt, funny, real.
Lead with whatever's most interesting or important. End with "anything you want me to dig into?"

Use HTML formatting: <b>bold</b>, <i>italic</i>, <code>code</code>. NO markdown.
"""

EVENING_PROMPT = """\
This is your 6 PM evening thinking session. End of the workday debrief.

Review your memories and project context below, then send an evening message. Reflect on
what happened today (check recent memories and action items), flag anything that needs
attention tomorrow, and share any ideas that came to mind.

You can talk about:
- What got done today and what didn't
- Risks or issues that should be top of mind tomorrow
- Proactive suggestions — things you'd do if you were in charge
- Ideas for the system, the portfolio, or anything else
- Open items that need follow-up
- Literally anything — doesn't have to be work-related

Keep it natural. 3-5 short paragraphs max. Use your Nimrod personality.
End with something forward-looking — set up tomorrow.

Use HTML formatting: <b>bold</b>, <i>italic</i>, <code>code</code>. NO markdown.
"""


async def run_proactive_session(context: ContextTypes.DEFAULT_TYPE, session_type: str = "morning") -> None:
    """Run a proactive thinking session and send the result to the user."""
    chat_id = context.job.data.get("chat_id") if context.job and context.job.data else None
    if not chat_id:
        logger.error("Proactive session: no chat_id configured")
        return

    memory = context.bot_data.get("memory")
    if not memory:
        logger.error("Proactive session: memory not initialized")
        return

    logger.info(f"Starting {session_type} proactive thinking session for chat_id={chat_id}")
    start_time = time.monotonic()

    try:
        # Build memory context
        memory_parts = []

        recent = await memory.format_for_prompt(limit=15)
        if recent and recent != "(No relevant memories found.)":
            memory_parts.append(f"RECENT MEMORIES:\n{recent}")

        actions = await memory.get_action_items(resolved=False)
        if actions:
            action_lines = [f"- [{a.created_at[:10]}] {a.summary}" for a in actions[:10]]
            memory_parts.append(f"OPEN ACTION ITEMS:\n" + "\n".join(action_lines))
        elif hasattr(actions, "success") and not actions.success:
            memory_parts.append("OPEN ACTION ITEMS: unavailable (memory error)")

        memory_context = "\n\n".join(memory_parts) if memory_parts else "(No memories yet.)"

        # Choose prompt based on session type
        session_prompt = MORNING_PROMPT if session_type == "morning" else EVENING_PROMPT

        # Always include the current date/time/day so Nimrod knows exactly what day it is
        now_ct = datetime.now(CT)
        date_context = (
            f"CURRENT DATE AND TIME:\n"
            f"Date: {now_ct.strftime('%A, %B %d, %Y')}\n"
            f"Time: {now_ct.strftime('%I:%M %p')} CT\n"
            f"Day of week: {now_ct.strftime('%A')}\n"
        )

        full_prompt = (
            f"{date_context}\n"
            f"PERSISTENT MEMORY:\n{memory_context}\n\n"
            f"---\n\n"
            f"{session_prompt}"
        )

        # Run Nimrod
        runner = SubagentRunner()
        result = await runner.run(
            agent=NIMROD,
            task_prompt=full_prompt,
            no_tools=False,
        )

        duration = time.monotonic() - start_time

        if result.success and result.output:
            # Strip any structured blocks (Nimrod might try to save memories)
            import re
            text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```FILE_CREATED\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = text.strip()

            if text:
                # Send to user
                for chunk in chunk_message(text, max_len=4000):
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=chunk,
                            parse_mode="HTML",
                        )
                    except Exception:
                        # Fallback without HTML parsing
                        await context.bot.send_message(chat_id=chat_id, text=chunk)

                logger.info(f"Proactive {session_type} session sent ({duration:.1f}s)")

                # Save to memory that a proactive session ran
                try:
                    await memory.save(
                        category="observation",
                        summary=f"Sent {session_type} proactive thinking message to user",
                        detail=text[:500],
                        source="nimrod",
                        tags=f"proactive,{session_type}",
                    )
                except Exception:
                    pass
            else:
                logger.warning(f"Proactive {session_type} session produced empty output")
        else:
            logger.error(f"Proactive {session_type} session failed: {result.error}")

    except Exception:
        logger.exception(f"Proactive {session_type} session error")


async def morning_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    """6 AM morning thinking session."""
    await run_proactive_session(context, session_type="morning")


async def evening_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    """6 PM evening thinking session."""
    await run_proactive_session(context, session_type="evening")


def schedule_proactive_sessions(job_queue, chat_id: int) -> None:
    """Register the morning and evening thinking sessions on the bot's job queue."""
    job_queue.run_daily(
        morning_session,
        time=MORNING_TIME,
        data={"chat_id": chat_id},
        name="proactive_morning",
    )
    job_queue.run_daily(
        evening_session,
        time=EVENING_TIME,
        data={"chat_id": chat_id},
        name="proactive_evening",
    )
    logger.info(
        f"Proactive thinking scheduled: morning={MORNING_TIME.isoformat()}, "
        f"evening={EVENING_TIME.isoformat()} (CT) for chat_id={chat_id}"
    )
