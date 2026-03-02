"""Meeting Invite Handler — detects meeting invites in emails, schedules Recall bots,
and auto-populates project contact lists from attendees.

Part of Goliath's "Option A" integration: email-based meeting detection as a
workaround for Azure app registration (which IT won't approve).

Flow:
1. Email poller classifies email as "meeting_invite" (Teams URL + invite signals)
2. This handler extracts: meeting URL, start time, attendees, project mapping
3. Schedules a Recall.ai bot to join the meeting at the right time
4. Extracts attendees and updates the project's contact list
5. Notifies user via Telegram

Does NOT require:
- Azure app registration
- Calendar API access
- IT approval
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import PROJECTS, REPO_ROOT, match_project_key

CT = ZoneInfo("America/Chicago")
logger = logging.getLogger(__name__)

# ── Teams URL detection ──────────────────────────────────────────────────
TEAMS_URL_PATTERN = re.compile(
    r'https?://teams\.microsoft\.com/l/meetup-join/[^\s<>"\']+',
    re.IGNORECASE,
)

# ── Meeting invite body signals ──────────────────────────────────────────
# These phrases strongly indicate the email is a meeting invite, not just
# a regular email that mentions a meeting link.
INVITE_SIGNALS = [
    "join microsoft teams meeting",
    "join teams meeting",
    "microsoft teams meeting",
    "meeting id:",
    "passcode:",
    "dial-in by phone",
    "you have been invited to",
    "when:",
    "calendar event",
    "meeting invitation",
    "join with a video conferencing device",
    "click here to join the meeting",
]

# ── Calendar response keywords (these are NOT new invites) ───────────────
# Skip emails that are responses to invites, not actual invites
RESPONSE_SIGNALS = [
    "accepted:",
    "declined:",
    "tentatively accepted:",
    "tentative:",
    "canceled:",
    "cancelled:",
]

# ── Time parsing patterns ────────────────────────────────────────────────
# Common date/time formats in meeting invite emails:
# "When: Thursday, February 28, 2026 2:00 PM – 3:00 PM Central Time"
# "Start: Feb 28, 2026 at 2:00 PM CT"
# "Date: 2/28/2026"
# "Time: 2:00 PM - 3:00 PM"
WHEN_PATTERN = re.compile(
    r"(?:when|start(?:s)?|date\s*&?\s*time)\s*[:]\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# Parse time like "2:00 PM" or "14:00"
TIME_12H_PATTERN = re.compile(
    r"(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.IGNORECASE,
)
TIME_24H_PATTERN = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")

# Parse date like "February 28, 2026" or "Feb 28, 2026"
DATE_LONG_PATTERN = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)

# Parse date like "2/28/2026" or "02-28-2026"
DATE_NUMERIC_PATTERN = re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})")

# ── ICS parsing (simple regex-based, no library needed) ──────────────────
ICS_DTSTART_PATTERN = re.compile(r"DTSTART(?:;[^:]*)?:(\d{8}T\d{6}Z?)")
ICS_DTEND_PATTERN = re.compile(r"DTEND(?:;[^:]*)?:(\d{8}T\d{6}Z?)")
ICS_SUMMARY_PATTERN = re.compile(r"SUMMARY:(.+?)(?:\r?\n(?!\s)|$)", re.DOTALL)
ICS_ATTENDEE_PATTERN = re.compile(
    r"ATTENDEE.*?(?:CN=([^;:\"]+))?.*?(?:mailto:)?([^\s;>]+@[^\s;>]+)",
    re.IGNORECASE,
)
ICS_ORGANIZER_PATTERN = re.compile(
    r"ORGANIZER.*?(?:CN=([^;:\"]+))?.*?(?:mailto:)?([^\s;>]+@[^\s;>]+)",
    re.IGNORECASE,
)

# ── Contacts directory ───────────────────────────────────────────────────
CONTACTS_DIR = REPO_ROOT / "contacts"

# DSC team emails — never add these as project-specific contacts
DSC_EMAILS = {
    "aaron.gonzalez2@mastec.com",
    "tyler.wilcox@mastec.com",
    "joshua.hauger@mastec.com",
}


class MeetingInviteHandler:
    """Handles meeting invite detection, Recall bot scheduling, and contact extraction."""

    def __init__(self, recall_service=None):
        """
        Args:
            recall_service: RecallService instance for scheduling bots.
                           If None, bot scheduling is skipped (contacts-only mode).
        """
        self._recall_service = recall_service

    def is_meeting_invite(self, parsed: dict) -> bool:
        """Check if a parsed email looks like a meeting invite.

        Returns True if:
        1. Body contains a Teams meeting URL, AND
        2. Body contains at least one invite signal phrase, AND
        3. Subject does NOT start with a response keyword (Accepted/Declined/etc.)

        This avoids false positives from regular emails that just mention
        a meeting link (e.g., "see the recording from yesterday's meeting").
        """
        subject = (parsed.get("subject") or "").lower().strip()
        body = (parsed.get("body") or "").lower()

        # Skip calendar responses (Accepted, Declined, etc.)
        for signal in RESPONSE_SIGNALS:
            if subject.startswith(signal):
                return False

        # Must have a Teams URL
        if not TEAMS_URL_PATTERN.search(parsed.get("body") or ""):
            # Also check for .ics attachment with Teams URL
            for att in (parsed.get("attachments") or []):
                if att.get("filename", "").lower().endswith(".ics"):
                    ics_text = att.get("data", b"").decode("utf-8", errors="replace")
                    if TEAMS_URL_PATTERN.search(ics_text):
                        return True
            return False

        # Must have at least one invite signal
        invite_match = any(signal in body for signal in INVITE_SIGNALS)

        # Also check for .ics attachment as a strong signal
        has_ics = any(
            att.get("filename", "").lower().endswith(".ics")
            for att in (parsed.get("attachments") or [])
        )

        return invite_match or has_ics

    def extract_meeting_info(self, parsed: dict) -> dict:
        """Extract meeting details from a parsed email.

        Returns:
            {
                "teams_url": str,           # Teams meeting URL
                "meeting_time": datetime,   # Parsed start time (CT) or None
                "meeting_title": str,       # Meeting title (from subject)
                "project_key": str,         # Matched project key or None
                "project_name": str,        # Matched project name or None
                "attendees": [              # List of attendee dicts
                    {"name": str, "email": str}
                ],
                "organizer": {"name": str, "email": str} or None,
                "source": "body" | "ics",   # Where the URL was found
            }
        """
        body = parsed.get("body") or ""
        subject = parsed.get("subject") or ""
        cc = parsed.get("cc") or ""
        sender = parsed.get("sender") or ""

        result = {
            "teams_url": None,
            "meeting_time": None,
            "meeting_title": subject,
            "project_key": None,
            "project_name": None,
            "attendees": [],
            "organizer": None,
            "source": "body",
        }

        # ── Try ICS first (most structured) ──────────────────────────────
        ics_data = self._find_ics_data(parsed)
        if ics_data:
            ics_info = self._parse_ics(ics_data)
            if ics_info.get("teams_url"):
                result["teams_url"] = ics_info["teams_url"]
                result["source"] = "ics"
            if ics_info.get("start_time"):
                result["meeting_time"] = ics_info["start_time"]
            if ics_info.get("title"):
                result["meeting_title"] = ics_info["title"]
            if ics_info.get("attendees"):
                result["attendees"] = ics_info["attendees"]
            if ics_info.get("organizer"):
                result["organizer"] = ics_info["organizer"]

        # ── Fall back to body parsing ────────────────────────────────────
        if not result["teams_url"]:
            match = TEAMS_URL_PATTERN.search(body)
            if match:
                result["teams_url"] = match.group(0)

        if not result["meeting_time"]:
            result["meeting_time"] = self._parse_time_from_body(body)

        # ── Extract attendees from CC if not found in ICS ────────────────
        if not result["attendees"]:
            result["attendees"] = self._extract_attendees_from_headers(
                sender, cc
            )

        # ── Match project ────────────────────────────────────────────────
        project_key = match_project_key(result["meeting_title"])
        if project_key:
            result["project_key"] = project_key
            result["project_name"] = PROJECTS[project_key]["name"]

        return result

    async def process_meeting_invite(
        self,
        parsed: dict,
        chat_id: int,
        bot=None,
    ) -> dict:
        """Full pipeline: extract info → schedule bot → update contacts → notify.

        Args:
            parsed: Parsed email dict from EmailPoller
            chat_id: Telegram chat ID for notifications
            bot: Telegram bot instance for sending messages

        Returns:
            Dict with processing results
        """
        info = self.extract_meeting_info(parsed)

        result = {
            "teams_url": info["teams_url"],
            "project_key": info["project_key"],
            "meeting_time": info["meeting_time"],
            "bot_scheduled": False,
            "contacts_added": 0,
            "contacts_updated": 0,
        }

        if not info["teams_url"]:
            logger.warning("Meeting invite detected but no Teams URL found")
            return result

        # ── Schedule Recall bot ──────────────────────────────────────────
        if self._recall_service and self._recall_service.is_configured:
            bot_result = await self._schedule_bot(info, chat_id)
            result["bot_scheduled"] = not bot_result.get("error")
            result["bot_id"] = bot_result.get("bot_id")
            if bot_result.get("error"):
                result["bot_error"] = bot_result["error"]

        # ── Update contact list ──────────────────────────────────────────
        if info["project_key"] and info["attendees"]:
            added, updated = self._update_contacts(
                info["project_key"],
                info["project_name"],
                info["attendees"],
                info.get("organizer"),
            )
            result["contacts_added"] = added
            result["contacts_updated"] = updated

        # ── Send Telegram notification ───────────────────────────────────
        if bot and chat_id:
            await self._send_notification(bot, chat_id, info, result)

        return result

    async def _schedule_bot(self, info: dict, chat_id: int) -> dict:
        """Schedule a Recall.ai bot to join the meeting.

        If meeting_time is in the future, schedules for that time.
        If meeting_time is now or in the past (or unknown), joins immediately.
        """
        teams_url = info["teams_url"]
        meeting_time = info["meeting_time"]

        # Determine join_at
        join_at = None
        now = datetime.now(CT)

        if meeting_time:
            # If meeting is more than 2 minutes in the future, schedule it
            if meeting_time > now + timedelta(minutes=2):
                # Join 1 minute before meeting start
                join_time = meeting_time - timedelta(minutes=1)
                join_at = join_time.isoformat()
                logger.info(
                    f"Scheduling Recall bot for {join_at} "
                    f"(meeting at {meeting_time.isoformat()})"
                )
            elif meeting_time < now - timedelta(hours=2):
                # Meeting was more than 2 hours ago — don't join
                logger.info(
                    f"Meeting was at {meeting_time.isoformat()} — too old, "
                    f"skipping bot scheduling"
                )
                return {"error": "Meeting already ended (>2 hours ago)"}

        try:
            result = await self._recall_service.send_bot_to_meeting(
                meeting_url=teams_url,
                chat_id=chat_id,
                join_at=join_at,
            )
            return result
        except Exception as e:
            logger.exception("Failed to schedule Recall bot for meeting invite")
            return {"error": str(e)}

    def _update_contacts(
        self,
        project_key: str,
        project_name: str,
        attendees: list[dict],
        organizer: dict | None = None,
    ) -> tuple[int, int]:
        """Add/update contacts in the project's contact JSON file.

        Returns (added_count, updated_count).
        Skips DSC team members (they're portfolio-wide only).
        """
        CONTACTS_DIR.mkdir(parents=True, exist_ok=True)
        contact_file = CONTACTS_DIR / f"{project_key}.json"

        # Load existing contacts
        if contact_file.exists():
            try:
                data = json.loads(contact_file.read_text())
            except (json.JSONDecodeError, IOError):
                data = {}
        else:
            data = {}

        # Initialize structure if needed
        if "contacts" not in data:
            data = {
                "project": project_key,
                "project_name": project_name or project_key,
                "last_updated": datetime.now(CT).strftime("%Y-%m-%d"),
                "contacts": [],
            }

        existing_emails = {
            c["email"].lower() for c in data["contacts"] if c.get("email")
        }

        added = 0
        updated = 0
        today = datetime.now(CT).strftime("%Y-%m-%d")

        # Combine attendees + organizer
        all_people = list(attendees)
        if organizer and organizer.get("email"):
            all_people.append(organizer)

        for person in all_people:
            p_email = (person.get("email") or "").strip().lower()
            p_name = (person.get("name") or "").strip()

            if not p_email or "@" not in p_email:
                continue

            # Skip DSC team (portfolio-wide only)
            if p_email in DSC_EMAILS:
                continue

            if p_email in existing_emails:
                # Update name if we have a better one
                if p_name:
                    for contact in data["contacts"]:
                        if contact["email"].lower() == p_email:
                            if not contact.get("name") or contact["name"] == p_email:
                                contact["name"] = p_name
                                updated += 1
                            break
            else:
                # New contact
                new_contact = {
                    "name": p_name or p_email,
                    "email": p_email,
                    "role": "Unknown — from meeting invite",
                    "company": "Unknown",
                    "source": "meeting_invite",
                    "added": today,
                }
                data["contacts"].append(new_contact)
                existing_emails.add(p_email)
                added += 1

        if added > 0 or updated > 0:
            data["last_updated"] = today
            contact_file.write_text(json.dumps(data, indent=2) + "\n")
            logger.info(
                f"Updated {project_key} contacts: "
                f"+{added} new, {updated} updated"
            )

        return added, updated

    def _find_ics_data(self, parsed: dict) -> Optional[str]:
        """Find and return .ics calendar data from email attachments."""
        for att in (parsed.get("attachments") or []):
            filename = (att.get("filename") or "").lower()
            if filename.endswith(".ics") or filename.endswith(".vcs"):
                try:
                    return att["data"].decode("utf-8", errors="replace")
                except Exception:
                    pass
        return None

    def _parse_ics(self, ics_text: str) -> dict:
        """Parse an ICS calendar string to extract meeting details.

        Uses regex-based parsing — no icalendar library dependency.
        """
        result = {
            "teams_url": None,
            "start_time": None,
            "end_time": None,
            "title": None,
            "attendees": [],
            "organizer": None,
        }

        # Extract Teams URL
        url_match = TEAMS_URL_PATTERN.search(ics_text)
        if url_match:
            result["teams_url"] = url_match.group(0)

        # Extract start time
        dt_match = ICS_DTSTART_PATTERN.search(ics_text)
        if dt_match:
            result["start_time"] = self._parse_ics_datetime(dt_match.group(1))

        # Extract end time
        dt_end_match = ICS_DTEND_PATTERN.search(ics_text)
        if dt_end_match:
            result["end_time"] = self._parse_ics_datetime(dt_end_match.group(1))

        # Extract summary/title
        summary_match = ICS_SUMMARY_PATTERN.search(ics_text)
        if summary_match:
            title = summary_match.group(1).strip()
            # Unfold ICS line continuations
            title = re.sub(r"\r?\n\s", "", title)
            result["title"] = title

        # Extract attendees
        for match in ICS_ATTENDEE_PATTERN.finditer(ics_text):
            name = (match.group(1) or "").strip()
            addr = (match.group(2) or "").strip().lower()
            if addr and "@" in addr:
                result["attendees"].append({"name": name, "email": addr})

        # Extract organizer
        org_match = ICS_ORGANIZER_PATTERN.search(ics_text)
        if org_match:
            name = (org_match.group(1) or "").strip()
            addr = (org_match.group(2) or "").strip().lower()
            if addr and "@" in addr:
                result["organizer"] = {"name": name, "email": addr}

        return result

    @staticmethod
    def _parse_ics_datetime(dt_str: str) -> Optional[datetime]:
        """Parse an ICS datetime string like '20260228T140000Z' to CT datetime."""
        try:
            if dt_str.endswith("Z"):
                dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%SZ")
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            else:
                dt = datetime.strptime(dt_str, "%Y%m%dT%H%M%S")
                dt = dt.replace(tzinfo=CT)
            return dt.astimezone(CT)
        except (ValueError, TypeError):
            return None

    def _parse_time_from_body(self, body: str) -> Optional[datetime]:
        """Try to extract a meeting start time from the email body text.

        Looks for patterns like:
        - "When: Thursday, February 28, 2026 2:00 PM"
        - "Start: Feb 28, 2026 at 2:00 PM CT"
        """
        # Find "When:" or "Start:" line
        when_match = WHEN_PATTERN.search(body)
        if not when_match:
            return None

        when_text = when_match.group(1)

        # Parse date
        date_part = None
        long_match = DATE_LONG_PATTERN.search(when_text)
        if long_match:
            month_str = long_match.group(1)
            day = int(long_match.group(2))
            year = int(long_match.group(3))

            month_map = {
                "january": 1, "jan": 1, "february": 2, "feb": 2,
                "march": 3, "mar": 3, "april": 4, "apr": 4,
                "may": 5, "june": 6, "jun": 6,
                "july": 7, "jul": 7, "august": 8, "aug": 8,
                "september": 9, "sep": 9, "october": 10, "oct": 10,
                "november": 11, "nov": 11, "december": 12, "dec": 12,
            }
            month = month_map.get(month_str.lower())
            if month:
                date_part = datetime(year, month, day)
        else:
            num_match = DATE_NUMERIC_PATTERN.search(when_text)
            if num_match:
                month = int(num_match.group(1))
                day = int(num_match.group(2))
                year = int(num_match.group(3))
                if 1 <= month <= 12 and 1 <= day <= 31:
                    date_part = datetime(year, month, day)

        if not date_part:
            # Use today's date as fallback
            date_part = datetime.now(CT).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        # Parse time
        time_match = TIME_12H_PATTERN.search(when_text)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            ampm = time_match.group(3).upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
            date_part = date_part.replace(hour=hour, minute=minute)
        else:
            time_match = TIME_24H_PATTERN.search(when_text)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                date_part = date_part.replace(hour=hour, minute=minute)

        return date_part.replace(tzinfo=CT)

    @staticmethod
    def _extract_attendees_from_headers(
        sender: str, cc: str
    ) -> list[dict]:
        """Extract attendees from email sender + CC fields.

        These won't have names (just emails), but we can populate them later
        via Recall AI transcript speaker identification.
        """
        attendees = []
        all_emails = set()

        # Add sender
        if sender and "@" in sender:
            sender_lower = sender.strip().lower()
            if sender_lower not in DSC_EMAILS:
                attendees.append({"name": "", "email": sender_lower})
                all_emails.add(sender_lower)

        # Add CC recipients
        if cc:
            for addr in cc.split(","):
                addr = addr.strip().lower()
                if addr and "@" in addr and addr not in all_emails:
                    if addr not in DSC_EMAILS:
                        attendees.append({"name": "", "email": addr})
                        all_emails.add(addr)

        return attendees

    async def _send_notification(
        self, bot, chat_id: int, info: dict, result: dict
    ) -> None:
        """Send a Telegram notification about the processed meeting invite."""
        project_label = info["project_name"] or "Unknown Project"
        title = info["meeting_title"] or "Untitled Meeting"

        # Build message
        parts = [f"📅 <b>Meeting invite detected — {project_label}</b>"]
        parts.append(f"Title: <i>{title}</i>")

        if info["meeting_time"]:
            time_str = info["meeting_time"].strftime("%b %d, %Y at %I:%M %p CT")
            parts.append(f"When: {time_str}")

        if result.get("bot_scheduled"):
            if info["meeting_time"] and info["meeting_time"] > datetime.now(CT):
                parts.append(
                    f"🤖 Recall bot <b>scheduled</b> — will join 1 min before start"
                )
            else:
                parts.append(f"🤖 Recall bot <b>joining now</b>")
        elif result.get("bot_error"):
            parts.append(f"⚠️ Bot scheduling failed: {result['bot_error']}")
        elif not self._recall_service or not self._recall_service.is_configured:
            parts.append("ℹ️ Recall AI not configured — bot not scheduled")

        attendee_count = len(info.get("attendees", []))
        if attendee_count > 0:
            parts.append(
                f"👥 {attendee_count} attendee(s) found"
            )
            if result["contacts_added"] > 0:
                parts.append(
                    f"✅ <b>{result['contacts_added']} new contact(s)</b> "
                    f"added to {project_label} roster"
                )
            if result["contacts_updated"] > 0:
                parts.append(
                    f"📝 {result['contacts_updated']} contact(s) updated"
                )

        msg = "\n".join(parts)

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Failed to send meeting invite notification")
