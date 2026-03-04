"""
Email Reply Monitor — Human-in-the-Loop constraint update proposals.

After the user sends follow-up emails (copied from the daily PDF), this service:
  1. Monitors the email inbox for replies to those follow-ups
  2. Parses replies for resolution signals (e.g., "PO submitted", "delivery confirmed")
  3. When a signal is detected, sends a Telegram proposal (NOT auto-update)
  4. Waits for user approval before pushing any changes to ConstraintsPro

Proposed change types:
  - Resolve a constraint
  - Drop priority (High -> Medium, Medium -> Low)
  - Update notes with new information
  - Combination of above

CRITICAL: This is the EMAIL pipeline. It uses HUMAN-IN-THE-LOOP approval.
The TRANSCRIPT pipeline (transcript_processor) has FULL auto-update authority
because it has complete meeting context. These two trust levels are intentional.

Matching strategy (v2 — LLM-powered):
  1. Extract project from subject line + contact directory (primary filter)
  2. Filter constraints to the identified project only
  3. Send email context + candidate constraints to Claude Haiku for semantic matching
  4. Confidence thresholds: >=0.8 normal, 0.5-0.8 flagged, <0.5 manual fallback
  5. Falls back to word-overlap matching if LLM call fails
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import MEMORY_DB_PATH, REPO_ROOT

CT = ZoneInfo("America/Chicago")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolution signal patterns — phrases that indicate constraint progress
# ---------------------------------------------------------------------------

RESOLUTION_SIGNALS = {
    "resolved": {
        "patterns": [
            r"(?:has been|is|was)\s+(?:resolved|completed|closed|done|finished)",
            r"(?:issue|constraint|item|problem)\s+(?:is\s+)?(?:resolved|closed|fixed|done)",
            r"(?:we(?:'ve| have)|i(?:'ve| have))\s+(?:resolved|completed|closed|fixed|finished)",
            r"(?:all|everything)\s+(?:is\s+)?(?:good|clear|set|done|resolved)",
            r"(?:taken care of|wrapped up|buttoned up|signed off)",
        ],
        "proposed_action": "resolve",
        "confidence_threshold": 0.7,
    },
    "delivery_confirmed": {
        "patterns": [
            r"(?:delivery|shipment|order)\s+(?:(?:has been|is|was)\s+)?(?:confirmed|scheduled|shipped|en route|on the way)",
            r"(?:material|equipment|modules?|tracker|inverter)s?\s+(?:(?:has been|is|was|have been)\s+)?(?:shipped|delivered|arriving|on site|received)",
            r"(?:po |purchase order)\s*(?:#?\d+)?\s*(?:(?:has been|is|was)\s+)?(?:submitted|placed|confirmed|approved)",
            r"(?:tracking|shipment)\s+(?:number|#|info)",
            r"(?:eta|estimated|expected)\s+(?:delivery|arrival)",
            r"(?:scheduled for delivery|arriving|will arrive|on the truck)",
            r"(?:delivered|arrived)\s+(?:to|on|at)\s+(?:the\s+)?(?:site|jobsite|warehouse|yard)",
        ],
        "proposed_action": "update_notes",
        "confidence_threshold": 0.6,
    },
    "approved": {
        "patterns": [
            r"(?:has been|is|was)\s+(?:approved|signed|authorized|permitted|stamped)",
            r"(?:permit|approval|authorization|sign-?off)\s+(?:received|granted|issued|obtained)",
            r"(?:we got|received)\s+(?:the\s+)?(?:approval|permit|sign-?off|authorization)",
            r"(?:green light|go ahead|proceed|approved to proceed)",
        ],
        "proposed_action": "resolve",
        "confidence_threshold": 0.7,
    },
    "in_progress": {
        "patterns": [
            r"(?:working on|in progress|underway|started|begun|kicked off)",
            r"(?:should be|will be|expected)\s+(?:done|ready|complete|finished)\s+(?:by|next|this|within)",
            r"(?:scheduled for|planning to|going to)\s+(?:next|this|tomorrow)",
            r"(?:crew|team)\s+(?:is\s+)?(?:on it|working|mobilized|deployed)",
        ],
        "proposed_action": "drop_priority",
        "confidence_threshold": 0.5,
    },
    "partial_resolution": {
        "patterns": [
            r"(?:partially|partly|half)\s+(?:resolved|done|complete|delivered)",
            r"(?:first|initial)\s+(?:batch|shipment|phase|part)\s+(?:delivered|complete|done)",
            r"(?:some|partial)\s+(?:of the|materials?|equipment)\s+(?:arrived|delivered|received)",
        ],
        "proposed_action": "update_notes",
        "confidence_threshold": 0.5,
    },
}


# ---------------------------------------------------------------------------
# LLM matching configuration
# ---------------------------------------------------------------------------

# Model for constraint matching — fast and cheap, this is a focused decision
LLM_MATCH_MODEL = "claude-3-5-haiku-latest"
LLM_MATCH_MAX_TOKENS = 500

# Confidence thresholds
CONFIDENCE_HIGH = 0.8
CONFIDENCE_MEDIUM = 0.5

# System prompt for the LLM matching call
LLM_MATCH_SYSTEM_PROMPT = """\
You are a constraint matching engine for a solar construction project management system called Goliath.

Your job: Given an email reply (subject, body, sender) and a list of candidate constraints from ConstraintsPro, determine which constraint the email is most likely responding to.

Context:
- These are large-scale solar farm construction projects across multiple sites
- Constraints are blockers/issues tracked in a system called ConstraintsPro
- Common constraint types: equipment delivery, permitting, engineering, materials, subcontractor issues
- The email is a reply to a follow-up email that was sent about one of these constraints

Instructions:
- Analyze the email content semantically — look for references to the same equipment, materials, permits, people, or issues described in the constraints
- Consider the sender — if the constraint has an owner, does the sender match?
- Consider specificity — a constraint about "tracker delivery" should match an email about "trackers shipping next week" even if the exact words differ
- If no constraint is a good match, return null for constraint_id with low confidence and explain why

Return a JSON object with exactly these fields:
{
  "constraint_id": "<id of best matching constraint, or null if no good match>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<1-2 sentence explanation of why this constraint matches or why no match was found>"
}

Output ONLY the JSON object, no other text."""


# ---------------------------------------------------------------------------
# EmailReplyMonitor — detects constraint-related replies and proposes changes
# ---------------------------------------------------------------------------

class EmailReplyMonitor:
    """Monitors email replies for resolution signals and proposes ConstraintsPro updates."""

    def __init__(self):
        self._contact_dir = None

    # ------------------------------------------------------------------
    # Contact directory access (lazy-loaded singleton)
    # ------------------------------------------------------------------

    def _get_contact_directory(self):
        """Get or create the contact directory instance."""
        if self._contact_dir is None:
            from bot.services.contact_directory import ContactDirectory
            self._contact_dir = ContactDirectory()
            self._contact_dir.load()
        return self._contact_dir

    # ------------------------------------------------------------------
    # Determine project from email context (subject + sender)
    # ------------------------------------------------------------------

    def _identify_project(self, subject: str, sender: str) -> Optional[str]:
        """Identify the project from email subject line and sender.

        Uses two strategies:
          1. Subject line parsing via match_project_key()
          2. Sender lookup in the contact directory

        Returns:
            Project key (e.g., 'tehuacana') or None.
        """
        from bot.config import match_project_key

        # Strategy 1: Check subject line for project name
        project_from_subject = match_project_key(subject)
        if project_from_subject:
            logger.info(f"Project identified from subject: {project_from_subject}")
            return project_from_subject

        # Strategy 2: Check sender against contact directory
        contact_dir = self._get_contact_directory()
        project_from_sender = contact_dir.lookup(sender)
        if project_from_sender and project_from_sender != "portfolio-wide":
            logger.info(
                f"Project identified from sender '{sender}': {project_from_sender}"
            )
            return project_from_sender

        logger.info(
            f"Could not identify project from subject '{subject}' "
            f"or sender '{sender}'"
        )
        return None

    # ------------------------------------------------------------------
    # Filter constraints to a specific project
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_constraints_by_project(
        constraints: list[dict], project_key: str
    ) -> list[dict]:
        """Filter constraints to only those belonging to the given project.

        Args:
            constraints: Full list of constraints across all projects
            project_key: Project key to filter by (e.g., 'tehuacana')

        Returns:
            Filtered list of constraints for the specified project.
        """
        filtered = []
        for c in constraints:
            c_project = (
                c.get("project_key", "")
                or c.get("project", "")
            ).lower().replace(" ", "-")

            if project_key.lower() in c_project or c_project in project_key.lower():
                filtered.append(c)

        logger.info(
            f"Filtered to {len(filtered)} constraints for project '{project_key}' "
            f"(from {len(constraints)} total)"
        )
        return filtered

    # ------------------------------------------------------------------
    # Analyze an email reply for resolution signals
    # ------------------------------------------------------------------

    def analyze_reply(self, subject: str, body: str, sender: str) -> list[dict]:
        """Analyze an email reply for constraint resolution signals.

        Args:
            subject: Email subject line (may contain constraint/project references)
            body: Email body text
            sender: Sender email address

        Returns:
            List of detected signals, each with:
                signal_type, proposed_action, confidence, matched_text, context
        """
        signals = []
        text = f"{subject} {body}".lower()

        for signal_name, signal_info in RESOLUTION_SIGNALS.items():
            for pattern in signal_info["patterns"]:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    # Calculate confidence based on pattern specificity
                    confidence = signal_info["confidence_threshold"]
                    matched_text = match.group(0)

                    # Boost confidence for longer, more specific matches
                    if len(matched_text) > 30:
                        confidence = min(confidence + 0.1, 1.0)

                    # Boost confidence if the subject references a known project
                    from bot.config import match_project_key
                    project_key = match_project_key(subject)
                    if project_key:
                        confidence = min(confidence + 0.1, 1.0)

                    signals.append({
                        "signal_type": signal_name,
                        "proposed_action": signal_info["proposed_action"],
                        "confidence": confidence,
                        "matched_text": matched_text,
                        "context": body[:300],
                        "sender": sender,
                        "subject": subject,
                        "project_key": project_key,
                    })
                    break  # Only count each signal type once

        return signals

    # ------------------------------------------------------------------
    # LLM-powered constraint matching (primary strategy)
    # ------------------------------------------------------------------

    async def _llm_match_constraint(
        self,
        subject: str,
        body: str,
        sender: str,
        candidates: list[dict],
    ) -> Optional[dict]:
        """Use Claude Haiku to semantically match an email to a constraint.

        Args:
            subject: Email subject line
            body: Email body text
            sender: Sender name/email
            candidates: Pre-filtered list of candidate constraints

        Returns:
            Dict with keys: constraint_id, confidence, reasoning
            Or None if the LLM call fails entirely.
        """
        if not candidates:
            return {"constraint_id": None, "confidence": 0.0, "reasoning": "No candidate constraints to match against."}

        # Build the candidate constraint descriptions for the prompt
        constraint_descriptions = []
        for i, c in enumerate(candidates, 1):
            cid = c.get("id", "unknown")
            desc = c.get("description", "No description")
            owner = c.get("owner", "Unassigned")
            priority = c.get("priority", "Unknown")
            notes = c.get("notes", "")
            need_by = c.get("need_by_date", "")
            project = c.get("project", c.get("project_key", "Unknown"))

            entry = (
                f"[{i}] ID: {cid}\n"
                f"    Project: {project}\n"
                f"    Description: {desc}\n"
                f"    Owner: {owner}\n"
                f"    Priority: {priority}\n"
                f"    Need-by date: {need_by or 'Not set'}\n"
                f"    Recent notes: {notes[:300] if notes else 'None'}"
            )
            constraint_descriptions.append(entry)

        constraints_text = "\n\n".join(constraint_descriptions)

        user_prompt = (
            f"EMAIL SUBJECT: {subject}\n\n"
            f"EMAIL SENDER: {sender}\n\n"
            f"EMAIL BODY:\n{body[:1500]}\n\n"
            f"---\n\n"
            f"CANDIDATE CONSTRAINTS ({len(candidates)} total):\n\n"
            f"{constraints_text}\n\n"
            f"---\n\n"
            f"Which constraint does this email most likely relate to? "
            f"Return the JSON response."
        )

        try:
            import anthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                logger.warning(
                    "ANTHROPIC_API_KEY not set — cannot use LLM matching, "
                    "falling back to word-overlap"
                )
                return None

            client = anthropic.AsyncAnthropic(api_key=api_key)

            logger.info(
                f"LLM constraint matching: sending {len(candidates)} candidates "
                f"for email from '{sender}' re: '{subject[:60]}'"
            )

            response = await asyncio.wait_for(
                client.messages.create(
                    model=LLM_MATCH_MODEL,
                    max_tokens=LLM_MATCH_MAX_TOKENS,
                    system=LLM_MATCH_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                ),
                timeout=30.0,  # 30 second timeout for this quick decision
            )

            # Extract the text response
            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text += block.text

            if not response_text.strip():
                logger.warning("LLM returned empty response for constraint matching")
                return None

            # Parse JSON from the response (handle possible markdown wrapping)
            json_text = response_text.strip()
            if json_text.startswith("```"):
                # Strip markdown code fences
                lines = json_text.split("\n")
                json_text = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                )

            result = json.loads(json_text)

            logger.info(
                f"LLM match result: constraint_id={result.get('constraint_id')}, "
                f"confidence={result.get('confidence')}, "
                f"reasoning={result.get('reasoning', '')[:100]}"
            )

            return {
                "constraint_id": result.get("constraint_id"),
                "confidence": float(result.get("confidence", 0.0)),
                "reasoning": result.get("reasoning", ""),
            }

        except ImportError:
            logger.warning(
                "anthropic package not installed — falling back to word-overlap matching. "
                "Install with: pip install anthropic"
            )
            return None

        except asyncio.TimeoutError:
            logger.warning(
                "LLM constraint matching timed out (30s) — falling back to word-overlap"
            )
            return None

        except json.JSONDecodeError as e:
            logger.warning(
                f"LLM returned invalid JSON for constraint matching: {e}. "
                f"Response was: {response_text[:200]}"
            )
            return None

        except Exception:
            logger.exception("LLM constraint matching failed — falling back to word-overlap")
            return None

    # ------------------------------------------------------------------
    # Legacy word-overlap matching (fallback only)
    # ------------------------------------------------------------------

    @staticmethod
    def _word_overlap_match(
        signal: dict, constraints: list[dict]
    ) -> Optional[tuple[dict, float]]:
        """Legacy word-overlap matching — used as fallback when LLM is unavailable.

        Returns:
            Tuple of (matched_constraint, normalized_confidence) or None.
        """
        subject = signal.get("subject", "").lower()
        context = signal.get("context", "").lower()
        sender = signal.get("sender", "").lower()
        project_key = signal.get("project_key")

        best_match = None
        best_score = 0

        for constraint in constraints:
            score = 0
            desc = constraint.get("description", "").lower()
            owner = constraint.get("owner", "").lower()
            c_project = constraint.get(
                "project_key", constraint.get("project", "")
            ).lower()

            # Check if project matches
            if project_key and project_key in c_project:
                score += 3

            # Check if description words appear in subject or context
            # Filter out common stop words to reduce noise
            stop_words = {
                "the", "a", "an", "is", "are", "was", "were", "be", "been",
                "being", "have", "has", "had", "do", "does", "did", "will",
                "would", "could", "should", "may", "might", "shall", "can",
                "to", "of", "in", "for", "on", "with", "at", "by", "from",
                "as", "into", "through", "during", "before", "after", "and",
                "but", "or", "not", "no", "so", "if", "then", "than", "too",
                "very", "just", "about", "up", "out", "all", "also", "it",
                "its", "this", "that", "these", "those", "we", "they", "them",
                "our", "your", "his", "her", "-", "–",
            }

            desc_words = set(desc.split()) - stop_words
            subject_words = set(subject.split()) - stop_words
            context_words = set(context.split()) - stop_words

            common_with_subject = desc_words & subject_words
            common_with_context = desc_words & context_words

            score += len(common_with_subject) * 2
            score += len(common_with_context)

            # Check if sender matches owner
            if owner and any(
                part in sender for part in owner.split() if len(part) > 2
            ):
                score += 2

            if score > best_score:
                best_score = score
                best_match = constraint

        # Only return if we have a reasonable match
        if best_score >= 3 and best_match is not None:
            # Normalize score to a rough 0-1 confidence
            # This is approximate — just enough for the confidence display
            normalized = min(best_score / 10.0, 0.95)
            return best_match, normalized

        return None

    # ------------------------------------------------------------------
    # Match a reply to a known constraint (main entry point)
    # ------------------------------------------------------------------

    async def match_to_constraint(
        self, signal: dict, constraints: list[dict]
    ) -> Optional[dict]:
        """Match a detected signal to a specific constraint using intelligent matching.

        Strategy:
          1. Identify project from subject + sender (primary filter)
          2. Filter constraints to that project
          3. Use LLM (Claude Haiku) for semantic matching
          4. Fall back to word-overlap if LLM unavailable

        The matched constraint dict gets extra keys injected:
          - _match_confidence: float 0-1
          - _match_reasoning: str explaining the match
          - _match_method: 'llm' or 'word_overlap'

        Returns:
            The matched constraint dict (with injected match metadata), or None.
        """
        subject = signal.get("subject", "")
        context = signal.get("context", "")
        sender = signal.get("sender", "")

        # Step 1: Identify project
        project_key = self._identify_project(subject, sender)

        # Override signal's project_key with our more comprehensive identification
        if project_key:
            signal["project_key"] = project_key

        # Step 2: Filter constraints to the identified project
        if project_key:
            candidates = self._filter_constraints_by_project(constraints, project_key)
            if not candidates:
                logger.warning(
                    f"Project '{project_key}' identified but no constraints found "
                    f"for that project — falling back to full constraint list"
                )
                candidates = constraints
        else:
            candidates = constraints
            logger.info(
                f"No project identified — matching against all "
                f"{len(candidates)} constraints"
            )

        # Step 3: Try LLM-powered matching
        llm_result = await self._llm_match_constraint(
            subject=subject,
            body=context,
            sender=sender,
            candidates=candidates,
        )

        if llm_result is not None:
            matched_id = llm_result.get("constraint_id")
            confidence = llm_result.get("confidence", 0.0)
            reasoning = llm_result.get("reasoning", "")

            if matched_id:
                # Find the constraint dict by ID
                for c in candidates:
                    if c.get("id") == matched_id:
                        # Inject match metadata
                        c["_match_confidence"] = confidence
                        c["_match_reasoning"] = reasoning
                        c["_match_method"] = "llm"
                        logger.info(
                            f"LLM matched constraint {matched_id[:8]} "
                            f"with confidence {confidence:.2f}: {reasoning[:80]}"
                        )
                        return c

                logger.warning(
                    f"LLM returned constraint_id '{matched_id}' "
                    f"but it was not found in candidates — discarding"
                )

            # LLM explicitly said no match
            logger.info(
                f"LLM found no confident match (confidence={confidence:.2f}): "
                f"{reasoning[:100]}"
            )
            # Still return None but attach info to signal for low-confidence handling
            signal["_llm_no_match_reasoning"] = reasoning
            signal["_llm_no_match_confidence"] = confidence
            return None

        # Step 4: Fallback to word-overlap matching
        logger.info("Using word-overlap fallback for constraint matching")
        fallback_result = self._word_overlap_match(signal, candidates)

        if fallback_result:
            matched_constraint, fallback_confidence = fallback_result
            matched_constraint["_match_confidence"] = fallback_confidence
            matched_constraint["_match_reasoning"] = "Matched via word-overlap fallback (LLM unavailable)"
            matched_constraint["_match_method"] = "word_overlap"
            logger.info(
                f"Word-overlap fallback matched constraint "
                f"{matched_constraint.get('id', '?')[:8]} "
                f"with confidence {fallback_confidence:.2f}"
            )
            return matched_constraint

        return None

    # ------------------------------------------------------------------
    # Generate a Telegram proposal message
    # ------------------------------------------------------------------

    async def send_update_proposal(
        self, bot, chat_id: int,
        constraint: dict, signal: dict, proposed_changes: dict
    ) -> None:
        """Send a human-readable proposal to Telegram for approval.

        The proposal describes what change the system wants to make to ConstraintsPro
        and includes inline buttons for Approve/Reject.

        Includes confidence level display and LLM reasoning when available.

        Args:
            bot: Telegram bot instance
            chat_id: Target chat ID
            constraint: The matched constraint dict (may contain _match_* metadata)
            signal: The detected signal info
            proposed_changes: Dict describing the proposed ConstraintsPro updates
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        project = escape(constraint.get("project", "Unknown"))
        desc = escape(constraint.get("description", "")[:200])
        owner = escape(constraint.get("owner", "Unassigned"))
        priority = escape(constraint.get("priority", ""))
        cid = constraint.get("id", "unknown")
        sender = escape(signal.get("sender", "Unknown"))
        matched = escape(signal.get("matched_text", ""))

        # Use match confidence from the matching step (LLM or fallback)
        match_confidence = constraint.get("_match_confidence", signal.get("confidence", 0))
        match_reasoning = constraint.get("_match_reasoning", "")
        match_method = constraint.get("_match_method", "unknown")

        # Build the human-readable proposal
        action = proposed_changes.get("action", "update")
        action_desc = ""

        if action == "resolve":
            action_desc = f"Resolve constraint #{cid[:8]} and mark as closed"
        elif action == "drop_priority":
            old_p = constraint.get("priority", "HIGH")
            new_p = proposed_changes.get("new_priority", "MEDIUM")
            action_desc = f"Drop priority from {old_p} to {new_p}"
        elif action == "update_notes":
            note = proposed_changes.get("note", "")
            action_desc = f'Add note: "{note[:100]}"'

        # Combine multiple actions
        all_actions = [action_desc]
        if proposed_changes.get("add_note") and action != "update_notes":
            all_actions.append(f'Add note: "{proposed_changes["add_note"][:80]}"')

        actions_text = "\n".join(f"  - {a}" for a in all_actions)

        # Confidence indicator
        if match_confidence >= CONFIDENCE_HIGH:
            confidence_display = f"{match_confidence:.0%}"
        elif match_confidence >= CONFIDENCE_MEDIUM:
            confidence_display = f"~{match_confidence:.0%} (medium — please verify)"
        else:
            confidence_display = f"~{match_confidence:.0%} (low)"

        # Build the message text
        text_parts = [
            f"<b>Constraint Update Proposal</b>\n",
            f"<b>Project:</b> {project}",
            f"<b>Constraint:</b> {desc}",
            f"<b>Owner:</b> {owner} | <b>Priority:</b> {priority}\n",
            f"<b>Email reply from:</b> {sender}",
            f"<b>Signal detected:</b> <i>\"{matched}\"</i>",
            f"<b>Match confidence:</b> {confidence_display}",
        ]

        # Add reasoning from LLM if available
        if match_reasoning and match_method == "llm":
            text_parts.append(
                f"<b>Match reasoning:</b> <i>{escape(match_reasoning[:200])}</i>"
            )

        # Add warning banner for medium confidence
        if CONFIDENCE_MEDIUM <= match_confidence < CONFIDENCE_HIGH:
            text_parts.insert(1, "-- Medium confidence match — please verify --\n")

        text_parts.extend([
            f"\n<b>Proposed changes:</b>\n{actions_text}\n",
            "Should I push these changes to ConstraintsPro?",
        ])

        text = "\n".join(text_parts)

        # Encode the proposed changes in callback data
        # Format: cp_approve:<constraint_id>:<action_code>:<sender_hash>
        # action codes: r=resolve, d=drop_priority, n=update_notes
        # sender_hash: first 8 chars for contact learning on approval
        action_code = action[0]  # r, d, or u
        sender_short = signal.get("sender", "")[:20].replace(":", "_")
        project_tag = signal.get("project_key", "")[:20]
        callback_approve = f"cp_approve:{cid}:{action_code}:{sender_short}|{project_tag}"
        callback_reject = f"cp_reject:{cid}:{action_code}"

        # Telegram callback_data has a 64-byte limit — truncate if needed
        if len(callback_approve.encode("utf-8")) > 64:
            callback_approve = f"cp_approve:{cid}:{action_code}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Yes, push it",
                    callback_data=callback_approve,
                ),
                InlineKeyboardButton(
                    "No, skip",
                    callback_data=callback_reject,
                ),
            ]
        ])

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:
                logger.exception("Failed to send update proposal to Telegram")

    # ------------------------------------------------------------------
    # Send a low-confidence notification (no auto-proposal)
    # ------------------------------------------------------------------

    async def _send_low_confidence_notice(
        self, bot, chat_id: int, signal: dict
    ) -> None:
        """Send a notification when a signal was detected but matching confidence is too low.

        Instead of proposing a possibly-wrong match, we inform the user and let them
        decide whether to investigate manually.
        """
        sender = escape(signal.get("sender", "Unknown"))
        subject = escape(signal.get("subject", ""))
        signal_type = signal.get("signal_type", "unknown")
        matched_text = escape(signal.get("matched_text", ""))
        context = escape(signal.get("context", "")[:200])
        reasoning = signal.get("_llm_no_match_reasoning", "")

        text = (
            f"<b>Email Reply — Low Confidence Match</b>\n\n"
            f"Got an email reply that might be constraint-related "
            f"but I couldn't confidently match it.\n\n"
            f"<b>From:</b> {sender}\n"
            f"<b>Subject:</b> {subject}\n"
            f"<b>Signal detected:</b> <i>\"{matched_text}\"</i> ({signal_type})\n"
            f"<b>Preview:</b> {context}\n"
        )

        if reasoning:
            text += f"\n<b>Analysis:</b> <i>{escape(reasoning[:200])}</i>\n"

        text += (
            "\nWant me to try matching it manually? "
            "Reply with the project name or constraint ID."
        )

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                logger.exception("Failed to send low-confidence notice to Telegram")

    # ------------------------------------------------------------------
    # Log reply to persistent file for follow-up awareness
    # ------------------------------------------------------------------

    @staticmethod
    def _log_reply_to_file(
        *, signal: dict, constraint: Optional[dict],
        sender: str, subject: str, body: str,
    ) -> None:
        """Log a detected reply to the persistent JSON reply log.

        Called for every signal that has at least some match — even low
        confidence. The follow-up generators (proactive_followup.py,
        morning_report.py) read this log to avoid generating blind
        follow-up drafts for constraints that already received replies.
        """
        try:
            from bot.services.reply_log import log_reply

            # Extract sender name (try to get the display name from before the @)
            sender_name = sender
            if "@" in sender:
                local_part = sender.split("@")[0]
                # Convert "patrick.root" to "Patrick Root"
                sender_name = " ".join(
                    p.capitalize() for p in local_part.replace(".", " ").replace("_", " ").split()
                )

            # Build a short reply summary from the email body
            reply_summary = ""
            if body:
                # Take the first substantive paragraph (skip greeting/signature lines)
                lines = [
                    ln.strip() for ln in body.split("\n")
                    if ln.strip()
                    and not ln.strip().lower().startswith(("hi ", "hello ", "hey ", "dear "))
                    and not ln.strip().lower().startswith(("thanks", "regards", "best", "sent from"))
                    and len(ln.strip()) > 10
                ]
                reply_summary = " ".join(lines[:3])[:500] if lines else body[:500]

            log_reply(
                sender=sender,
                sender_name=sender_name,
                project_key=signal.get("project_key", "") or (
                    constraint.get("project_key", "") if constraint else ""
                ),
                constraint_id=(constraint.get("id", "") if constraint else ""),
                constraint_desc=(constraint.get("description", "") if constraint else ""),
                signal_type=signal.get("signal_type", ""),
                reply_summary=reply_summary,
                confidence=constraint.get("_match_confidence", 0.0) if constraint else 0.0,
                subject=subject,
            )
        except Exception:
            logger.debug("Failed to log reply to file — non-critical", exc_info=True)

    # ------------------------------------------------------------------
    # Build proposed changes from a signal
    # ------------------------------------------------------------------

    def build_proposed_changes(
        self, signal: dict, constraint: dict
    ) -> dict:
        """Build a dict of proposed ConstraintsPro changes from a signal.

        Returns:
            Dict with keys: action, new_priority (if drop), note (if update), add_note
        """
        action = signal["proposed_action"]
        sender = signal.get("sender", "someone")
        context = signal.get("context", "")[:200]
        today = datetime.now(CT).strftime("%Y-%m-%d")

        changes = {"action": action}

        if action == "resolve":
            changes["add_note"] = (
                f"[{today}] Resolved via email reply from {sender}: {context[:100]}"
            )

        elif action == "drop_priority":
            current = constraint.get("priority", "HIGH")
            if current == "HIGH":
                changes["new_priority"] = "MEDIUM"
            elif current == "MEDIUM":
                changes["new_priority"] = "LOW"
            else:
                changes["new_priority"] = "LOW"
            changes["add_note"] = (
                f"[{today}] Priority dropped based on email reply from {sender}: {context[:100]}"
            )

        elif action == "update_notes":
            changes["note"] = (
                f"[{today}] Update from {sender}: {context[:200]}"
            )

        return changes

    # ------------------------------------------------------------------
    # Process a single email reply (called from email_poller integration)
    # ------------------------------------------------------------------

    async def process_reply(
        self, bot, chat_id: int,
        subject: str, body: str, sender: str,
        known_constraints: Optional[list[dict]] = None,
    ) -> int:
        """Process an email reply, detect signals, and propose updates.

        Args:
            bot: Telegram bot instance
            chat_id: Chat ID for Telegram notifications
            subject: Email subject line
            body: Email body text
            sender: Sender email address
            known_constraints: Optional pre-loaded constraint list

        Returns:
            Number of proposals sent.
        """
        # 1. Analyze for signals
        signals = self.analyze_reply(subject, body, sender)
        if not signals:
            return 0

        logger.info(
            f"Email reply monitor: {len(signals)} signal(s) detected "
            f"in reply from {sender}: {[s['signal_type'] for s in signals]}"
        )

        # 2. Load constraints if not provided
        if known_constraints is None:
            try:
                from bot.services.proactive_followup import ProactiveFollowUpEngine
                from bot.config import DATA_DIR

                engine = ProactiveFollowUpEngine(DATA_DIR / "proactive_followup.db")
                await engine.initialize()
                known_constraints = await engine.get_all_open_constraints()
                await engine.close()
            except Exception:
                logger.exception("Failed to load constraints for reply matching")
                return 0

        # 3. Match signals to constraints and propose changes
        proposals_sent = 0
        for signal in signals:
            try:
                matched_constraint = await self.match_to_constraint(
                    signal, known_constraints
                )

                if not matched_constraint:
                    # Check if this was a low-confidence LLM result vs total miss
                    llm_confidence = signal.get("_llm_no_match_confidence", 0.0)
                    if llm_confidence > 0 or signal.get("_llm_no_match_reasoning"):
                        # LLM tried but couldn't match — send low-confidence notice
                        logger.info(
                            f"Signal '{signal['signal_type']}' from {sender}: "
                            f"LLM could not match (confidence={llm_confidence:.2f}) "
                            f"— sending low-confidence notice"
                        )
                        # Log unmatched reply so follow-up generators know
                        # *something* came in from this sender/project
                        self._log_reply_to_file(
                            signal=signal, constraint=None,
                            sender=sender, subject=subject, body=body,
                        )
                        await self._send_low_confidence_notice(bot, chat_id, signal)
                        proposals_sent += 1  # Count notices too
                    else:
                        logger.debug(
                            f"Signal '{signal['signal_type']}' from {sender} "
                            f"could not be matched to a constraint — skipping"
                        )
                    continue

                # Check confidence thresholds
                match_confidence = matched_constraint.get("_match_confidence", 0.5)

                if match_confidence < CONFIDENCE_MEDIUM:
                    # Too low to propose — send notice instead
                    logger.info(
                        f"Match confidence {match_confidence:.2f} below threshold "
                        f"{CONFIDENCE_MEDIUM} — sending low-confidence notice"
                    )
                    signal["_llm_no_match_reasoning"] = matched_constraint.get(
                        "_match_reasoning", ""
                    )
                    # Log low-confidence match to reply log
                    self._log_reply_to_file(
                        signal=signal, constraint=matched_constraint,
                        sender=sender, subject=subject, body=body,
                    )
                    await self._send_low_confidence_notice(bot, chat_id, signal)
                    proposals_sent += 1
                    continue

                # Confidence is medium or high — propose the change
                proposed_changes = self.build_proposed_changes(
                    signal, matched_constraint
                )

                # Log matched reply to the persistent reply log so
                # follow-up generators (proactive_followup, morning_report)
                # know this constraint already received a reply
                self._log_reply_to_file(
                    signal=signal, constraint=matched_constraint,
                    sender=sender, subject=subject, body=body,
                )

                await self.send_update_proposal(
                    bot, chat_id, matched_constraint, signal, proposed_changes
                )
                proposals_sent += 1

                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)

            except Exception:
                logger.exception(
                    f"Error processing signal {signal.get('signal_type')}"
                )

        return proposals_sent


# ---------------------------------------------------------------------------
# Callback handler for constraint proposal approval (Telegram inline buttons)
# ---------------------------------------------------------------------------

async def handle_constraint_proposal_callback(update, context) -> None:
    """Handle Approve/Reject for constraint update proposals.

    Callback data format: cp_approve:<constraint_id>:<action_code>[:<sender|project>]
                      or: cp_reject:<constraint_id>:<action_code>

    Action codes: r=resolve, d=drop_priority, n=update_notes, u=update_notes

    On approval, also learns the sender->project association in the contact directory.
    """
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")
    if len(parts) < 3:
        return

    action_type = parts[0]  # cp_approve or cp_reject
    constraint_id = parts[1]
    action_code = parts[2]

    if action_type == "cp_reject":
        await query.edit_message_text(
            f"Skipped — no changes pushed to ConstraintsPro for constraint {constraint_id[:8]}.",
        )
        return

    if action_type == "cp_approve":
        # Learn the sender->project association from the callback data
        if len(parts) >= 4 and "|" in parts[3]:
            try:
                sender_info, project_info = parts[3].split("|", 1)
                if sender_info and project_info:
                    from bot.services.contact_directory import ContactDirectory
                    contact_dir = ContactDirectory()
                    contact_dir.load()
                    contact_dir.add_contact(
                        name=sender_info.strip(),
                        project=project_info.strip(),
                        source="learned",
                    )
                    logger.info(
                        f"Learned contact association: '{sender_info}' -> '{project_info}'"
                    )
            except Exception:
                logger.debug(
                    "Could not learn contact from callback data — non-critical",
                    exc_info=True,
                )

        # Push the change to ConstraintsPro via constraints_manager
        try:
            from bot.agents.definitions import CONSTRAINTS_MANAGER
            from bot.agents.runner import get_runner

            action_map = {
                "r": "resolve",
                "d": "drop_priority",
                "n": "update_notes",
                "u": "update_notes",
            }
            action_name = action_map.get(action_code, "update_notes")

            if action_name == "resolve":
                prompt = (
                    f"Resolve constraint with ID '{constraint_id}' in ConstraintsPro.\n"
                    f"Set its status to 'resolved' or 'closed'.\n"
                    f"Add a note: 'Resolved based on email follow-up reply — {datetime.now(CT).strftime('%Y-%m-%d')}'\n"
                    f"Use the appropriate MCP tool (constraint_update or similar) to make this change."
                )
            elif action_name == "drop_priority":
                prompt = (
                    f"Lower the priority of constraint '{constraint_id}' in ConstraintsPro.\n"
                    f"If it's currently HIGH, set to MEDIUM. If MEDIUM, set to LOW.\n"
                    f"Add a note: 'Priority lowered based on positive email follow-up — {datetime.now(CT).strftime('%Y-%m-%d')}'\n"
                    f"Use the appropriate MCP tool to make this change."
                )
            else:
                prompt = (
                    f"Add a note to constraint '{constraint_id}' in ConstraintsPro:\n"
                    f"'Update received via email follow-up — {datetime.now(CT).strftime('%Y-%m-%d')}'\n"
                    f"Use the appropriate MCP tool to make this change."
                )

            runner = get_runner()
            result = await runner.run(
                agent=CONSTRAINTS_MANAGER,
                task_prompt=prompt,
                timeout=120,
            )

            if result.success:
                await query.edit_message_text(
                    f"Done — changes pushed to ConstraintsPro for constraint {constraint_id[:8]}.\n"
                    f"Action: {action_name}",
                )

                # Log to memory
                try:
                    from bot.memory.store import MemoryStore
                    memory = MemoryStore(MEMORY_DB_PATH)
                    await memory.initialize()
                    await memory.save(
                        category="action_item",
                        summary=f"Constraint {constraint_id[:8]} updated via email follow-up: {action_name}",
                        detail=f"Constraint ID: {constraint_id}\nAction: {action_name}\nSource: email reply monitor",
                        source="email_reply_monitor",
                        tags=f"proactive_followup,constraint_update,{action_name}",
                    )
                    await memory.close()
                except Exception:
                    pass

            else:
                await query.edit_message_text(
                    f"Failed to push changes to ConstraintsPro.\n"
                    f"Error: {result.error[:200] if result.error else 'Unknown'}\n"
                    f"You may need to update manually.",
                )

        except Exception as e:
            logger.exception("Failed to push constraint update")
            await query.edit_message_text(
                f"Error pushing changes: {str(e)[:200]}\nPlease update ConstraintsPro manually.",
            )
