"""Teams Meeting Auto-Detector — intercepts pasted meeting invites in Telegram messages,
parses meeting details, and auto-schedules a Recall.ai bot to join.

This module runs in the message pipeline BEFORE the orchestrator, so Nimrod doesn't
have to manually recognize meeting invites. When a user pastes a Teams invite, this:

1. Detects Teams meeting URLs (both /l/meetup-join/ and /meet/ patterns)
2. Parses meeting time from the surrounding text (natural language + structured formats)
3. Schedules the Recall.ai bot to join 1 minute before start time
4. Returns a confirmation message that gets sent immediately to the user
5. Passes the original message through to the orchestrator with context injected

Edge cases handled:
- Past meetings (>2h ago): skipped with explanation
- Missing time info: bot joins immediately
- Already-scheduled bots for same URL: detected and reported
- Malformed URLs: gracefully ignored
- Multiple URLs in one message: first one wins (rare scenario)
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import PROJECTS, match_project_key

CT = ZoneInfo("America/Chicago")
logger = logging.getLogger(__name__)

# ── Teams URL Patterns ──────────────────────────────────────────────────────
# Pattern 1: Classic /l/meetup-join/ URLs (older style, still common in email invites)
#   https://teams.microsoft.com/l/meetup-join/19%3ameeting_...
#
# Pattern 2: Short /meet/ URLs (newer style, used in recent Teams invites)
#   https://teams.microsoft.com/meet/2239030137332?p=...
#
# Pattern 3: teams.live.com redirect URLs
#   https://teams.live.com/meet/...
TEAMS_URL_PATTERN = re.compile(
    r'https?://(?:teams\.microsoft\.com/(?:l/meetup-join|meet)/|teams\.live\.com/meet/)[^\s<>"\']+',
    re.IGNORECASE,
)

# ── Time Parsing Patterns ───────────────────────────────────────────────────
# These handle the common formats Josh pastes from Teams/Outlook invites.

# Full date-time: "March 2, 2026 2:30 PM", "Mar 2, 2026 at 2:30 PM"
DATE_LONG = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)

# Numeric date: "3/2/2026", "03-02-2026"
DATE_NUMERIC = re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})")

# Time: "2:30 PM", "14:30", "2:30PM"
TIME_12H = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)", re.IGNORECASE)
TIME_24H = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\s*(?:AM|PM|am|pm))")

# "When:" / "Start:" / "Date & Time:" prefix line from invites
WHEN_LINE = re.compile(
    r"(?:when|start(?:s)?|date\s*&?\s*time|scheduled)\s*[:]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# Day-of-week detection (helps confirm correct date)
DAY_OF_WEEK = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)",
    re.IGNORECASE,
)

# "today", "tomorrow", "this afternoon" etc.
RELATIVE_DATE = re.compile(
    r"\b(today|tomorrow|this\s+(?:morning|afternoon|evening))\b",
    re.IGNORECASE,
)

# Common timezone markers we handle (all map to CT for this portfolio)
TZ_MARKERS = re.compile(
    r"\b(CST|CDT|CT|Central\s+(?:Standard\s+)?Time|Central)\b",
    re.IGNORECASE,
)

MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sep": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}


class TeamsMeetingDetector:
    """Detects Teams meeting invites in Telegram messages and auto-schedules Recall bots."""

    def __init__(self, recall_service):
        """
        Args:
            recall_service: RecallService instance for scheduling bots.
        """
        self._recall = recall_service

    def detect_teams_url(self, text: str) -> Optional[str]:
        """Extract a Teams meeting URL from a text message.

        Returns the first Teams URL found, or None.
        """
        match = TEAMS_URL_PATTERN.search(text)
        return match.group(0).rstrip(".,;)") if match else None

    def parse_meeting_time(self, text: str) -> Optional[datetime]:
        """Parse a meeting start time from the message text.

        Tries multiple strategies:
        1. Look for "When:" / "Start:" prefixed lines (most reliable)
        2. Look for explicit date + time anywhere in text
        3. Look for relative dates ("today", "tomorrow") + time
        4. If only time is found, assume today

        Returns a timezone-aware datetime in CT, or None if no time found.
        """
        now = datetime.now(CT)

        # Strategy 1: "When:" / "Start:" line
        when_match = WHEN_LINE.search(text)
        if when_match:
            when_text = when_match.group(1)
            result = self._parse_datetime_from_text(when_text, now)
            if result:
                return result

        # Strategy 2: Look for date + time in the full text
        result = self._parse_datetime_from_text(text, now)
        if result:
            return result

        # Strategy 3: Relative date + time
        rel_match = RELATIVE_DATE.search(text)
        if rel_match:
            rel_word = rel_match.group(1).lower()
            base_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if "tomorrow" in rel_word:
                base_date += timedelta(days=1)
            # "today" / "this morning/afternoon/evening" = today

            time_val = self._parse_time(text)
            if time_val:
                hour, minute = time_val
                return base_date.replace(hour=hour, minute=minute, tzinfo=CT)

        # Strategy 4: Time only -> assume today
        time_val = self._parse_time(text)
        if time_val:
            hour, minute = time_val
            result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # If the time already passed today, it might be for tomorrow
            # But for meeting invites pasted right before the meeting, "today" is more likely
            return result

        return None

    def _parse_datetime_from_text(self, text: str, now: datetime) -> Optional[datetime]:
        """Try to extract a full date + time from a text string."""
        date_part = self._parse_date(text, now)
        time_val = self._parse_time(text)

        if date_part and time_val:
            hour, minute = time_val
            return date_part.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=CT)
        elif date_part:
            # Have date but no time -- not enough for scheduling
            return None
        return None

    def _parse_date(self, text: str, now: datetime) -> Optional[datetime]:
        """Parse a date from text. Returns a naive datetime (date only, hour=0)."""
        # Try long date first: "March 2, 2026"
        long_match = DATE_LONG.search(text)
        if long_match:
            month_str = long_match.group(1)
            day = int(long_match.group(2))
            year = int(long_match.group(3))
            month = MONTH_MAP.get(month_str.lower())
            if month:
                try:
                    return datetime(year, month, day)
                except ValueError:
                    pass

        # Try numeric date: "3/2/2026"
        num_match = DATE_NUMERIC.search(text)
        if num_match:
            month = int(num_match.group(1))
            day = int(num_match.group(2))
            year = int(num_match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    return datetime(year, month, day)
                except ValueError:
                    pass

        return None

    def _parse_time(self, text: str) -> Optional[tuple[int, int]]:
        """Parse a time from text. Returns (hour_24h, minute) or None."""
        # Try 12-hour time first: "2:30 PM"
        match_12 = TIME_12H.search(text)
        if match_12:
            hour = int(match_12.group(1))
            minute = int(match_12.group(2))
            ampm = match_12.group(3).upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return (hour, minute)

        # Try 24-hour time: "14:30"
        match_24 = TIME_24H.search(text)
        if match_24:
            hour = int(match_24.group(1))
            minute = int(match_24.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return (hour, minute)

        return None

    def extract_meeting_name(self, text: str) -> str:
        """Try to extract a meeting title/name from the message.

        Looks for:
        - First line of the message (often the subject)
        - "Subject:" prefixed line
        - Project name match
        """
        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        if not lines:
            return "Teams Meeting"

        # Check for "Subject:" line
        for line in lines:
            if line.lower().startswith("subject:"):
                return line[len("subject:"):].strip() or "Teams Meeting"

        # Check for project name in the text
        project_key = match_project_key(text)
        if project_key:
            project_name = PROJECTS[project_key]["name"]
            return f"{project_name} Meeting"

        # Use first non-URL line as title (often the subject)
        for line in lines:
            if "teams.microsoft.com" not in line.lower() and len(line) > 3:
                # Truncate long lines
                title = line[:80]
                # Don't use lines that look like metadata
                if not any(line.lower().startswith(p) for p in [
                    "when:", "start:", "date:", "time:", "join", "meeting id:",
                    "passcode:", "dial-in", "http", "____",
                ]):
                    return title

        return "Teams Meeting"

    async def check_already_scheduled(self, meeting_url: str) -> Optional[dict]:
        """Check if a Recall bot is already scheduled for this meeting URL.

        Returns the existing bot info if found, None otherwise.
        """
        if not self._recall:
            return None
        try:
            active_bots = await self._recall.get_active_bots()
            for bot in active_bots:
                if bot.get("meeting_url") == meeting_url:
                    return bot
            # Also check recent bots (last 20)
            recent_bots = await self._recall.get_recent_bots(limit=20)
            for bot in recent_bots:
                if bot.get("meeting_url") == meeting_url and not bot.get("error"):
                    return bot
        except Exception:
            logger.debug("Failed to check for existing bots", exc_info=True)
        return None

    async def process_message(self, text: str, chat_id: int) -> Optional[dict]:
        """Main entry point: check a message for Teams meeting URLs and auto-schedule.

        Args:
            text: The raw message text from Telegram
            chat_id: Telegram chat ID for the Recall bot to report back to

        Returns:
            None if no Teams URL found.
            Dict with results if a Teams URL was detected:
            {
                "teams_url": str,
                "meeting_name": str,
                "meeting_time": datetime or None,
                "scheduled": bool,
                "bot_id": str or None,
                "join_at": str or None,
                "error": str or None,
                "already_scheduled": bool,
                "confirmation_message": str,
            }
        """
        # Step 1: Detect Teams URL
        teams_url = self.detect_teams_url(text)
        if not teams_url:
            return None

        logger.info(f"Teams meeting URL detected: {teams_url[:80]}...")

        # Step 2: Parse meeting details
        meeting_name = self.extract_meeting_name(text)
        meeting_time = self.parse_meeting_time(text)
        now = datetime.now(CT)

        result = {
            "teams_url": teams_url,
            "meeting_name": meeting_name,
            "meeting_time": meeting_time,
            "scheduled": False,
            "bot_id": None,
            "join_at": None,
            "error": None,
            "already_scheduled": False,
            "confirmation_message": "",
        }

        # Step 3: Check if Recall is configured
        if not self._recall or not self._recall.is_configured:
            result["error"] = "Recall.ai not configured"
            result["confirmation_message"] = (
                "I detected a Teams meeting link but Recall.ai is not configured. "
                "Add RECALL_API_KEY to .env to enable automatic meeting recording."
            )
            return result

        # Step 4: Check if already scheduled
        existing = await self.check_already_scheduled(teams_url)
        if existing:
            result["already_scheduled"] = True
            bot_id = existing.get("bot_id", "unknown")
            result["bot_id"] = bot_id
            result["confirmation_message"] = (
                f"Meeting bot already scheduled for this meeting.\n"
                f"Bot ID: {bot_id[:8]}... | Status: {existing.get('status', 'unknown')}"
            )
            logger.info(f"Bot already scheduled for {teams_url[:50]}: {bot_id}")
            return result

        # Step 5: Check if meeting is in the past
        if meeting_time and meeting_time < now - timedelta(hours=2):
            result["error"] = "Meeting already ended (>2 hours ago)"
            time_str = meeting_time.strftime("%b %d at %I:%M %p CT")
            result["confirmation_message"] = (
                f"I detected a Teams meeting link for {meeting_name}, "
                f"but it was scheduled for {time_str} -- that's more than 2 hours ago. "
                f"Skipping bot scheduling."
            )
            logger.info(f"Skipping past meeting: {meeting_name} at {time_str}")
            return result

        # Step 6: Determine join_at time
        join_at = None
        join_description = "joining now"

        if meeting_time:
            if meeting_time > now + timedelta(minutes=2):
                # Future meeting: schedule for 1 min before
                join_time = meeting_time - timedelta(minutes=1)
                join_at = join_time.isoformat()
                time_str = meeting_time.strftime("%I:%M %p CT")
                join_description = f"scheduled to join at {join_time.strftime('%I:%M %p CT')} (1 min before {time_str} start)"
            else:
                # Meeting is happening now or about to start
                join_description = "joining immediately (meeting starting now)"

        result["join_at"] = join_at

        # Step 7: Schedule the Recall bot
        try:
            bot_result = await self._recall.send_bot_to_meeting(
                meeting_url=teams_url,
                chat_id=chat_id,
                join_at=join_at,
            )

            if "error" in bot_result:
                result["error"] = bot_result["error"]
                result["confirmation_message"] = (
                    f"I detected a Teams meeting link for <b>{meeting_name}</b> "
                    f"but failed to schedule the bot: {bot_result['error']}"
                )
                logger.error(f"Failed to schedule bot: {bot_result['error']}")
            else:
                bot_id = bot_result.get("bot_id", "unknown")
                result["scheduled"] = True
                result["bot_id"] = bot_id

                # Build confirmation message
                parts = [
                    f"<b>Meeting bot auto-scheduled</b>",
                    f"Meeting: <i>{meeting_name}</i>",
                ]
                if meeting_time:
                    parts.append(f"Time: {meeting_time.strftime('%b %d, %Y at %I:%M %p CT')}")
                parts.extend([
                    f"Bot: {join_description}",
                    f"Bot ID: <code>{bot_id[:8]}...</code>",
                    "",
                    "The bot will appear as <b>Aaron Gonzalez</b> in the participant list.",
                    "When the meeting ends, I'll auto-process the transcript.",
                    "<i>Note: The host may need to admit the bot from the Teams lobby.</i>",
                ])
                result["confirmation_message"] = "\n".join(parts)

                logger.info(
                    f"Recall bot {bot_id[:8]} scheduled for {meeting_name} "
                    f"({join_description})"
                )

        except Exception as e:
            result["error"] = str(e)
            result["confirmation_message"] = (
                f"I detected a Teams meeting link for <b>{meeting_name}</b> "
                f"but hit an error scheduling the bot: {str(e)[:200]}"
            )
            logger.exception("Unexpected error scheduling Recall bot from auto-detect")

        return result
