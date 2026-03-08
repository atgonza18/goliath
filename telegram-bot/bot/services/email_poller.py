"""Gmail IMAP poller for the Power Automate inbound email relay.

Inbound flow:
  1. Power Automate forwards Office 365 emails to a Gmail account with
     tagged subjects: [INBOX: sender@email.com] Original Subject
  2. This poller connects via IMAP and reads unread emails with [INBOX: tags
  3. Parses the original sender address from the tag
  4. Feeds each email into the message queue as an inbound item
  5. Marks the email as read so it won't be re-processed

Attachment auto-filing:
  - POD emails: Attachments saved to projects/{key}/pod/
  - Constraints updates (from Joshua Hauger): Saved to
    dsc-constraints-production-reports/{date}/ AND project constraints/ folders
  - Schedule updates: Saved to projects/{key}/schedule/ — detected by keywords
    in subject or filenames. Also scans for direct emails (no PA tag).
  - Only files for portfolio projects (defined in config.PROJECTS)
  - Non-portfolio project emails with attachments are silently skipped

Constraint auto-logging (Feature #5):
  - Constraint-classified emails are also parsed for individual constraints
  - Each constraint is auto-created in ConstraintsPro via the constraints_manager agent
  - User is notified in Telegram with a summary for review
  - Runs as a background task so it never blocks the polling cycle

Credentials come from env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST.
"""

import asyncio
import collections
import email
import email.header
import email.message
import hashlib
import imaplib
import logging
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
from pathlib import Path
from typing import Optional

from bot.config import (
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST,
    PROJECTS, PROJECTS_DIR, REPORT_CHAT_ID, CONSTRAINTS_REPORTS_DIR,
    RELAY_TO_ADDRESS, match_project_key,
    CONSTRAINT_EMAIL_SENDERS, CONSTRAINT_EMAIL_KEYWORDS,
    HAUGER_SUMMARY_SENDERS, HAUGER_SUMMARY_SUBJECT_PREFIX,
)

logger = logging.getLogger(__name__)

# Regex to parse [INBOX: sender@email.com] from the subject line
INBOX_TAG_PATTERN = re.compile(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", re.IGNORECASE)

# Regex to parse [CC: addr1, addr2] tag from subject (added by Power Automate relay)
CC_TAG_PATTERN = re.compile(r"\[CC:\s*([^\]]+)\]\s*(.*)", re.IGNORECASE)

# IMAP connection timeout
IMAP_TIMEOUT = 15

# Allowed attachment file extensions (lowercase, with leading dot)
ALLOWED_EXTENSIONS = {
    '.pdf', '.xlsx', '.xls', '.csv', '.doc', '.docx',
    '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.zip', '.msg',
    '.ics', '.vcs',  # Calendar invite files (for meeting invite detection)
}

# Known constraints update senders (case-insensitive partial match on email address)
CONSTRAINTS_SENDERS = ['hauger', 'hogger']

# Schedule-related keywords for email/filename detection (case-insensitive)
SCHEDULE_KEYWORDS = [
    'schedule', 'lookahead', 'look ahead', 'look-ahead',
    'baseline', 'critical path',
    '3 week', '4 week', '3-week', '4-week', '3wk', '4wk',
    'gantt', 'level 3', 'level 2', 'l3 schedule', 'l2 schedule',
    'wk lookahead', 'week look',
]

# File extensions typically associated with project schedules
SCHEDULE_EXTENSIONS = {'.pdf', '.xer', '.mpp'}

# ── Module-level dedup set ──────────────────────────────────────────────
# This set persists for the lifetime of the bot process, surviving across
# EmailPoller instances (which are recreated every ~45s by the scheduler).
# We use an OrderedDict as an ordered set so we can evict the oldest entries
# when the cap is reached, rather than clearing everything at once (which
# would momentarily forget all IDs and risk re-processing).
_MAX_PROCESSED_IDS = 2000
_global_processed_ids: collections.OrderedDict = collections.OrderedDict()


def _content_dedup_key(parsed: dict) -> str:
    """Generate a dedup key from email content when Message-ID is missing.

    Uses sender + subject to create a stable hash. This catches Power Automate
    relays that forward the same email multiple times without preserving the
    original Message-ID header.
    """
    content = f"{parsed.get('sender', '')}|{parsed.get('subject', '')}"
    return f"content:{hashlib.md5(content.encode()).hexdigest()}"


def _dedup_key(parsed: dict) -> str:
    """Return the best available dedup key for an email.

    Prefers Message-ID when present; falls back to a content-based hash
    so emails without Message-ID headers are still deduped.
    """
    msg_id = parsed.get("message_id", "")
    return msg_id if msg_id else _content_dedup_key(parsed)


def _mark_processed(key: str) -> None:
    """Record a dedup key as processed in the global dedup cache.

    Uses an OrderedDict as an ordered set (values are always None).
    When the cap is exceeded, the oldest entries are evicted — not the
    entire set — so recently-seen IDs are never forgotten.
    """
    if not key:
        return
    # Move to end if already present (refreshes position), otherwise insert
    _global_processed_ids[key] = None
    _global_processed_ids.move_to_end(key)
    # Evict oldest entries if over cap
    while len(_global_processed_ids) > _MAX_PROCESSED_IDS:
        _global_processed_ids.popitem(last=False)


class EmailPoller:
    """Async-friendly Gmail IMAP poller for inbound emails.

    All IMAP operations run in a thread executor to avoid blocking the
    event loop, since imaplib is synchronous.
    """

    def __init__(
        self,
        address: str = None,
        app_password: str = None,
        imap_host: str = None,
    ):
        self.address = address or GMAIL_ADDRESS
        self.app_password = app_password or GMAIL_APP_PASSWORD
        self.imap_host = imap_host or GMAIL_IMAP_HOST
        self._queue = None  # Set by main.py or scheduler after initialization
        self._bot = None    # Set for Telegram notifications
        self._chat_id = None
        self._poll_lock = asyncio.Lock()       # Prevent concurrent poll cycles

    @property
    def is_configured(self) -> bool:
        """Return True if Gmail credentials are present."""
        return bool(self.address and self.app_password)

    def set_queue(self, queue) -> None:
        """Attach the message queue (called during bot initialization)."""
        self._queue = queue

    def set_bot(self, bot, chat_id: int = None) -> None:
        """Attach the Telegram bot for sending auto-file notifications.

        If chat_id is not provided, falls back to REPORT_CHAT_ID from env.
        """
        self._bot = bot
        self._chat_id = chat_id or (int(REPORT_CHAT_ID) if REPORT_CHAT_ID else None)

    # ==================================================================
    # Main poll cycle
    # ==================================================================

    async def poll(self) -> int:
        """Run a single poll cycle: fetch unread [INBOX:] emails and process them.

        - POD and constraints emails have their attachments auto-filed.
          They NEVER fall through to the draft queue (even without attachments).
        - Self-forwards (sender matches RELAY_TO_ADDRESS) are silently skipped.
        - All other emails are enqueued for draft responses.

        Returns:
            Number of emails processed.
        """
        if not self.is_configured:
            logger.debug("Email poller not configured — GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing")
            return 0

        if not self._queue:
            logger.error("EmailPoller.poll called but no queue attached")
            return 0

        # ── Prevent concurrent poll cycles ────────────────────────────
        if self._poll_lock.locked():
            logger.debug("Poll already in progress — skipping this cycle")
            return 0

        async with self._poll_lock:
            loop = asyncio.get_running_loop()
            try:
                raw_emails = await loop.run_in_executor(None, self._fetch_unread)
            except Exception:
                logger.exception("IMAP fetch failed")
                return 0

            if not raw_emails:
                return 0

            # Initialize POD notification batch — accumulates files per project
            # so we send ONE consolidated message instead of one per email.
            self._pod_batch = {}

            count = 0
            for raw in raw_emails:
                try:
                    parsed = self._parse_email(raw)
                    if not parsed:
                        continue

                    msg_id = parsed.get("message_id", "")
                    dedup = _dedup_key(parsed)

                    # ── Dedup: skip if already processed ──────────────────
                    if dedup in _global_processed_ids:
                        logger.info(
                            f"Skipping duplicate email — already processed: {dedup}"
                        )
                        continue
                    # ── End dedup ──────────────────────────────────────────

                    # ── Safety filter: skip outbound relay echoes ─────────
                    subj = parsed.get("subject", "")
                    if "[SEND:" in subj.upper():
                        logger.info(
                            f"Skipping relay echo — subject contains [SEND:]: {subj!r}"
                        )
                        _mark_processed(dedup)
                        continue
                    # ── End safety filter ─────────────────────────────────

                    # ── Classify and route ────────────────────────────────
                    # Classification runs FIRST so POD/constraints/schedule
                    # emails are never dropped by the self-forward filter.
                    classification = self._classify_email(parsed)
                    attachments = parsed.get("attachments", [])

                    # ── Self-forward detection: skip emails from user's own address ──
                    # Only applied to normal (draft-queue) emails.  POD, constraints,
                    # hauger_update, and schedule emails bypass this filter entirely
                    # so that forwarded project emails are always auto-filed.
                    if classification not in ("pod", "constraints", "hauger_update", "schedule"):
                        sender_lower = parsed["sender"].lower().strip()
                        relay_lower = (RELAY_TO_ADDRESS or "").lower().strip()
                        gmail_lower = (self.address or "").lower().strip()
                        if relay_lower and sender_lower == relay_lower:
                            logger.info(
                                f"Skipping self-forward — sender matches RELAY_TO_ADDRESS: "
                                f"{parsed['subject']!r}"
                            )
                            _mark_processed(dedup)
                            continue
                        if gmail_lower and sender_lower == gmail_lower:
                            logger.info(
                                f"Skipping self-sent — sender matches GMAIL_ADDRESS: "
                                f"{parsed['subject']!r}"
                            )
                            _mark_processed(dedup)
                            continue
                    # ── End self-forward detection ────────────────────────

                    if classification in ("pod", "constraints", "hauger_update", "schedule"):
                        # POD/constraints/hauger emails NEVER fall through to draft queue
                        if attachments:
                            # Hauger DSC summaries still get filed to central
                            # constraints folder (same as generic constraints)
                            file_class = "constraints" if classification == "hauger_update" else classification
                            await self._file_attachments(
                                parsed, file_class, attachments
                            )
                        else:
                            logger.info(
                                f"{classification.upper()} email received but no "
                                f"attachments — skipping: {parsed['subject']!r}"
                            )

                        # ── Hauger DSC summary processing (Feature #5b) ──
                        # Route to the note-append / production-intel pipeline
                        # instead of creating new constraints.
                        if classification == "hauger_update":
                            self._spawn_hauger_processing(parsed)
                        # ── Constraint auto-logging (Feature #5) ─────────
                        # Spawn a background task to parse constraints from
                        # the email and create them in ConstraintsPro.
                        # Non-blocking — the poller continues immediately.
                        elif classification == "constraints":
                            self._spawn_constraint_logging(parsed)
                        # ── End constraint auto-logging ───────────────────

                        count += 1
                        _mark_processed(dedup)
                        continue

                    # ── Meeting invite: auto-schedule Recall bot + harvest contacts ──
                    # DISABLED — meeting invite detection disabled to prevent
                    # automatic firing. Code kept dormant for future re-enable.
                    # if classification == "meeting_invite":
                    #     self._spawn_meeting_invite_processing(parsed)
                    #     count += 1
                    #     _mark_processed(dedup)
                    #     continue
                    # ── End meeting invite ────────────────────────────────────────────

                    # ── Normal email: enqueue for draft response ──────────
                    inbound_cc = parsed.get("cc") or None
                    await self._queue.enqueue(
                        source="email",
                        direction="inbound",
                        sender=parsed["sender"],
                        subject=parsed["subject"],
                        body=parsed["body"],
                        cc=inbound_cc,
                        external_message_id=parsed.get("message_id"),
                    )
                    count += 1
                    cc_info = f" cc={inbound_cc}" if inbound_cc else ""
                    logger.info(
                        f"Polled inbound email from {parsed['sender']}{cc_info} — "
                        f"subject: {parsed['subject']!r}"
                    )

                    # ── Email Reply Monitor: check for constraint resolution signals ──
                    # Runs as a background task so it never blocks the polling cycle.
                    # Detects signals like "PO submitted", "delivery confirmed" and
                    # proposes ConstraintsPro updates to the user via Telegram.
                    self._spawn_reply_monitoring(parsed)
                    # ── End email reply monitor ────────────────────────────────────

                    _mark_processed(dedup)

                except Exception:
                    logger.exception("Failed to process polled email")

            # ── Flush batched POD notifications (one per project) ──────
            try:
                await self._flush_pod_notifications()
            except Exception:
                logger.exception("Failed to flush batched POD notifications")
            # ── End POD notification flush ──────────────────────────────

            logger.info(f"Email poll cycle complete: {count} email(s) processed")

            # ── Direct email scan: catch schedule PDFs sent straight to Gmail ──
            # These don't have [INBOX:] tags so the main fetch above skips them.
            try:
                direct_count = await self._process_direct_schedule_emails()
                if direct_count:
                    logger.info(
                        f"Direct schedule scan: {direct_count} schedule email(s) filed"
                    )
                    count += direct_count
            except Exception:
                logger.exception("Direct schedule email scan failed")

            return count

    # ==================================================================
    # Direct email scanning (non-PA)
    # ==================================================================

    async def _process_direct_schedule_emails(self) -> int:
        """Scan for schedule emails sent directly to Gmail (no PA [INBOX:] tag).

        Uses IMAP SUBJECT search for schedule keywords to minimize downloads.
        Only marks an email as read if it's successfully processed as a schedule.
        Non-schedule direct emails are left unread.

        Returns number of schedule emails processed.
        """
        if not self.is_configured:
            return 0

        loop = asyncio.get_running_loop()
        try:
            raw_emails = await loop.run_in_executor(
                None, self._fetch_direct_schedule_candidates
            )
        except Exception:
            logger.exception("Failed to fetch direct schedule emails")
            return 0

        if not raw_emails:
            return 0

        count = 0
        today = datetime.now(CT).strftime("%Y-%m-%d")

        for raw in raw_emails:
            try:
                parsed = self._parse_direct_email(raw)
                if not parsed:
                    continue

                dedup = _dedup_key(parsed)
                if dedup in _global_processed_ids:
                    continue

                # Verify it's actually a schedule email (keyword check in fetch
                # was broad — double-check with full classification logic)
                if not self._is_schedule_email(parsed):
                    continue

                attachments = parsed.get("attachments", [])
                if attachments:
                    await self._file_schedule_attachments(
                        subject=parsed.get("subject", ""),
                        sender=parsed.get("sender", "unknown"),
                        attachments=attachments,
                        today=today,
                    )
                    count += 1
                _mark_processed(dedup)
            except Exception:
                logger.exception("Failed to process direct schedule email")

        return count

    def _fetch_direct_schedule_candidates(self) -> list[bytes]:
        """Synchronous IMAP fetch for direct schedule emails (no [INBOX:] tag).

        Searches for unread emails with schedule-related keywords in subject.
        Only fetches emails that do NOT have the [INBOX:] PA relay tag.
        Marks matched emails as read.

        Runs in executor thread.
        """
        results = []
        try:
            mail = imaplib.IMAP4_SSL(self.imap_host, 993, timeout=IMAP_TIMEOUT)
            mail.login(self.address, self.app_password)
            mail.select("INBOX")

            # Search for a few high-signal keywords — covers 95%+ of schedule emails
            candidate_ids = set()
            for keyword in ("schedule", "lookahead", "look ahead", "baseline"):
                status, data = mail.search(
                    None, f'(UNSEEN SUBJECT "{keyword}")'
                )
                if status == "OK" and data and data[0]:
                    candidate_ids.update(data[0].split())

            if not candidate_ids:
                mail.logout()
                return results

            for msg_id in candidate_ids:
                try:
                    # Peek at subject first to skip [INBOX:] tagged emails
                    status, header_data = mail.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])")
                    if status != "OK" or not header_data or not header_data[0]:
                        continue

                    header_bytes = header_data[0][1] if isinstance(header_data[0], tuple) else b""
                    header_text = header_bytes.decode("utf-8", errors="replace").lower()
                    if "[inbox:" in header_text:
                        continue  # PA-relayed — handled by main fetch

                    # Fetch full email
                    status, msg_data = mail.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_email = msg_data[0][1]
                    results.append(raw_email)

                    # Mark as read
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                    logger.info(f"Direct schedule candidate fetched: msg_id={msg_id}")

                except Exception:
                    logger.exception(f"Failed to fetch direct email {msg_id}")

            mail.logout()

        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP error (direct schedule scan): {e}")
        except Exception:
            logger.exception("IMAP connection failed (direct schedule scan)")

        return results

    def _parse_direct_email(self, raw: bytes) -> Optional[dict]:
        """Parse a direct email (no [INBOX:] tag) — sender from From: header."""
        msg = email.message_from_bytes(raw)

        # Get sender from From: header
        raw_from = msg.get("From", "")
        # Extract email address from "Name <email>" format
        addr_match = re.search(r'[\w.\-+]+@[\w.\-]+\.\w+', raw_from)
        sender = addr_match.group(0) if addr_match else raw_from

        # Decode subject
        raw_subject = msg.get("Subject", "")
        decoded_parts = email.header.decode_header(raw_subject)
        subject_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                subject_parts.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                subject_parts.append(part)
        subject = " ".join(subject_parts).strip()

        message_id = msg.get("Message-ID", "")
        body = self._extract_body(msg)
        attachments = self._extract_attachments(msg)

        return {
            "sender": sender,
            "subject": subject,
            "body": body,
            "message_id": message_id,
            "attachments": attachments,
        }

    # ==================================================================
    # Email classification
    # ==================================================================

    def _classify_email(self, parsed: dict) -> str:
        """Classify an email as 'pod', 'hauger_update', 'constraints', 'schedule',
        'meeting_invite', or 'normal'.

        Returns:
            'pod'              — Production/POD report email
            'hauger_update'    — Josh Hauger's DSC summary (constraint notes + production intel)
            'constraints'      — Constraints update from other senders
            'schedule'         — Schedule update (lookahead, baseline, P6 export, etc.)
            'meeting_invite'   — Meeting invite with Teams URL (auto-schedule Recall bot)
            'normal'           — Everything else (queued for draft response)
        """
        subject = (parsed.get("subject") or "").lower()
        sender = (parsed.get("sender") or "").lower()

        # POD detection: subject or attachment filenames contain "POD" or
        # "Plan of the Day" (case-insensitive).  Catches both shorthand and
        # the long-form name that site teams sometimes use.
        pod_patterns = (r'\bpod\b', r'plan\s+of\s+the\s+day', r'plan\s+of\s+day',
                        r'daily\s+pod', r'daily\s+plan')
        if any(re.search(p, subject) for p in pod_patterns):
            return "pod"
        # Also check attachment filenames — sometimes subject is generic
        # but the attachment is literally "Plan of the Day - ProjectX.pdf"
        for att in (parsed.get("attachments") or []):
            att_name = (att.get("filename") or "").lower()
            if any(re.search(p, att_name) for p in pod_patterns):
                return "pod"

        # Hauger DSC summary detection — MUST come before generic constraints
        # detection so Hauger's emails are routed to the note-append / intel
        # pipeline rather than creating new constraints (which would be circular).
        is_hauger_sender = any(name in sender for name in HAUGER_SUMMARY_SENDERS)
        is_dsc_subject = subject.startswith(HAUGER_SUMMARY_SUBJECT_PREFIX)
        if is_hauger_sender and is_dsc_subject:
            return "hauger_update"

        # Constraints detection: from known constraints senders (non-Hauger DSC)
        # This still catches Hauger emails that do NOT have the DSC subject prefix
        # (e.g., ad-hoc constraint emails) — those go through the normal create path.
        for name in CONSTRAINTS_SENDERS:
            if name in sender:
                return "constraints"

        # Constraints detection: subject mentions constraints + has attachments
        if "constraint" in subject and parsed.get("attachments"):
            return "constraints"

        # Schedule detection: attachments with schedule keywords in subject or filename
        if self._is_schedule_email(parsed):
            return "schedule"

        # Meeting invite detection: Teams URL + invite signals in body
        # DISABLED — meeting invite detection disabled to prevent automatic firing.
        # if self._is_meeting_invite(parsed):
        #     return "meeting_invite"

        return "normal"

    def _is_schedule_email(self, parsed: dict) -> bool:
        """Detect schedule-related emails by checking subject and attachment filenames.

        An email is classified as a schedule if:
        1. Subject contains a schedule keyword AND has schedule-type attachments, OR
        2. Any attachment filename contains a schedule keyword AND has a schedule extension
        """
        subject = (parsed.get("subject") or "").lower()
        attachments = parsed.get("attachments", [])

        if not attachments:
            return False

        has_schedule_attachment = any(
            os.path.splitext(a["filename"])[1].lower() in SCHEDULE_EXTENSIONS
            for a in attachments
        )

        # Check subject for schedule keywords (requires schedule-type attachment)
        if has_schedule_attachment:
            for kw in SCHEDULE_KEYWORDS:
                if kw in subject:
                    return True

        # Check filenames for schedule keywords + schedule extensions
        for att in attachments:
            fname_lower = att["filename"].lower()
            ext = os.path.splitext(fname_lower)[1]
            if ext in SCHEDULE_EXTENSIONS:
                for kw in SCHEDULE_KEYWORDS:
                    if kw in fname_lower:
                        return True

        return False

    def _is_meeting_invite(self, parsed: dict) -> bool:
        """Check if an email is a meeting invite with a Teams URL.

        DISABLED — meeting invite detection disabled to prevent automatic firing.
        Always returns False. Original code kept below for future re-enable.
        """
        return False
        # try:
        #     from bot.services.meeting_invite_handler import MeetingInviteHandler
        #     handler = MeetingInviteHandler()
        #     return handler.is_meeting_invite(parsed)
        # except ImportError:
        #     logger.debug("meeting_invite_handler not available — skipping invite check")
        #     return False

    # ==================================================================
    # Attachment filing
    # ==================================================================

    async def _file_attachments(
        self, parsed: dict, classification: str, attachments: list[dict]
    ) -> bool:
        """Save attachments to the appropriate project folder(s).

        POD files:
            → projects/{project-key}/pod/{date}_{filename}
            Only saved if we can match a portfolio project. Returns False if not.

        Constraints files:
            → dsc-constraints-production-reports/{date}/{filename}  (always)
            → projects/{project-key}/constraints/{date}_{filename}  (if project matched)
            Returns True as long as central save succeeds.

        Schedule files:
            → projects/{project-key}/schedule/{date}_{filename}  (if project matched)
            → projects/schedule-inbox/{date}_{filename}  (if project unclear)
            Each attachment is individually matched to a project by filename.

        Returns True if at least one file was saved, False otherwise.
        """
        subject = parsed.get("subject", "")
        sender = parsed.get("sender", "unknown")
        today = datetime.now(CT).strftime("%Y-%m-%d")

        if classification == "pod":
            return await self._file_pod_attachments(
                subject, sender, attachments, today
            )
        elif classification == "constraints":
            return await self._file_constraints_attachments(
                subject, sender, attachments, today
            )
        elif classification == "schedule":
            return await self._file_schedule_attachments(
                subject, sender, attachments, today
            )
        return False

    async def _file_pod_attachments(
        self, subject: str, sender: str, attachments: list[dict], today: str
    ) -> bool:
        """File POD attachments to the matching project's pod/ folder.

        Only saves for portfolio projects. Non-portfolio PODs are skipped.
        Tries to match the project from the subject first, then falls back
        to checking attachment filenames (e.g. "Plan of the Day - Scioto Ridge.pdf").
        """
        project_key = match_project_key(subject)
        # Fallback: check attachment filenames for a project name
        if not project_key:
            for att in attachments:
                project_key = match_project_key(att.get("filename") or "")
                if project_key:
                    break
        if not project_key:
            logger.info(
                f"POD email for non-portfolio project — skipping: {subject!r}"
            )
            return False

        target_dir = PROJECTS_DIR / project_key / "pod"
        target_dir.mkdir(parents=True, exist_ok=True)

        project_name = PROJECTS[project_key]["name"]
        saved_files = self._save_attachments(attachments, target_dir, today)

        if saved_files:
            # Batch POD notifications: accumulate files per project so we send
            # ONE consolidated Telegram message per project per poll cycle,
            # instead of one notification per email (Power Automate sends one
            # email per attachment, so a 12-attachment POD was 12 notifications).
            if hasattr(self, "_pod_batch") and self._pod_batch is not None:
                if project_key not in self._pod_batch:
                    self._pod_batch[project_key] = {
                        "project_name": project_name,
                        "sender": sender,
                        "files": list(saved_files),
                    }
                else:
                    self._pod_batch[project_key]["files"].extend(saved_files)
            else:
                # Fallback: called outside poll cycle — notify immediately
                await self._notify_attachment_filed(
                    project_name=project_name,
                    project_key=project_key,
                    classification="pod",
                    sender=sender,
                    subject=subject,
                    files=saved_files,
                )
            # Trigger async POD data extraction for saved PDF files
            self._trigger_pod_extraction(saved_files, project_key)
        return bool(saved_files)

    def _trigger_pod_extraction(self, files: list[Path], project_key: str) -> None:
        """Kick off POD data extraction in a background thread (non-blocking)."""
        import concurrent.futures
        pdf_files = [f for f in files if f.suffix.lower() == ".pdf" and ".corrupted" not in f.name]
        if not pdf_files:
            return

        def _run_extraction():
            import subprocess
            script = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "extract_pod_data.py"
            if not script.exists():
                logger.debug(f"POD extraction script not found: {script}")
                return
            for pdf in pdf_files:
                try:
                    subprocess.run(
                        [sys.executable, str(script), "--file", str(pdf)],
                        capture_output=True, timeout=180,
                    )
                except Exception:
                    logger.debug(f"POD extraction failed for {pdf.name}", exc_info=True)

        try:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            executor.submit(_run_extraction)
        except Exception:
            logger.debug("Could not start POD extraction thread", exc_info=True)

    async def _file_constraints_attachments(
        self, subject: str, sender: str, attachments: list[dict], today: str
    ) -> bool:
        """File constraints attachments to the central reports folder.

        Also copies to project-specific constraints/ folders if the filename
        contains a recognizable project name.
        """
        # Always save to the central dated folder
        central_dir = CONSTRAINTS_REPORTS_DIR / today
        central_dir.mkdir(parents=True, exist_ok=True)

        saved_central = self._save_attachments(attachments, central_dir, today)

        # Also try to file per-project based on filename or subject
        per_project_saves = []
        for att in attachments:
            filename = att["filename"]
            # Try matching project from filename first, then subject
            pk = match_project_key(filename) or match_project_key(subject)
            if pk:
                proj_dir = PROJECTS_DIR / pk / "constraints"
                proj_dir.mkdir(parents=True, exist_ok=True)
                proj_saved = self._save_attachments([att], proj_dir, today)
                per_project_saves.extend(proj_saved)

        if saved_central:
            # Build notification showing where files went
            await self._notify_constraints_filed(
                sender=sender,
                subject=subject,
                central_files=saved_central,
                project_files=per_project_saves,
                central_dir=central_dir,
            )
        return bool(saved_central)

    async def _file_schedule_attachments(
        self, subject: str, sender: str, attachments: list[dict], today: str
    ) -> bool:
        """File schedule attachments to the matching project's schedule/ folder.

        Project matching priority (per attachment):
        1. Match from attachment filename (most reliable — filenames usually
           contain project name, e.g. 'Blackford Solar_4WK Lookahead_20260223.pdf')
        2. Fall back to matching from email subject
        3. If still no match, save to schedule-inbox/ and notify user

        Handles multi-project emails: if one email has PDFs for Blackford AND
        Duff, each goes to the correct project folder.
        """
        # Only process schedule-type files
        schedule_atts = [
            a for a in attachments
            if os.path.splitext(a["filename"])[1].lower() in SCHEDULE_EXTENSIONS
        ]
        # If no schedule-extension files found, try all attachments (user
        # explicitly sent this as a schedule email, trust them)
        if not schedule_atts:
            schedule_atts = attachments

        # Group attachments by matched project key
        # key = project_key or "__unmatched__"
        grouped: dict[str, list[dict]] = {}
        for att in schedule_atts:
            pk = match_project_key(att["filename"]) or match_project_key(subject)
            bucket = pk or "__unmatched__"
            grouped.setdefault(bucket, []).append(att)

        any_saved = False

        # File matched attachments to their project schedule/ folders
        for pk, atts in grouped.items():
            if pk == "__unmatched__":
                continue
            target_dir = PROJECTS_DIR / pk / "schedule"
            target_dir.mkdir(parents=True, exist_ok=True)
            project_name = PROJECTS[pk]["name"]
            saved = self._save_attachments(atts, target_dir, today)
            if saved:
                any_saved = True
                await self._notify_schedule_filed(
                    project_name=project_name,
                    project_key=pk,
                    sender=sender,
                    subject=subject,
                    files=saved,
                    unmatched=False,
                )

        # File unmatched attachments to schedule-inbox/
        unmatched = grouped.get("__unmatched__", [])
        if unmatched:
            inbox_dir = PROJECTS_DIR / "schedule-inbox"
            inbox_dir.mkdir(parents=True, exist_ok=True)
            saved = self._save_attachments(unmatched, inbox_dir, today)
            if saved:
                any_saved = True
                await self._notify_schedule_filed(
                    project_name=None,
                    project_key=None,
                    sender=sender,
                    subject=subject,
                    files=saved,
                    unmatched=True,
                )

        return any_saved

    def _validate_attachment(self, data: bytes, filename: str) -> tuple[bytes, str]:
        """Validate an attachment payload and attempt repair if corrupted.

        Returns (clean_data, status) where status is one of:
          - 'clean': data is valid
          - 'repaired': null prefix was stripped, data is now valid
          - 'corrupted': data has irreparable corruption (UTF-8 mangling)
        """
        if not data:
            return data, "clean"

        # Check 1: null prefix (Power Automate sometimes prepends "null")
        if data[:4] == b"null":
            stripped = data[4:]
            replacement_count = stripped.count(b"\xef\xbf\xbd")

            # Check if stripping reveals valid content
            if stripped[:5] == b"%PDF-" or stripped[:2] == b"PK":
                if replacement_count > 50:
                    # Has valid header after strip but deep corruption from
                    # UTF-8 mangling. Still strip null so the file is more
                    # usable — at least tools can try to open it.
                    pct = (replacement_count * 3 / len(stripped)) * 100
                    logger.warning(
                        f"Attachment '{filename}' is corrupted: null prefix stripped "
                        f"but {replacement_count:,} UTF-8 replacement chars (~{pct:.1f}%%) "
                        f"— Power Automate binary mangling"
                    )
                    return stripped, "corrupted"
                logger.warning(
                    f"Attachment '{filename}' had 'null' prefix — stripped successfully"
                )
                return stripped, "repaired"

            # Still not valid after stripping
            if replacement_count > 50:
                logger.warning(
                    f"Attachment '{filename}' is corrupted: null prefix + "
                    f"{replacement_count:,} UTF-8 replacement chars — lossy mangling"
                )
                return stripped, "corrupted"
            logger.warning(
                f"Attachment '{filename}' had 'null' prefix but unknown format after strip"
            )
            return stripped, "repaired"

        # Check 2: excessive UTF-8 replacement characters (EF BF BD)
        # This indicates binary data was round-tripped through text encoding
        if filename.lower().endswith(".pdf"):
            replacement_count = data.count(b"\xef\xbf\xbd")
            if replacement_count > 100:
                pct = (replacement_count * 3 / len(data)) * 100
                logger.warning(
                    f"Attachment '{filename}' has {replacement_count:,} UTF-8 "
                    f"replacement chars (~{pct:.1f}% of file) — corrupted"
                )
                return data, "corrupted"

            # Check 3: PDF should start with %PDF-
            if not data[:5] == b"%PDF-":
                # Could be other valid formats, only flag PDFs
                logger.warning(
                    f"PDF '{filename}' missing %%PDF- magic bytes. "
                    f"First 20 bytes: {data[:20].hex()}"
                )

        return data, "clean"

    def _save_attachments(
        self, attachments: list[dict], target_dir: Path, today: str
    ) -> list[Path]:
        """Save a list of attachments to a target directory.

        Adds date prefix if filename doesn't already have one.
        Validates attachments for corruption before saving.
        Avoids overwriting by appending a numeric suffix.

        Returns list of saved file paths.
        """
        saved = []
        for att in attachments:
            filename = att["filename"]
            data = att["data"]

            # Validate and attempt repair
            clean_data, status = self._validate_attachment(data, filename)

            # Sanitize filename — keep alphanumeric, hyphens, dots, underscores, spaces, parens
            safe_name = re.sub(r'[^\w\-._() ]', '', filename).strip()
            if not safe_name:
                safe_name = f"attachment_{len(saved) + 1}.bin"

            # Add date prefix if filename doesn't already start with a date pattern
            if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}', safe_name):
                safe_name = f"{today}_{safe_name}"

            # Corrupted files get .corrupted extension
            if status == "corrupted":
                safe_name = safe_name + ".corrupted"

            filepath = target_dir / safe_name

            # Don't overwrite — append numeric suffix if file exists
            if filepath.exists():
                stem = filepath.stem
                suffix = filepath.suffix
                counter = 1
                while filepath.exists():
                    filepath = target_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                filepath.write_bytes(clean_data)
                saved.append(filepath)
                log_msg = f"Saved attachment: {filepath}"
                if status == "repaired":
                    log_msg += " (repaired: null prefix stripped)"
                elif status == "corrupted":
                    log_msg += " (CORRUPTED — saved with .corrupted extension)"
                logger.info(log_msg)

                # Send Telegram alert for corrupted files
                if status == "corrupted" and hasattr(self, '_bot') and self._bot:
                    try:
                        asyncio.get_event_loop().create_task(
                            self._bot.send_message(
                                chat_id=REPORT_CHAT_ID,
                                text=(
                                    f"<b>Corrupted attachment detected</b>\n"
                                    f"<code>{filename}</code>\n"
                                    f"Saved as: <code>{filepath.name}</code>\n"
                                    f"The file has irreparable UTF-8 mangling. "
                                    f"Check Power Automate email relay."
                                ),
                                parse_mode="HTML",
                            )
                        )
                    except Exception:
                        logger.debug("Could not send corruption alert to Telegram")

            except Exception:
                logger.exception(f"Failed to save attachment {safe_name} to {target_dir}")

        return saved

    # ==================================================================
    # Telegram notifications
    # ==================================================================

    async def _notify_attachment_filed(
        self,
        project_name: str,
        project_key: str,
        classification: str,
        sender: str,
        subject: str,
        files: list[Path],
    ) -> None:
        """Send a Telegram notification about auto-filed POD attachments."""
        if not self._bot or not self._chat_id:
            logger.debug("No bot/chat_id — skipping Telegram notification")
            return

        icon = "📊" if classification == "pod" else "📋"
        label = "POD" if classification == "pod" else "Constraints Update"
        subfolder = "pod" if classification == "pod" else "constraints"

        file_list = "\n".join(f"  • <code>{f.name}</code>" for f in files)
        msg = (
            f"{icon} <b>{label} auto-filed — {project_name}</b>\n\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"<b>Saved to</b> <code>projects/{project_key}/{subfolder}/</code>:\n"
            f"{file_list}"
        )

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Failed to send POD notification to Telegram")

    async def _flush_pod_notifications(self) -> None:
        """Send ONE consolidated Telegram notification per project for all POD
        files received during this poll cycle.

        This eliminates notification spam caused by Power Automate sending one
        email per attachment (e.g. a 12-attachment POD was producing 12
        separate Telegram messages).
        """
        if not self._pod_batch:
            return
        if not self._bot or not self._chat_id:
            logger.debug("No bot/chat_id — skipping batched POD notifications")
            self._pod_batch = None
            return

        for project_key, info in self._pod_batch.items():
            project_name = info["project_name"]
            files = info["files"]
            file_count = len(files)
            file_list = "\n".join(f"  • <code>{f.name}</code>" for f in files)
            msg = (
                f"📊 <b>POD auto-filed — {project_name}</b>\n\n"
                f"From: {info['sender']}\n"
                f"<b>{file_count} file(s)</b> saved to "
                f"<code>projects/{project_key}/pod/</code>:\n"
                f"{file_list}"
            )
            try:
                await self._bot.send_message(
                    chat_id=self._chat_id,
                    text=msg,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.exception(
                    f"Failed to send batched POD notification for {project_key}"
                )

        self._pod_batch = None

    async def _notify_schedule_filed(
        self,
        project_name: str | None,
        project_key: str | None,
        sender: str,
        subject: str,
        files: list[Path],
        unmatched: bool = False,
    ) -> None:
        """Send a Telegram notification about auto-filed schedule attachments."""
        if not self._bot or not self._chat_id:
            logger.debug("No bot/chat_id — skipping Telegram notification")
            return

        file_list = "\n".join(f"  • <code>{f.name}</code>" for f in files)

        if unmatched:
            msg = (
                f"📅 <b>Schedule received — project unclear</b>\n\n"
                f"From: {sender}\n"
                f"Subject: {subject}\n\n"
                f"<b>Saved to</b> <code>projects/schedule-inbox/</code>:\n"
                f"{file_list}\n\n"
                f"⚠️ Couldn't match a project. Reply with the project name "
                f"and I'll move it to the right folder."
            )
        else:
            msg = (
                f"📅 <b>Schedule auto-filed — {project_name}</b>\n\n"
                f"From: {sender}\n"
                f"Subject: {subject}\n\n"
                f"<b>Saved to</b> <code>projects/{project_key}/schedule/</code>:\n"
                f"{file_list}"
            )

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Failed to send schedule notification to Telegram")

    async def _notify_constraints_filed(
        self,
        sender: str,
        subject: str,
        central_files: list[Path],
        project_files: list[Path],
        central_dir: Path,
    ) -> None:
        """Send a Telegram notification about auto-filed constraints attachments."""
        if not self._bot or not self._chat_id:
            logger.debug("No bot/chat_id — skipping Telegram notification")
            return

        central_list = "\n".join(f"  • <code>{f.name}</code>" for f in central_files)
        msg = (
            f"📋 <b>Constraints Update auto-filed</b>\n\n"
            f"From: {sender}\n"
            f"Subject: {subject}\n\n"
            f"<b>Central:</b> <code>{central_dir.relative_to(central_dir.parent.parent)}/</code>\n"
            f"{central_list}"
        )

        if project_files:
            proj_list = "\n".join(f"  • <code>{f.name}</code>" for f in project_files)
            msg += f"\n\n<b>Also copied to project folders:</b>\n{proj_list}"

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Failed to send constraints notification to Telegram")

    # ==================================================================
    # Constraint auto-logging (Feature #5)
    # ==================================================================

    def _spawn_constraint_logging(self, parsed: dict) -> None:
        """Spawn a background task to auto-log constraints from an email
        into ConstraintsPro.

        This is fire-and-forget — it runs asynchronously and never blocks
        the email polling cycle. Errors are caught and logged internally.

        Args:
            parsed: Parsed email dict with 'sender', 'subject', 'body',
                    and optionally 'attachments'.
        """
        if not self._bot or not self._chat_id:
            logger.debug(
                "Constraint auto-logging skipped — no bot/chat_id configured"
            )
            return

        # Don't auto-log if the email has no body content to parse
        body = (parsed.get("body") or "").strip()
        if not body:
            logger.info(
                "Constraint auto-logging skipped — email has no body text"
            )
            return

        sender = parsed.get("sender", "unknown")
        subject = parsed.get("subject", "")

        logger.info(
            f"Spawning constraint auto-logger for email from {sender}: "
            f"{subject!r}"
        )

        asyncio.create_task(
            self._run_constraint_logging(
                email_body=body,
                sender=sender,
                subject=subject,
            ),
            name=f"constraint-autolog-{sender}",
        )

    async def _run_constraint_logging(
        self,
        email_body: str,
        sender: str,
        subject: str,
    ) -> None:
        """Background task that runs the constraint auto-logger.

        Catches all exceptions so it never crashes the bot process.
        """
        try:
            from bot.services.constraint_logger import ConstraintLogger

            clogger = ConstraintLogger()
            created_count = await clogger.process_and_log(
                bot=self._bot,
                chat_id=self._chat_id,
                email_body=email_body,
                sender=sender,
                subject=subject,
            )

            if created_count > 0:
                logger.info(
                    f"Constraint auto-logger: {created_count} constraint(s) "
                    f"created from {sender}'s email"
                )
            else:
                logger.info(
                    f"Constraint auto-logger: no new constraints from "
                    f"{sender}'s email"
                )
        except Exception:
            logger.exception(
                f"Constraint auto-logging failed for email from {sender}"
            )

    # ==================================================================
    # Email Reply Monitor integration (Proactive Follow-Up pipeline)
    # ==================================================================

    def _spawn_reply_monitoring(self, parsed: dict) -> None:
        """Spawn a background task to check an email for constraint resolution signals.

        This is part of the Proactive Follow-Up pipeline. After the user sends
        follow-up emails (copied from the daily PDF), replies may contain signals
        like "PO submitted" or "delivery confirmed". This service detects those
        signals and proposes ConstraintsPro updates to the user via Telegram.

        IMPORTANT: This is the EMAIL pipeline — it uses HUMAN-IN-THE-LOOP approval.
        The transcript pipeline (transcript_processor) has full auto-update authority.
        """
        if not self._bot or not self._chat_id:
            return

        body = (parsed.get("body") or "").strip()
        if not body:
            return

        sender = parsed.get("sender", "unknown")
        subject = parsed.get("subject", "")

        asyncio.create_task(
            self._run_reply_monitoring(
                subject=subject,
                body=body,
                sender=sender,
            ),
            name=f"reply-monitor-{sender[:20]}",
        )

    async def _run_reply_monitoring(
        self, subject: str, body: str, sender: str
    ) -> None:
        """Background task that checks an email reply for resolution signals."""
        try:
            from bot.services.email_reply_monitor import EmailReplyMonitor

            monitor = EmailReplyMonitor()
            proposals = await monitor.process_reply(
                bot=self._bot,
                chat_id=self._chat_id,
                subject=subject,
                body=body,
                sender=sender,
            )

            if proposals > 0:
                logger.info(
                    f"Email reply monitor: {proposals} proposal(s) sent "
                    f"from {sender}'s reply"
                )
        except Exception:
            logger.exception(
                f"Email reply monitoring failed for email from {sender}"
            )

    # ==================================================================
    # Hauger DSC summary processing (Feature #5b)
    # ==================================================================

    def _spawn_hauger_processing(self, parsed: dict) -> None:
        """Spawn a background task to process a Hauger DSC summary email.

        Unlike the generic constraint auto-logger, this does NOT create new
        constraints. Instead it:
          A. Matches constraint content to EXISTING constraints and appends notes
          B. Stores production data as intel in MemoryStore + project files
          C. Flags unmatched items for human review

        Fire-and-forget — runs asynchronously and never blocks the polling cycle.
        """
        if not self._bot or not self._chat_id:
            logger.debug(
                "Hauger processing skipped — no bot/chat_id configured"
            )
            return

        body = (parsed.get("body") or "").strip()
        if not body:
            logger.info(
                "Hauger processing skipped — email has no body text"
            )
            return

        sender = parsed.get("sender", "unknown")
        subject = parsed.get("subject", "")

        logger.info(
            f"Spawning Hauger DSC summary processor for email from {sender}: "
            f"{subject!r}"
        )

        asyncio.create_task(
            self._run_hauger_processing(
                email_body=body,
                sender=sender,
                subject=subject,
            ),
            name=f"hauger-processing-{sender}",
        )

    async def _run_hauger_processing(
        self,
        email_body: str,
        sender: str,
        subject: str,
    ) -> None:
        """Background task that runs the Hauger DSC summary processor.

        Catches all exceptions so it never crashes the bot process.
        """
        try:
            from bot.services.constraint_logger import ConstraintLogger

            clogger = ConstraintLogger()
            result = await clogger.process_hauger_email(
                bot=self._bot,
                chat_id=self._chat_id,
                email_body=email_body,
                sender=sender,
                subject=subject,
            )

            logger.info(
                f"Hauger DSC processor complete — "
                f"{result.get('notes_added', 0)} constraint(s) updated with notes, "
                f"{result.get('flagged_for_review', 0)} flagged for review, "
                f"{result.get('production_stored', 0)} production data points stored"
            )
        except Exception:
            logger.exception(
                f"Hauger DSC summary processing failed for email from {sender}"
            )

    # ==================================================================
    # Meeting invite processing (Option A — email-based Recall integration)
    # ==================================================================

    # DISABLED — meeting invite processing disabled to prevent automatic firing.
    # Code kept dormant for future re-enable. The meeting_invite_handler.py
    # file is also preserved unchanged.
    #
    # def _spawn_meeting_invite_processing(self, parsed: dict) -> None:
    #     """Spawn a background task to process a meeting invite email.
    #
    #     Extracts Teams URL, schedules a Recall.ai bot, and updates project
    #     contact lists from the attendee list. Fire-and-forget — never blocks
    #     the polling cycle.
    #     """
    #     if not self._bot or not self._chat_id:
    #         logger.debug(
    #             "Meeting invite processing skipped — no bot/chat_id configured"
    #         )
    #         return
    #
    #     sender = parsed.get("sender", "unknown")
    #     subject = parsed.get("subject", "")
    #
    #     logger.info(
    #         f"Spawning meeting invite processor for email from {sender}: "
    #         f"{subject!r}"
    #     )
    #
    #     asyncio.create_task(
    #         self._run_meeting_invite_processing(parsed),
    #         name=f"meeting-invite-{sender[:20]}",
    #     )
    #
    # async def _run_meeting_invite_processing(self, parsed: dict) -> None:
    #     """Background task that processes a meeting invite.
    #
    #     Catches all exceptions so it never crashes the bot process.
    #     """
    #     try:
    #         from bot.services.meeting_invite_handler import MeetingInviteHandler
    #
    #         # Get recall_service from bot_data if available
    #         recall_service = None
    #         if self._bot and hasattr(self._bot, "bot_data"):
    #             recall_service = self._bot.bot_data.get("recall_service")
    #
    #         handler = MeetingInviteHandler(recall_service=recall_service)
    #         result = await handler.process_meeting_invite(
    #             parsed=parsed,
    #             chat_id=self._chat_id,
    #             bot=self._bot,
    #         )
    #
    #         logger.info(
    #             f"Meeting invite processed — "
    #             f"bot_scheduled={result.get('bot_scheduled')}, "
    #             f"contacts_added={result.get('contacts_added', 0)}, "
    #             f"project={result.get('project_key', 'unknown')}"
    #         )
    #     except Exception:
    #         logger.exception(
    #             f"Meeting invite processing failed for email: "
    #             f"{parsed.get('subject', 'unknown')!r}"
    #         )

    # ==================================================================
    # IMAP operations
    # ==================================================================

    def _fetch_unread(self) -> list[bytes]:
        """Synchronous IMAP fetch — runs in executor thread.

        Connects to Gmail IMAP, searches for unread emails whose subject
        contains [INBOX:, fetches them, marks as read, and returns raw
        email bytes.
        """
        results = []

        try:
            # Connect with SSL (port 993)
            mail = imaplib.IMAP4_SSL(self.imap_host, 993, timeout=IMAP_TIMEOUT)
            mail.login(self.address, self.app_password)
            mail.select("INBOX")

            # Search for unread emails with [INBOX: in the subject
            # Gmail IMAP supports SUBJECT search
            status, data = mail.search(None, '(UNSEEN SUBJECT "[INBOX:")')

            if status != "OK" or not data or not data[0]:
                mail.logout()
                return results

            msg_ids = data[0].split()
            logger.debug(f"IMAP found {len(msg_ids)} unread [INBOX:] email(s)")

            for msg_id in msg_ids:
                try:
                    # Fetch the full email
                    status, msg_data = mail.fetch(msg_id, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_email = msg_data[0][1]
                    results.append(raw_email)

                    # Mark as read (add SEEN flag)
                    mail.store(msg_id, "+FLAGS", "\\Seen")

                except Exception:
                    logger.exception(f"Failed to fetch/mark IMAP message {msg_id}")

            mail.logout()

        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP error: {e}")
        except Exception:
            logger.exception("IMAP connection failed")

        return results

    # ==================================================================
    # Email parsing
    # ==================================================================

    def _parse_email(self, raw: bytes) -> Optional[dict]:
        """Parse a raw email and extract sender, subject, body, CC, attachments, and message ID.

        Expects the subject to contain [INBOX: sender@email.com] Original Subject.
        Optionally parses [CC: addr1, addr2] from the subject (added by PA relay).
        Also reads the standard Cc header as a fallback for CC recipients.
        Returns None if the subject doesn't match the expected format.
        """
        msg = email.message_from_bytes(raw)

        # Decode the subject header
        raw_subject = msg.get("Subject", "")
        decoded_parts = email.header.decode_header(raw_subject)
        subject_parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                subject_parts.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                subject_parts.append(part)
        full_subject = " ".join(subject_parts)

        # Parse the [INBOX: sender] tag from the subject
        match = INBOX_TAG_PATTERN.match(full_subject)
        if not match:
            logger.debug(f"Skipping email — no [INBOX:] tag in subject: {full_subject!r}")
            return None

        original_sender = match.group(1).strip()
        original_subject = match.group(2).strip()

        # ── Extract CC recipients ─────────────────────────────────────
        # Source 1: [CC: addr1, addr2] tag in subject (from PA relay)
        cc_from_tag = ""
        cc_tag_match = CC_TAG_PATTERN.match(original_subject)
        if cc_tag_match:
            cc_from_tag = cc_tag_match.group(1).strip()
            # Strip the CC tag from the subject so it doesn't pollute the display
            original_subject = cc_tag_match.group(2).strip()

        # Source 2: Standard email Cc header (fallback / merge)
        cc_from_header = ""
        raw_cc = msg.get("Cc", "") or msg.get("CC", "") or ""
        if raw_cc:
            # Decode the CC header (may be RFC 2047 encoded)
            cc_decoded_parts = email.header.decode_header(raw_cc)
            cc_header_parts = []
            for part, charset in cc_decoded_parts:
                if isinstance(part, bytes):
                    cc_header_parts.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    cc_header_parts.append(part)
            cc_from_header = " ".join(cc_header_parts).strip()

        # Build exclusion set: user's own addresses + the original sender
        # (sender becomes the To: recipient in the reply, so no need to CC them)
        exclude_addrs: set[str] = set()
        if self.address:
            exclude_addrs.add(self.address.lower().strip())
        relay = RELAY_TO_ADDRESS or ""
        if relay:
            exclude_addrs.add(relay.lower().strip())
        if original_sender:
            exclude_addrs.add(original_sender.lower().strip())

        # Merge CC from both sources (deduplicated, user/sender excluded)
        cc = self._merge_cc(cc_from_tag, cc_from_header, exclude=exclude_addrs)
        # ── End CC extraction ─────────────────────────────────────────

        # Extract message ID
        message_id = msg.get("Message-ID", "")

        # Extract body — prefer plain text, fall back to HTML
        body = self._extract_body(msg)

        # Extract file attachments
        attachments = self._extract_attachments(msg)

        return {
            "sender": original_sender,
            "subject": original_subject,
            "body": body,
            "cc": cc,
            "message_id": message_id,
            "attachments": attachments,
        }

    @staticmethod
    def _merge_cc(cc_from_tag: str, cc_from_header: str, exclude: set[str] | None = None) -> str:
        """Merge CC addresses from subject tag and email header, deduplicating.

        Both inputs are comma-separated email address strings (possibly with
        display names like '"John Doe" <john@example.com>').  We extract bare
        email addresses, deduplicate case-insensitively, and return a clean
        comma-separated string of bare addresses (or empty string if none).

        Args:
            cc_from_tag: CC addresses from [CC: ...] subject tag.
            cc_from_header: CC addresses from email Cc header.
            exclude: Optional set of lowercase email addresses to exclude
                     (e.g., the user's own addresses to avoid CC'ing yourself).
        """
        import email.utils as _eu

        all_raw = f"{cc_from_tag}, {cc_from_header}" if (cc_from_tag and cc_from_header) else (cc_from_tag or cc_from_header)
        if not all_raw.strip():
            return ""

        exclude = exclude or set()

        seen: set[str] = set()
        result: list[str] = []
        for _name, addr in _eu.getaddresses([all_raw]):
            addr = addr.strip().lower()
            if addr and addr not in seen and "@" in addr and addr not in exclude:
                seen.add(addr)
                result.append(addr)

        return ", ".join(result)

    def _extract_body(self, msg: email.message.Message) -> str:
        """Extract the email body, preferring plain text over HTML."""
        if msg.is_multipart():
            plain_text = ""
            html_text = ""
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))

                # Skip attachments
                if "attachment" in disposition:
                    continue

                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                except Exception:
                    continue

                if content_type == "text/plain" and not plain_text:
                    plain_text = text
                elif content_type == "text/html" and not html_text:
                    html_text = text

            return plain_text or html_text or ""
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
            except Exception:
                pass
            return ""

    def _extract_attachments(self, msg: email.message.Message) -> list[dict]:
        """Extract file attachments from a MIME email.

        Returns a list of dicts: [{'filename': str, 'data': bytes, 'content_type': str}]
        Only includes files with allowed extensions (see ALLOWED_EXTENSIONS).
        Skips inline images and tiny files (< 100 bytes).
        """
        attachments = []
        if not msg.is_multipart():
            return attachments

        for part in msg.walk():
            # Check if this part is an attachment
            disposition = str(part.get("Content-Disposition", ""))
            filename = part.get_filename()

            # Skip parts that aren't attachments
            if not filename and "attachment" not in disposition:
                continue

            if not filename:
                continue

            # Decode filename if needed (handles RFC 2231 / encoded headers)
            decoded = email.header.decode_header(filename)
            name_parts = []
            for p, ch in decoded:
                if isinstance(p, bytes):
                    name_parts.append(p.decode(ch or "utf-8", errors="replace"))
                else:
                    name_parts.append(p)
            filename = "".join(name_parts)

            # Check extension against allowlist
            ext = os.path.splitext(filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                logger.debug(f"Skipping attachment with non-allowed extension: {filename}")
                continue

            try:
                data = part.get_payload(decode=True)
                if not data or len(data) < 100:
                    # Skip tiny/empty attachments (likely signatures or spacers)
                    continue

                attachments.append({
                    "filename": filename,
                    "data": data,
                    "content_type": part.get_content_type(),
                })
                logger.debug(f"Extracted attachment: {filename} ({len(data):,} bytes)")
            except Exception:
                logger.exception(f"Failed to extract attachment: {filename}")

        return attachments
