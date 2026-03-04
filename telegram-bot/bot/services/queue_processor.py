"""Background async processor for the message queue.

Picks up new inbound messages and routes them:
  - Email/Teams items → Nimrod draft → approval request to Telegram (human-in-the-loop)
  - Recall transcript items → full orchestrator pipeline → direct to Telegram
    (transcript processing → constraint extraction → AUTO cross-reference against
     ConstraintsPro → deduped sync proposal to user → user says "push it")

The transcript bypass was added because transcripts should go straight to analysis,
not through the email draft/approval flow. The constraint cross-reference against
ConstraintsPro happens AUTOMATICALLY after extraction — duplicates are caught, existing
constraints matched, and the user sees a clean "here's what we'd push" summary.
The user just says "push it" to sync (no manual dedup step needed).
"""

import asyncio
import logging
import re
from pathlib import Path

from bot.agents.orchestrator import NimrodOrchestrator
from bot.config import REPO_ROOT
from bot.services.message_queue import MessageQueue

logger = logging.getLogger(__name__)

# Sources that should bypass the email draft/approval flow and go
# straight through the full orchestrator pipeline (transcript processing,
# constraint extraction, sync proposal).
DIRECT_PROCESSING_SOURCES = {"recall_transcript"}

# Cache soul.md content at module level (loaded once)
_soul_md_cache: str | None = None


def _load_soul_md() -> str:
    """Load soul.md communication profile. Cached after first read."""
    global _soul_md_cache
    if _soul_md_cache is not None:
        return _soul_md_cache
    soul_path = REPO_ROOT / "soul.md"
    try:
        _soul_md_cache = soul_path.read_text(encoding="utf-8")
        return _soul_md_cache
    except FileNotFoundError:
        logger.warning("soul.md not found at %s — drafts may lack voice context", soul_path)
        _soul_md_cache = ""
        return _soul_md_cache


def _extract_clean_draft(raw_text: str) -> str:
    """Extract ONLY the clean email body from Nimrod's response.

    Nimrod sometimes wraps the draft with conversational preamble
    ("Here's the draft...", "Damn, that's a lot of data...") and trailing
    commentary ("Want me to tweak anything?", "That's the draft.").

    This function strips everything before the actual email greeting and
    everything after the signoff, returning only what should be sent.
    """
    text = raw_text.strip()
    if not text:
        return text

    # ── Step 1: Find the email start ─────────────────────────────────
    # Look for a standard email greeting (Hi/Hello/Hey/Dear/Good morning etc.)
    greeting_pattern = re.compile(
        r'^(Hi|Hello|Hey|Dear|Good\s+(?:morning|afternoon|evening))\b',
        re.MULTILINE | re.IGNORECASE,
    )
    match = greeting_pattern.search(text)
    if match:
        text = text[match.start():]
    else:
        # No greeting found — try cutting at common preamble markers
        preamble_markers = [
            "---\n",
            "here's the draft email:",
            "here is the draft email:",
            "here's the draft:",
            "here is the draft:",
            "here's the email:",
            "here is the email:",
            "draft email:",
            "draft response:",
        ]
        for marker in preamble_markers:
            idx = text.lower().find(marker.lower())
            if idx != -1:
                after = text[idx + len(marker):].strip()
                if after:
                    text = after
                    break

    # ── Step 2: Find the email end ───────────────────────────────────
    # Look for Aaron's signoff and cut everything after it
    signoff_pattern = re.compile(
        r'((?:Thanks|Thank\s+you|Best|Regards|Best\s+regards|Sincerely|Cheers|V/r|Respectfully),?'
        r'\s*\n\s*Aaron(?:\s+Gonzalez)?)',
        re.IGNORECASE,
    )
    signoff_match = signoff_pattern.search(text)
    if signoff_match:
        text = text[: signoff_match.end()].strip()
    else:
        # No signoff found — cut at common trailing commentary markers
        trail_markers = [
            "\n---",
            "\nthat's the draft",
            "\nwant me to",
            "\nlet me know if",
            "\nshould i",
            "\ndoes this work",
            "\ni kept",
            "\nthis hits",
            "\ni've kept",
            "\nthis draft",
            "\nfeel free to",
        ]
        for marker in trail_markers:
            idx = text.lower().find(marker.lower())
            if idx > 50:  # Only cut if there's enough real content before it
                text = text[:idx].strip()
                break

    # ── Step 3: Clean up any remaining artifacts ─────────────────────
    # Remove stray markdown-style formatting
    text = re.sub(r'^```\w*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()

    return text


def _build_draft_prompt(item: dict) -> str:
    source = item["source"]
    sender = item["sender"] or "Unknown"
    subject = item["subject"] or "(no subject)"
    body = item["body"] or "(empty)"
    channel = item.get("channel") or ""

    # Load soul.md for communication style
    soul = _load_soul_md()
    soul_section = ""
    if soul:
        soul_section = (
            f"\n\n== AARON'S COMMUNICATION PROFILE (soul.md) ==\n"
            f"{soul}\n"
            f"== END COMMUNICATION PROFILE ==\n"
        )

    # Project list for context matching
    projects = (
        "Union Ridge, Duff, Salt Branch, Blackford, Delta Bobcat, Tehuacana, "
        "Three Rivers, Scioto Ridge, Mayes, Graceland, Pecan Prairie, Duffy BESS"
    )

    cc = item.get("cc") or ""

    if source == "email":
        cc_line = f"CC: {cc}\n" if cc else ""
        cc_note = (
            f"\nNOTE: This is a reply-all — the CC recipients ({cc}) will also "
            f"receive your reply. Keep your tone and content appropriate for all "
            f"parties on the thread.\n"
        ) if cc else ""
        return (
            f"You are drafting an email reply AS Aaron Gonzalez (MasTec DSC). "
            f"You are his double — you respond the way he would, with the same "
            f"knowledge and authority.\n\n"
            f"== INBOUND EMAIL ==\n"
            f"From: {sender}\n"
            f"{cc_line}"
            f"Subject: {subject}\n"
            f"Body:\n{body}\n"
            f"== END EMAIL ==\n"
            f"{cc_note}"
            f"{soul_section}\n"
            f"== YOUR MISSION ==\n"
            f"1. UNDERSTAND what the sender is asking for or communicating about.\n"
            f"2. RESEARCH: Use your tools to look up relevant project data. Search "
            f"   files under /opt/goliath/projects/ and your memory context for:\n"
            f"   - Constraints, procurement status, open items\n"
            f"   - Schedule data, milestones, critical path risks\n"
            f"   - Production data, manpower, weather impacts\n"
            f"   - Any facts relevant to the email's topic\n"
            f"   The portfolio projects are: {projects}\n"
            f"3. DRAFT a substantive reply that ACTUALLY ANSWERS their question or "
            f"   provides the update they need — with real data, specific dates, "
            f"   numbers, and next steps. If they asked for a report, include the "
            f"   data. If they asked for status, give the status. If they raised a "
            f"   concern, address it with facts.\n"
            f"4. If you genuinely don't have the data to answer, say what you DO "
            f"   know and commit to following up with specifics by a certain time. "
            f"   Never just say 'I'm looking into it' without substance.\n\n"
            f"== OUTPUT FORMAT RULES ==\n"
            f"Your ENTIRE response must be the email body and NOTHING ELSE.\n"
            f"- Start with a greeting (e.g., 'Hi [Name],' or 'Hello,').\n"
            f"- End with a signoff (e.g., 'Thanks,\\nAaron' or 'Best,\\nAaron').\n"
            f"- Use plain text only. No HTML tags, no markdown, no emojis.\n"
            f"- Use proper paragraph breaks for readability.\n"
            f"- Use bullet points (- ) for lists when appropriate.\n"
            f"- DO NOT include any preamble like 'Here is the draft' or 'Sure' or 'Alright'.\n"
            f"- DO NOT add commentary after the signoff.\n"
            f"- DO NOT use '---' separators or code blocks.\n"
            f"- DO NOT dump raw internal analysis or Telegram-style briefings.\n"
            f"- DO include relevant project facts naturally woven into a professional response.\n"
            f"  Example: Instead of 'checking on procurement,' write 'The DC collection cable "
            f"  PO was submitted on 2/15 and we're tracking a 12-week lead time, putting "
            f"  delivery around 5/10. I'll confirm with the vendor this week.'\n\n"
            f"Write ONLY the email body. Nothing before it. Nothing after it.\n"
        )
    else:
        context = f"in channel #{channel}" if channel else "as a DM"
        return (
            f"You received the following Teams message {context}.\n\n"
            f"From: {sender}\n"
            f"Message: {body}\n"
            f"{soul_section}\n"
            f"You are responding AS Aaron Gonzalez (MasTec DSC). You are his double.\n\n"
            f"RESEARCH the topic using your tools and memory. Look up relevant project "
            f"data under /opt/goliath/projects/ if applicable. The portfolio projects are: "
            f"{projects}\n\n"
            f"Draft a substantive response with real data and next steps — not filler.\n"
            f"Output ONLY the response text — no preamble, no explanation, no commentary.\n"
            f"Use plain text. Be concise and direct. Follow Aaron's communication style "
            f"from soul.md."
        )


async def process_queue_once(queue: MessageQueue, memory, bot, chat_id: int) -> int:
    """Process all pending items. Returns number of items processed.

    Routes items based on their source:
      - recall_transcript → direct orchestrator pipeline (transcript processing,
        constraint extraction, sync proposal). No email-draft approval step.
      - email / teams / other → Nimrod draft + approval request to Telegram.
    """
    pending = await queue.get_pending()
    if not pending:
        return 0

    processed = 0

    for item in pending:
        source = item.get("source", "")

        if source in DIRECT_PROCESSING_SOURCES:
            # ── TRANSCRIPT PATH: bypass email-draft approval, go straight
            # through the full orchestrator pipeline ──
            try:
                count = await _process_transcript_item(item, queue, memory, bot, chat_id)
                processed += count
            except Exception:
                logger.exception(
                    f"Failed to process transcript queue item {item['id']} "
                    f"(source={source})"
                )
        else:
            # ── EMAIL/TEAMS PATH: draft + approval ──
            try:
                orchestrator = NimrodOrchestrator(memory=memory)
                prompt = _build_draft_prompt(item)
                result = await asyncio.wait_for(
                    orchestrator.handle_message(prompt),
                    timeout=300,
                )

                raw_draft = result.text
                draft = _extract_clean_draft(raw_draft)
                if not draft:
                    # Extraction stripped everything — fall back to raw
                    logger.warning(
                        f"Draft extraction returned empty for item {item['id']} — using raw text"
                    )
                    draft = raw_draft.strip()
                await queue.update_draft(item["id"], draft)

                # Send approval request to Telegram
                from bot.handlers.approval import send_approval_request
                updated_item = await queue.get_by_id(item["id"])
                await send_approval_request(bot, chat_id, updated_item)

                processed += 1
                logger.info(f"Processed queue item {item['id']} — draft ready for approval")

            except asyncio.TimeoutError:
                logger.error(f"Queue item {item['id']} timed out during Nimrod processing")
            except Exception:
                logger.exception(f"Failed to process queue item {item['id']}")

    return processed


async def _process_transcript_item(
    item: dict, queue: MessageQueue, memory, bot, chat_id: int
) -> int:
    """Route a recall_transcript queue item directly through the orchestrator.

    This bypasses the email-draft/approval flow entirely. The transcript goes
    straight to transcript_processor for analysis, constraint extraction, and
    sync proposal generation. The user still approves constraint pushes to
    ConstraintsPro (that approval lives in the orchestrator's sync proposal flow,
    not in the email-approval flow).

    Returns 1 on success, 0 on failure.
    """
    from bot.utils.formatting import chunk_message

    item_id = item["id"]
    body = item.get("body") or ""

    # Use the item's own chat_id if set (e.g., from the Recall bot's session),
    # otherwise fall back to the global REPORT_CHAT_ID.
    target_chat_id = item.get("telegram_chat_id") or chat_id

    logger.info(
        f"Transcript queue item {item_id}: routing directly to orchestrator "
        f"(bypassing email-draft approval, target_chat_id={target_chat_id})"
    )

    # Mark the queue item as being processed so it's not picked up again.
    # We use 'processing' status to take it out of the 'new' pool immediately.
    try:
        await queue._db.execute(
            "UPDATE message_queue SET status = 'processing', "
            "processed_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?",
            (item_id,),
        )
        await queue._db.commit()
    except Exception:
        logger.exception(f"Failed to mark transcript item {item_id} as processing")

    # Run the full orchestrator pipeline — same path as a user Telegram message.
    # The orchestrator will:
    #   1. Route to transcript_processor subagent (based on the body content)
    #   2. Extract constraints via CONSTRAINTS_SYNC block
    #   3. Generate a sync proposal (human-in-the-loop for ConstraintsPro pushes)
    #   4. Return the full analysis + sync proposal text
    orchestrator = NimrodOrchestrator(memory=memory)

    try:
        result = await asyncio.wait_for(
            orchestrator.handle_message(body),
            timeout=600,  # transcripts can be long — 10 min timeout
        )
    except asyncio.TimeoutError:
        logger.error(f"Transcript queue item {item_id} timed out during orchestrator processing")
        # Revert to 'new' so it can be retried next cycle
        try:
            await queue._db.execute(
                "UPDATE message_queue SET status = 'new' WHERE id = ?",
                (item_id,),
            )
            await queue._db.commit()
        except Exception:
            pass
        return 0

    result_text = result.text
    file_paths = result.file_paths

    # Mark the queue item as fully processed (not pending_approval — no approval needed)
    try:
        await queue._db.execute(
            "UPDATE message_queue SET status = 'sent', draft_response = ?, "
            "sent_at = strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id = ?",
            (result_text[:5000], item_id),
        )
        await queue._db.commit()
    except Exception:
        logger.exception(f"Failed to mark transcript item {item_id} as sent")

    # Send the analysis results directly to Telegram (no approval gate)
    try:
        for chunk in chunk_message(result_text, max_len=4000):
            try:
                await bot.send_message(chat_id=target_chat_id, text=chunk, parse_mode="HTML")
            except Exception:
                # Fall back to plain text if HTML parsing fails
                await bot.send_message(chat_id=target_chat_id, text=chunk)

        # Send any generated files (PDFs, processed transcript summaries, etc.)
        for fp in file_paths:
            file_path = Path(fp)
            if file_path.is_file():
                try:
                    with open(file_path, "rb") as f:
                        await bot.send_document(
                            chat_id=target_chat_id, document=f, filename=file_path.name
                        )
                    logger.info(f"Sent transcript file to Telegram: {file_path}")
                except Exception:
                    logger.exception(f"Failed to send transcript file: {file_path}")

    except Exception:
        logger.exception(f"Failed to send transcript results to Telegram for item {item_id}")

    logger.info(
        f"Transcript queue item {item_id} processed and delivered to Telegram "
        f"(bypassed email-draft approval)"
    )
    return 1


async def run_queue_processor(queue: MessageQueue, memory, bot, chat_id: int, interval: int = 30):
    """Background loop that processes the queue every `interval` seconds."""
    logger.info(f"Queue processor started (interval={interval}s, chat_id={chat_id})")
    while True:
        try:
            count = await process_queue_once(queue, memory, bot, chat_id)
            if count:
                logger.info(f"Queue processor handled {count} item(s)")
        except Exception:
            logger.exception("Queue processor error")

        await asyncio.sleep(interval)
