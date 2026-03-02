"""Gmail SMTP email sender for the Power Automate relay flow.

Outbound flow:
  1. Nimrod drafts a response to an inbound email
  2. User approves via Telegram inline buttons
  3. This service sends the email via Gmail SMTP with a tagged subject:
     [SEND: recipient@email.com] Original Subject
  4. Power Automate picks it up from Gmail and forwards from the user's
     MasTec Outlook account

Uses port 587 (STARTTLS) as primary, port 465 (SSL) as fallback.
Credentials come from env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD.
"""

import asyncio
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape
from typing import Optional

from bot.config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_SMTP_HOST, RELAY_TO_ADDRESS

logger = logging.getLogger(__name__)


def _format_email_html(text: str) -> str:
    """Convert plain-text email draft to well-formatted HTML.

    Handles paragraph spacing, single line breaks, bullet/numbered lists,
    and wraps everything in a clean HTML email template.
    """
    text = text.strip()
    if not text:
        return text

    # Escape HTML special chars in the source text
    text = html_escape(text)

    # Split on double-newlines to get paragraphs
    raw_paragraphs = re.split(r'\n\s*\n', text)

    html_parts = []
    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue

        lines = para.split('\n')

        # Detect bullet lists (lines starting with - • * or numbered 1. 2. etc.)
        list_lines = [
            l for l in lines
            if re.match(r'^\s*(?:[-•*]|\d+[.)]\s)', l.strip())
        ]
        is_list = len(list_lines) == len([l for l in lines if l.strip()])

        if is_list and list_lines:
            items = []
            for line in lines:
                cleaned = re.sub(r'^\s*(?:[-•*]|\d+[.)]\s*)\s*', '', line.strip())
                if cleaned:
                    items.append(f'  <li style="margin-bottom:4px;">{cleaned}</li>')
            html_parts.append(
                '<ul style="margin:0 0 12px 20px;padding:0;">\n'
                + '\n'.join(items)
                + '\n</ul>'
            )
        else:
            # Regular paragraph — preserve single line breaks as <br>
            formatted = '<br>\n'.join(l.strip() for l in lines)
            html_parts.append(
                f'<p style="margin:0 0 12px 0;line-height:1.5;">{formatted}</p>'
            )

    body_html = '\n'.join(html_parts)

    # Wrap in a minimal email template with sensible defaults
    return (
        '<div style="font-family:Calibri,Arial,sans-serif;font-size:14px;'
        'color:#1a1a1a;line-height:1.5;">\n'
        f'{body_html}\n'
        '</div>'
    )

# Connection timeout for SMTP operations
SMTP_TIMEOUT = 15


class EmailService:
    """Async-friendly Gmail SMTP sender.

    All SMTP operations run in a thread executor to avoid blocking the
    event loop, since smtplib is synchronous.
    """

    def __init__(
        self,
        address: str = None,
        app_password: str = None,
        smtp_host: str = None,
    ):
        self.address = address or GMAIL_ADDRESS
        self.app_password = app_password or GMAIL_APP_PASSWORD
        self.smtp_host = smtp_host or GMAIL_SMTP_HOST
        self._queue = None  # Set by main.py after initialization

    @property
    def is_configured(self) -> bool:
        """Return True if Gmail credentials are present."""
        return bool(self.address and self.app_password)

    def set_queue(self, queue) -> None:
        """Attach the message queue (called during bot initialization)."""
        self._queue = queue

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
    ) -> bool:
        """Send an email via Gmail SMTP with [SEND: to] subject tag.

        Args:
            to: Recipient email address.
            subject: Original subject line (will be wrapped with [SEND: to] tag).
            body: Email body in HTML format.
            cc: Optional CC address(es), comma-separated.

        Returns:
            True on success, False on failure.
        """
        if not self.is_configured:
            logger.error("Email service not configured — GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing")
            return False

        # Auto-convert plain text to well-formatted HTML if the body doesn't
        # already contain real HTML structure tags.  This prevents the
        # "wall of text" problem where newlines and numbered lists get lost.
        if not re.search(r'<(p|div|table|ul|ol)\b', body):
            body = _format_email_html(body)

        tagged_subject = f"[SEND: {to}] {subject}"
        if cc:
            # Add CC tag so Power Automate relay can parse and add CC recipients
            tagged_subject = f"[SEND: {to}] [CC: {cc}] {subject}"

        # Route through PA relay: send to user's MasTec Outlook, not directly to recipient.
        # PA picks it up, parses [SEND: recipient], sends from user's work email, deletes relay.
        relay = RELAY_TO_ADDRESS
        if not relay:
            logger.warning("RELAY_TO_ADDRESS not set — sending directly to recipient (bypasses PA)")
            relay = to

        msg = MIMEMultipart("alternative")
        msg["From"] = self.address
        msg["To"] = relay
        msg["Subject"] = tagged_subject
        if cc:
            msg["Cc"] = cc

        # Attach HTML body (primary) and plain-text fallback
        plain_body = body.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        # Strip remaining HTML tags for plain-text version
        plain_body = re.sub(r"<[^>]+>", "", plain_body)
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))

        # Build recipient list for SMTP envelope — send to relay, not to final recipient
        recipients = [relay]
        if cc:
            recipients.extend([addr.strip() for addr in cc.split(",") if addr.strip()])

        loop = asyncio.get_running_loop()
        try:
            success = await loop.run_in_executor(
                None, lambda: self._smtp_send(msg, recipients)
            )
            if success:
                cc_info = f" cc={cc}" if cc else ""
                logger.info(f"Email sent to {to}{cc_info} — subject: {tagged_subject}")
            return success
        except Exception:
            logger.exception(f"Failed to send email to {to}")
            return False

    def _smtp_send(self, msg: MIMEMultipart, recipients: list[str]) -> bool:
        """Synchronous SMTP send — runs in executor thread.

        Tries port 587 (STARTTLS) first, falls back to port 465 (SSL).
        """
        # Attempt 1: Port 587 with STARTTLS
        try:
            with smtplib.SMTP(self.smtp_host, 587, timeout=SMTP_TIMEOUT) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.address, self.app_password)
                server.sendmail(self.address, recipients, msg.as_string())
            logger.debug("SMTP send succeeded via port 587 (STARTTLS)")
            return True
        except Exception as e:
            logger.warning(f"SMTP port 587 failed: {e} — trying port 465 (SSL)")

        # Attempt 2: Port 465 with SSL
        try:
            with smtplib.SMTP_SSL(self.smtp_host, 465, timeout=SMTP_TIMEOUT) as server:
                server.ehlo()
                server.login(self.address, self.app_password)
                server.sendmail(self.address, recipients, msg.as_string())
            logger.debug("SMTP send succeeded via port 465 (SSL)")
            return True
        except Exception as e:
            logger.error(f"SMTP port 465 also failed: {e}")
            return False

    async def send_approved_message(self, message_id: int) -> bool:
        """Pull an approved message from the queue and send it via email.

        Args:
            message_id: The queue item ID to send.

        Returns:
            True if the email was sent and the queue item marked as sent,
            False otherwise.
        """
        if not self._queue:
            logger.error("EmailService.send_approved_message called but no queue attached")
            return False

        item = await self._queue.get_by_id(message_id)
        if not item:
            logger.error(f"Queue item {message_id} not found")
            return False

        if item["status"] != "approved":
            logger.warning(f"Queue item {message_id} status is '{item['status']}', expected 'approved'")
            return False

        # Determine recipient and response text
        recipient = item["sender"]  # Reply to whoever sent the inbound email
        if not recipient:
            logger.error(f"Queue item {message_id} has no sender to reply to")
            return False

        subject = item["subject"] or "(no subject)"
        # Prefix with Re: if not already there
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        response_body = item["approved_response"] or item["draft_response"] or ""
        if not response_body:
            logger.error(f"Queue item {message_id} has no response text")
            return False

        # Convert plain text to properly formatted HTML email
        # Only skip conversion if the body already has real HTML structure tags
        if not re.search(r'<(p|div|table|ul|ol)\b', response_body):
            response_body = _format_email_html(response_body)

        # Extract CC from queue item (may be None if not set)
        cc = item.get("cc") or None

        success = await self.send_email(
            to=recipient,
            subject=subject,
            body=response_body,
            cc=cc,
        )

        if success:
            # Mark as sent in the queue
            await self._queue.mark_sent(message_id)
            logger.info(f"Queue item {message_id} sent and marked as sent")
        else:
            logger.error(f"Failed to send queue item {message_id} — status remains 'approved'")

        return success
