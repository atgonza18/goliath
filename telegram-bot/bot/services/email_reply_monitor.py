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
"""

import asyncio
import json
import logging
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
# EmailReplyMonitor — detects constraint-related replies and proposes changes
# ---------------------------------------------------------------------------

class EmailReplyMonitor:
    """Monitors email replies for resolution signals and proposes ConstraintsPro updates."""

    def __init__(self):
        pass

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
    # Match a reply to a known constraint
    # ------------------------------------------------------------------

    async def match_to_constraint(
        self, signal: dict, constraints: list[dict]
    ) -> Optional[dict]:
        """Try to match a detected signal to a specific constraint.

        Uses subject line, sender name, and context to find the best match.
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
            c_project = constraint.get("project_key", constraint.get("project", "")).lower()

            # Check if project matches
            if project_key and project_key in c_project:
                score += 3

            # Check if description words appear in subject or context
            desc_words = set(desc.split())
            subject_words = set(subject.split())
            context_words = set(context.split())

            common_with_subject = desc_words & subject_words
            common_with_context = desc_words & context_words

            score += len(common_with_subject) * 2
            score += len(common_with_context)

            # Check if sender matches owner
            if owner and any(part in sender for part in owner.split() if len(part) > 2):
                score += 2

            if score > best_score:
                best_score = score
                best_match = constraint

        # Only return if we have a reasonable match
        if best_score >= 3:
            return best_match
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

        Args:
            bot: Telegram bot instance
            chat_id: Target chat ID
            constraint: The matched constraint dict
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
        confidence = signal.get("confidence", 0)

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

        text = (
            f"<b>Constraint Update Proposal</b>\n\n"
            f"<b>Project:</b> {project}\n"
            f"<b>Constraint:</b> {desc}\n"
            f"<b>Owner:</b> {owner} | <b>Priority:</b> {priority}\n\n"
            f"<b>Email reply from:</b> {sender}\n"
            f"<b>Signal detected:</b> <i>\"{matched}\"</i>\n"
            f"<b>Confidence:</b> {confidence:.0%}\n\n"
            f"<b>Proposed changes:</b>\n{actions_text}\n\n"
            f"Should I push these changes to ConstraintsPro?"
        )

        # Encode the proposed changes in callback data
        # Format: cp_approve:<constraint_id>:<action_code>
        # action codes: r=resolve, d=drop_priority, n=update_notes
        action_code = action[0]  # r, d, or u
        callback_approve = f"cp_approve:{cid}:{action_code}"
        callback_reject = f"cp_reject:{cid}:{action_code}"

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
                matched_constraint = await self.match_to_constraint(signal, known_constraints)
                if not matched_constraint:
                    logger.debug(
                        f"Signal '{signal['signal_type']}' from {sender} "
                        f"could not be matched to a constraint — skipping"
                    )
                    continue

                proposed_changes = self.build_proposed_changes(signal, matched_constraint)
                await self.send_update_proposal(
                    bot, chat_id, matched_constraint, signal, proposed_changes
                )
                proposals_sent += 1

                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)

            except Exception:
                logger.exception(f"Error processing signal {signal.get('signal_type')}")

        return proposals_sent


# ---------------------------------------------------------------------------
# Callback handler for constraint proposal approval (Telegram inline buttons)
# ---------------------------------------------------------------------------

async def handle_constraint_proposal_callback(update, context) -> None:
    """Handle Approve/Reject for constraint update proposals.

    Callback data format: cp_approve:<constraint_id>:<action_code>
                      or: cp_reject:<constraint_id>:<action_code>

    Action codes: r=resolve, d=drop_priority, n=update_notes, u=update_notes
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
        # Push the change to ConstraintsPro via constraints_manager
        try:
            from bot.agents.definitions import CONSTRAINTS_MANAGER
            from bot.agents.runner import SubagentRunner

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

            runner = SubagentRunner()
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
