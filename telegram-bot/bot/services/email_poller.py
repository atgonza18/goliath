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
  - Only files for portfolio projects (defined in config.PROJECTS)
  - Non-portfolio project emails with attachments are silently skipped

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
from datetime import datetime
from pathlib import Path
from typing import Optional

from bot.config import (
    GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST,
    PROJECTS, PROJECTS_DIR, REPORT_CHAT_ID, CONSTRAINTS_REPORTS_DIR,
    RELAY_TO_ADDRESS, match_project_key,
)

logger = logging.getLogger(__name__)

# Regex to parse [INBOX: sender@email.com] from the subject line
INBOX_TAG_PATTERN = re.compile(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", re.IGNORECASE)

# IMAP connection timeout
IMAP_TIMEOUT = 15

# Allowed attachment file extensions (lowercase, with leading dot)
ALLOWED_EXTENSIONS = {
    '.pdf', '.xlsx', '.xls', '.csv', '.doc', '.docx',
    '.ppt', '.pptx', '.png', '.jpg', '.jpeg', '.zip', '.msg',
}

# Known constraints update senders (case-insensitive partial match on email address)
CONSTRAINTS_SENDERS = ['hauger', 'hogger']

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

                    # ── Self-forward detection: skip emails from user's own address ──
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

                    # ── Classify and route ────────────────────────────────
                    classification = self._classify_email(parsed)
                    attachments = parsed.get("attachments", [])

                    if classification in ("pod", "constraints"):
                        # POD/constraints emails NEVER fall through to draft queue
                        if attachments:
                            await self._file_attachments(
                                parsed, classification, attachments
                            )
                        else:
                            logger.info(
                                f"{classification.upper()} email received but no "
                                f"attachments — skipping: {parsed['subject']!r}"
                            )
                        count += 1
                        _mark_processed(dedup)
                        continue

                    # ── Normal email: enqueue for draft response ──────────
                    await self._queue.enqueue(
                        source="email",
                        direction="inbound",
                        sender=parsed["sender"],
                        subject=parsed["subject"],
                        body=parsed["body"],
                        external_message_id=parsed.get("message_id"),
                    )
                    count += 1
                    logger.info(
                        f"Polled inbound email from {parsed['sender']} — "
                        f"subject: {parsed['subject']!r}"
                    )
                    _mark_processed(dedup)

                except Exception:
                    logger.exception("Failed to process polled email")

            logger.info(f"Email poll cycle complete: {count} email(s) processed")
            return count

    # ==================================================================
    # Email classification
    # ==================================================================

    def _classify_email(self, parsed: dict) -> str:
        """Classify an email as 'pod', 'constraints', or 'normal'.

        Returns:
            'pod'         — Production/POD report email
            'constraints' — Constraints update (typically from Joshua Hauger)
            'normal'      — Everything else (queued for draft response)
        """
        subject = (parsed.get("subject") or "").lower()
        sender = (parsed.get("sender") or "").lower()

        # POD detection: subject contains the word "POD" (word boundary)
        if re.search(r'\bpod\b', subject):
            return "pod"

        # Constraints detection: from known constraints senders
        for name in CONSTRAINTS_SENDERS:
            if name in sender:
                return "constraints"

        # Constraints detection: subject mentions constraints + has attachments
        if "constraint" in subject and parsed.get("attachments"):
            return "constraints"

        return "normal"

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

        Returns True if at least one file was saved, False otherwise.
        """
        subject = parsed.get("subject", "")
        sender = parsed.get("sender", "unknown")
        today = datetime.now().strftime("%Y-%m-%d")

        if classification == "pod":
            return await self._file_pod_attachments(
                subject, sender, attachments, today
            )
        elif classification == "constraints":
            return await self._file_constraints_attachments(
                subject, sender, attachments, today
            )
        return False

    async def _file_pod_attachments(
        self, subject: str, sender: str, attachments: list[dict], today: str
    ) -> bool:
        """File POD attachments to the matching project's pod/ folder.

        Only saves for portfolio projects. Non-portfolio PODs are skipped.
        """
        # Match project from subject first, then body isn't available here
        # but subject is usually "Please see POD for today - Project Name"
        project_key = match_project_key(subject)
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
            await self._notify_attachment_filed(
                project_name=project_name,
                project_key=project_key,
                classification="pod",
                sender=sender,
                subject=subject,
                files=saved_files,
            )
        return bool(saved_files)

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

    def _save_attachments(
        self, attachments: list[dict], target_dir: Path, today: str
    ) -> list[Path]:
        """Save a list of attachments to a target directory.

        Adds date prefix if filename doesn't already have one.
        Avoids overwriting by appending a numeric suffix.

        Returns list of saved file paths.
        """
        saved = []
        for att in attachments:
            filename = att["filename"]
            data = att["data"]

            # Sanitize filename — keep alphanumeric, hyphens, dots, underscores, spaces, parens
            safe_name = re.sub(r'[^\w\-._() ]', '', filename).strip()
            if not safe_name:
                safe_name = f"attachment_{len(saved) + 1}.bin"

            # Add date prefix if filename doesn't already start with a date pattern
            if not re.match(r'^\d{4}[-_]\d{2}[-_]\d{2}', safe_name):
                safe_name = f"{today}_{safe_name}"

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
                filepath.write_bytes(data)
                saved.append(filepath)
                logger.info(f"Saved attachment: {filepath}")
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
        """Parse a raw email and extract sender, subject, body, attachments, and message ID.

        Expects the subject to contain [INBOX: sender@email.com] Original Subject.
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
            "message_id": message_id,
            "attachments": attachments,
        }

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
