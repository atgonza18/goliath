"""Gmail IMAP poller for the Power Automate inbound email relay.

Inbound flow:
  1. Power Automate forwards Office 365 emails to a Gmail account with
     tagged subjects: [INBOX: sender@email.com] Original Subject
  2. This poller connects via IMAP and reads unread emails with [INBOX: tags
  3. Parses the original sender address from the tag
  4. Feeds each email into the message queue as an inbound item
  5. Marks the email as read so it won't be re-processed

Credentials come from env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST.
"""

import asyncio
import email
import email.header
import imaplib
import logging
import re
from typing import Optional

from bot.config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST

logger = logging.getLogger(__name__)

# Regex to parse [INBOX: sender@email.com] from the subject line
INBOX_TAG_PATTERN = re.compile(r"\[INBOX:\s*([^\]]+)\]\s*(.*)", re.IGNORECASE)

# IMAP connection timeout
IMAP_TIMEOUT = 15


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
        self._queue = None  # Set by main.py after initialization

    @property
    def is_configured(self) -> bool:
        """Return True if Gmail credentials are present."""
        return bool(self.address and self.app_password)

    def set_queue(self, queue) -> None:
        """Attach the message queue (called during bot initialization)."""
        self._queue = queue

    async def poll(self) -> int:
        """Run a single poll cycle: fetch unread [INBOX:] emails and enqueue them.

        Returns:
            Number of emails processed.
        """
        if not self.is_configured:
            logger.debug("Email poller not configured — GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing")
            return 0

        if not self._queue:
            logger.error("EmailPoller.poll called but no queue attached")
            return 0

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

                # ── Safety filter: skip outbound relay echoes ────────────
                # If the subject contains [SEND:, this is an outbound relay
                # email that bounced back — NOT a real inbound. Skip it.
                subj = parsed.get("subject", "")
                if "[SEND:" in subj.upper():
                    logger.info(
                        f"Skipping relay echo — subject contains [SEND:]: {subj!r}"
                    )
                    continue
                # ── End safety filter ────────────────────────────────────

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
            except Exception:
                logger.exception("Failed to enqueue polled email")

        logger.info(f"Email poll cycle complete: {count} email(s) queued")
        return count

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

    def _parse_email(self, raw: bytes) -> Optional[dict]:
        """Parse a raw email and extract sender, subject, body, and message ID.

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

        return {
            "sender": original_sender,
            "subject": original_subject,
            "body": body,
            "message_id": message_id,
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
