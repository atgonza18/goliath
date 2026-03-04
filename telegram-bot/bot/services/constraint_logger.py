"""
Constraint Auto-Logger — Email to ConstraintsPro bridge.

When a constraint-related email arrives (e.g., from Josh Hauger or with
"constraint" in the subject), this module:
  1. Parses the email body for individual constraints using Claude CLI
  2. Matches each constraint to a portfolio project
  3. Creates entries in ConstraintsPro via the constraints_manager agent's MCP tools
  4. Notifies the user in Telegram with a summary for review

Hauger DSC Summary handling (Feature #5b):
  Josh Hauger's weekly DSC summary emails get special treatment — they contain
  constraint STATUS UPDATES (from ConstraintsPro, so creating new constraints
  would be circular) plus PRODUCTION DATA (not constraints at all). For these:
    A. Constraint content -> match to EXISTING constraints, append as notes
    B. Production content -> store as intel in MemoryStore + project files
    C. NEVER create new constraints from Hauger DSC summaries

This runs as a background task spawned from the email poller so it never
blocks the 45-second polling cycle.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from bot.config import PROJECTS, PROJECTS_DIR, REPORT_CHAT_ID, match_project_key

CT = ZoneInfo("America/Chicago")

logger = logging.getLogger(__name__)


class ConstraintLogger:
    """Parses constraint emails and auto-logs them to ConstraintsPro."""

    def __init__(self):
        pass

    # ==================================================================
    # Main entry point — orchestrates parse -> create -> notify
    # ==================================================================

    async def process_and_log(
        self,
        bot,
        chat_id: int,
        email_body: str,
        sender: str,
        subject: str,
        attachments_text: Optional[str] = None,
    ) -> int:
        """Full pipeline: parse email -> create in ConstraintsPro -> notify user.

        Args:
            bot: Telegram bot instance for sending notifications.
            chat_id: Telegram chat ID for notifications.
            email_body: The email body text to parse.
            sender: Email sender address.
            subject: Email subject line.
            attachments_text: Optional text extracted from attachments (for
                constraint emails that embed data in Excel/PDF attachments).

        Returns:
            Number of constraints successfully created.
        """
        logger.info(
            f"Constraint auto-logger starting — sender={sender}, "
            f"subject={subject!r}"
        )

        # Step 1: Parse the email for individual constraints
        try:
            constraints = await self.process_email_for_constraints(
                email_body=email_body,
                sender=sender,
                subject=subject,
                attachments_text=attachments_text,
            )
        except Exception:
            logger.exception("Constraint parsing failed")
            constraints = []

        if not constraints:
            logger.info(
                "No actionable constraints parsed from email — skipping creation"
            )
            return 0

        logger.info(f"Parsed {len(constraints)} constraint(s) from email")

        # Step 2: Create constraints in ConstraintsPro
        try:
            created = await self.create_constraints_in_pro(
                constraints_list=constraints,
                sender=sender,
                subject=subject,
            )
        except Exception:
            logger.exception("Constraint creation in ConstraintsPro failed")
            created = []

        if not created:
            logger.warning(
                "No constraints were created in ConstraintsPro — "
                "either all duplicates or creation failed"
            )
            return 0

        # Step 3: Notify user in Telegram
        try:
            await self.notify_user(
                bot=bot,
                chat_id=chat_id,
                constraints_created=created,
                source_email_info={
                    "sender": sender,
                    "subject": subject,
                    "count_parsed": len(constraints),
                    "count_created": len(created),
                },
            )
        except Exception:
            logger.exception("Constraint notification to Telegram failed")

        logger.info(
            f"Constraint auto-logger complete — {len(created)} constraint(s) "
            f"created from {sender}'s email"
        )
        return len(created)

    # ==================================================================
    # Step 1: Parse email for individual constraints
    # ==================================================================

    async def process_email_for_constraints(
        self,
        email_body: str,
        sender: str,
        subject: str,
        attachments_text: Optional[str] = None,
    ) -> list[dict]:
        """Use Claude CLI with constraints_manager to parse an email into
        individual constraint entries.

        Returns a list of dicts, each with:
            - description (str): What the constraint is
            - project_key (str): Matched project key (e.g., 'salt-branch')
            - project_name (str): Human-readable project name
            - priority (str): HIGH / MEDIUM / LOW
            - owner (str): Who owns this constraint
            - need_by_date (str or None): YYYY-MM-DD if mentioned
            - category (str): CONSTRUCTION / PROCUREMENT / ENGINEERING / PERMITTING / OTHER
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import get_runner

        # Build the project list for the agent to match against
        project_list = "\n".join(
            f"  - key={k}, name={v['name']}" for k, v in PROJECTS.items()
        )

        # Combine email body + attachment text if available
        full_text = email_body or ""
        if attachments_text:
            full_text += f"\n\n--- ATTACHMENT CONTENT ---\n{attachments_text}"

        # Truncate to avoid overwhelming the context window
        if len(full_text) > 15000:
            full_text = full_text[:15000] + "\n\n[... truncated ...]"

        prompt = f"""\
Parse the following email for individual construction constraints. Extract EACH \
distinct constraint mentioned in the email body.

EMAIL METADATA:
- From: {sender}
- Subject: {subject}

EMAIL CONTENT:
{full_text}

PORTFOLIO PROJECTS (match each constraint to one of these):
{project_list}

INSTRUCTIONS:
1. Identify each distinct constraint or blocker mentioned in the email.
2. For each constraint, extract:
   - description: Clear, concise description of the constraint
   - project_key: Match to one of the portfolio projects listed above (use the key). \
If the constraint mentions a project name, match it. If unclear, use the most likely \
project based on context. If truly no project can be determined, use "unknown".
   - project_name: The human-readable name of the matched project
   - priority: HIGH (blocks critical path / safety), MEDIUM (impacts schedule), or LOW (tracking)
   - owner: Who is responsible for resolving this (person or organization name from the email)
   - need_by_date: If a deadline/date is mentioned, use YYYY-MM-DD format. Otherwise null.
   - category: One of CONSTRUCTION, PROCUREMENT, ENGINEERING, PERMITTING, or OTHER

3. Do NOT create duplicate constraints — if the same issue is mentioned multiple \
times in different wording, consolidate into one.
4. If the email is just a status update with no new actionable constraints, return an empty array.
5. Only extract REAL constraints — not general commentary or pleasantries.

Return ONLY a JSON array of constraint objects. No other text.
Wrap the JSON in ```json ... ``` code fences.

Example output:
```json
[
  {{
    "description": "Waiting on geotech report for pile design — needed before driving can start",
    "project_key": "salt-branch",
    "project_name": "Salt Branch",
    "priority": "HIGH",
    "owner": "Terracon",
    "need_by_date": "2026-03-15",
    "category": "ENGINEERING"
  }}
]
```
"""

        runner = get_runner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=180,
            no_tools=True,  # Parsing only — no MCP calls needed
        )

        if not result.success or not result.output:
            logger.error(f"Constraint parsing agent failed: {result.error}")
            return []

        # Parse JSON from the output
        parsed = self._parse_json_array(result.output)

        # Validate and clean up each constraint
        valid = []
        for c in parsed:
            desc = (c.get("description") or "").strip()
            if not desc:
                continue

            # Ensure project_key is valid
            pk = c.get("project_key", "unknown")
            if pk not in PROJECTS and pk != "unknown":
                # Try to match from the project name or description
                pk = match_project_key(c.get("project_name", "")) or \
                     match_project_key(desc) or "unknown"

            pname = PROJECTS[pk]["name"] if pk in PROJECTS else "Unknown Project"

            valid.append({
                "description": desc,
                "project_key": pk,
                "project_name": pname,
                "priority": (c.get("priority") or "MEDIUM").upper(),
                "owner": (c.get("owner") or "Unassigned").strip(),
                "need_by_date": c.get("need_by_date") or None,
                "category": (c.get("category") or "OTHER").upper(),
            })

        return valid

    # ==================================================================
    # Step 2: Create constraints in ConstraintsPro via MCP tools
    # ==================================================================

    async def create_constraints_in_pro(
        self,
        constraints_list: list[dict],
        sender: str,
        subject: str,
    ) -> list[dict]:
        """Create parsed constraints in ConstraintsPro using the
        constraints_manager agent with MCP write tools.

        For each constraint, the agent will:
        1. Check if a similar constraint already exists (dedup)
        2. Create the constraint if new
        3. Add a source note referencing the email

        Returns list of constraints that were successfully created (each dict
        gets an added 'created' key with True/False and optional 'constraint_id').
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import get_runner

        if not constraints_list:
            return []

        # Build the creation instruction for the agent
        constraints_json = json.dumps(constraints_list, indent=2)

        prompt = f"""\
You need to create constraints in ConstraintsPro from parsed email data. \
Follow these steps carefully:

STEP 1: Call `projects_list` to get all project IDs and names.

STEP 2: For each constraint below, find the matching project by name/key \
and check if a very similar constraint already exists by calling \
`constraints_list_by_project` for that project. Compare descriptions — if \
a constraint with essentially the same issue already exists (even if worded \
slightly differently), SKIP creating it and note it as a duplicate.

STEP 3: For each NEW (non-duplicate) constraint, create it using \
`constraints_create` with:
  - The matched project ID from step 1
  - title: A concise title (first ~80 chars of description)
  - description: Full description
  - priority: As specified (HIGH/MEDIUM/LOW)
  - category: As specified
  - needByDate: As specified (or omit if null)
  - owner: As specified

STEP 4: After creating each constraint, add a note using `constraints_add_note` \
with this text:
  "Auto-logged from email — From: {sender} | Subject: {subject}"

CONSTRAINTS TO CREATE:
{constraints_json}

IMPORTANT:
- Use the MCP tools (constraints_create, constraints_add_note, etc.) to actually \
create these in ConstraintsPro. This is a WRITE operation — you are explicitly \
instructed to create these constraints.
- Skip duplicates — do NOT create a constraint if a very similar one already exists.
- If a project_key is "unknown", try to match from context or skip that constraint.

After processing all constraints, output a JSON summary wrapped in ```json ... ``` \
code fences:
```json
[
  {{
    "description": "...",
    "project_name": "...",
    "priority": "...",
    "status": "created" | "duplicate" | "failed",
    "constraint_id": "..." or null,
    "note": "optional explanation"
  }}
]
```
"""

        runner = get_runner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=300,  # MCP calls may be slow
        )

        if not result.success or not result.output:
            logger.error(f"Constraint creation agent failed: {result.error}")
            return []

        # Parse the summary JSON
        summary = self._parse_json_array(result.output)

        # Filter to only actually created constraints
        created = []
        for item in summary:
            status = (item.get("status") or "").lower()
            if status == "created":
                created.append(item)
            elif status == "duplicate":
                logger.info(
                    f"Constraint skipped (duplicate): {item.get('description', '')[:80]}"
                )
            elif status == "failed":
                logger.warning(
                    f"Constraint creation failed: {item.get('description', '')[:80]} — "
                    f"{item.get('note', 'unknown error')}"
                )

        # If we couldn't parse the summary but the agent succeeded,
        # fall back to counting from the original list
        if not summary and result.success:
            logger.warning(
                "Could not parse creation summary — assuming constraints were created"
            )
            for c in constraints_list:
                created.append({
                    "description": c["description"],
                    "project_name": c["project_name"],
                    "priority": c["priority"],
                    "status": "created",
                    "constraint_id": None,
                    "note": "Created (summary parsing failed)",
                })

        return created

    # ==================================================================
    # Step 3: Notify user in Telegram
    # ==================================================================

    async def notify_user(
        self,
        bot,
        chat_id: int,
        constraints_created: list[dict],
        source_email_info: dict,
    ) -> None:
        """Send an HTML-formatted Telegram notification summarizing what was
        created in ConstraintsPro.

        Shows:
        - How many constraints were created
        - Which projects they belong to
        - Priority breakdown
        - Source email info
        - Prompt to review in ConstraintsPro
        """
        if not bot or not chat_id:
            logger.debug("No bot/chat_id — skipping constraint notification")
            return

        sender = escape(source_email_info.get("sender", "unknown"))
        subject = escape(source_email_info.get("subject", ""))
        count_parsed = source_email_info.get("count_parsed", 0)
        count_created = len(constraints_created)

        # Group by project
        by_project: dict[str, list[dict]] = {}
        for c in constraints_created:
            pname = c.get("project_name", "Unknown")
            by_project.setdefault(pname, []).append(c)

        # Build constraint list
        constraint_lines = []
        for project, items in sorted(by_project.items()):
            constraint_lines.append(f"\n<b>{escape(project)}</b>:")
            for item in items:
                prio = escape(item.get("priority", ""))
                desc = escape((item.get("description") or "")[:120])
                prio_icon = {"HIGH": "!!!", "MEDIUM": "!!", "LOW": "!"}.get(
                    prio, "!"
                )
                constraint_lines.append(f"  [{prio_icon} {prio}] {desc}")

        constraint_detail = "\n".join(constraint_lines)

        # Compose message
        msg = (
            f"<b>Constraint Auto-Logger</b>\n\n"
            f"Created <b>{count_created}</b> new constraint(s) "
            f"from {sender}'s email.\n"
            f"Subject: <i>{subject}</i>\n"
            f"Parsed: {count_parsed} | Created: {count_created}\n"
            f"{constraint_detail}\n\n"
            f"Review in ConstraintsPro to verify and assign owners."
        )

        # Telegram message limit is 4096 chars
        if len(msg) > 4000:
            msg = msg[:3990] + "\n\n[... truncated]"

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            # Fallback without HTML if formatting causes issues
            try:
                fallback = (
                    f"Constraint Auto-Logger: Created {count_created} new "
                    f"constraint(s) from {source_email_info.get('sender', 'unknown')}'s "
                    f"email ({subject}). Review in ConstraintsPro."
                )
                await bot.send_message(chat_id=chat_id, text=fallback)
            except Exception:
                logger.exception("Failed to send constraint notification to Telegram")

    # ==================================================================
    # JSON parsing helper
    # ==================================================================

    @staticmethod
    def _parse_json_array(raw: str) -> list[dict]:
        """Extract a JSON array from Claude output (may be in code fences)."""
        # Try to find JSON inside code fences first
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else raw.strip()

        # Find array boundaries
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            logger.warning("No JSON array found in constraint agent output")
            return []

        try:
            data = json.loads(text[start : end + 1])
            if not isinstance(data, list):
                return []
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse constraint JSON: {e}")
            return []

    # ==================================================================
    # Hauger DSC Summary Processing (Feature #5b)
    # ==================================================================
    #
    # Josh Hauger's weekly DSC summary emails contain TWO types of content:
    #   1. CONSTRAINT STATUS UPDATES — these originate from ConstraintsPro,
    #      so creating new constraints would be circular/duplicate.
    #   2. PRODUCTION DATA — MW installed, piles driven, racking progress,
    #      manpower counts. These are NOT constraints.
    #
    # The pipeline:
    #   Pass 1: Claude splits email content into CONSTRAINT vs PRODUCTION items
    #   Pass 2A: Constraint items -> match to existing ConstraintsPro entries,
    #            append as notes. Unmatched items flagged for human review.
    #   Pass 2B: Production items -> stored in MemoryStore + project files.
    #   NEVER calls constraints_create for Hauger DSC emails.
    # ==================================================================

    async def process_hauger_email(
        self,
        bot,
        chat_id: int,
        email_body: str,
        sender: str,
        subject: str,
    ) -> dict:
        """Process a Hauger DSC summary email — notes + intel, never new constraints.

        Args:
            bot: Telegram bot instance for sending notifications.
            chat_id: Telegram chat ID for notifications.
            email_body: The email body text to parse.
            sender: Email sender address.
            subject: Email subject line.

        Returns:
            Dict with keys: notes_added, flagged_for_review, production_stored
        """
        result = {"notes_added": 0, "flagged_for_review": 0, "production_stored": 0}

        logger.info(
            f"Hauger DSC processor starting — sender={sender}, subject={subject!r}"
        )

        # ── Pass 1: Split content into CONSTRAINT items vs PRODUCTION items ──
        try:
            parsed = await self._parse_hauger_content(
                email_body=email_body,
                sender=sender,
                subject=subject,
            )
        except Exception:
            logger.exception("Hauger content parsing failed")
            parsed = {"constraints": [], "production": []}

        constraint_items = parsed.get("constraints", [])
        production_items = parsed.get("production", [])

        logger.info(
            f"Hauger parse results: {len(constraint_items)} constraint item(s), "
            f"{len(production_items)} production item(s)"
        )

        # ── Pass 2A: Match constraint items to existing constraints, add notes ──
        notes_added = 0
        flagged = []
        if constraint_items:
            try:
                notes_result = await self._match_and_note_constraints(
                    constraint_items=constraint_items,
                    sender=sender,
                    subject=subject,
                )
                notes_added = notes_result.get("notes_added", 0)
                flagged = notes_result.get("flagged", [])
            except Exception:
                logger.exception("Hauger constraint matching failed")

        result["notes_added"] = notes_added
        result["flagged_for_review"] = len(flagged)

        # ── Pass 2B: Store production data as intel ──
        production_stored = 0
        if production_items:
            try:
                production_stored = await self._store_production_intel(
                    production_items=production_items,
                    sender=sender,
                    subject=subject,
                )
            except Exception:
                logger.exception("Hauger production intel storage failed")

        result["production_stored"] = production_stored

        # ── Notify user in Telegram ──
        try:
            await self._notify_hauger_results(
                bot=bot,
                chat_id=chat_id,
                notes_added=notes_added,
                flagged=flagged,
                production_stored=production_stored,
                sender=sender,
                subject=subject,
            )
        except Exception:
            logger.exception("Hauger notification to Telegram failed")

        logger.info(
            f"Hauger DSC processor complete — {notes_added} notes, "
            f"{len(flagged)} flagged, {production_stored} production items stored"
        )

        return result

    # ------------------------------------------------------------------
    # Pass 1: Parse Hauger email into constraint vs production items
    # ------------------------------------------------------------------

    async def _parse_hauger_content(
        self,
        email_body: str,
        sender: str,
        subject: str,
    ) -> dict:
        """Use Claude to split Hauger email into constraint items and production items.

        Returns:
            {
                "constraints": [
                    {"description": "...", "project_key": "...", "project_name": "...", "status_note": "..."},
                    ...
                ],
                "production": [
                    {"project_key": "...", "project_name": "...", "metric": "...", "value": "...", "detail": "..."},
                    ...
                ]
            }
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import get_runner

        project_list = "\n".join(
            f"  - key={k}, name={v['name']}" for k, v in PROJECTS.items()
        )

        full_text = email_body or ""
        if len(full_text) > 15000:
            full_text = full_text[:15000] + "\n\n[... truncated ...]"

        prompt = f"""\
Parse the following DSC weekly summary email from Josh Hauger. This email contains \
TWO types of content that must be separated:

1. CONSTRAINT STATUS UPDATES — items about blockers, delays, issues, risks, \
pending approvals, RFI responses, etc. These are constraint-related updates.

2. PRODUCTION DATA — metrics like MW installed, piles driven/installed, racking \
progress percentage, manpower counts, acres graded, modules installed, wire pulled, \
string completion, trenching, etc. These are production/progress metrics.

EMAIL METADATA:
- From: {sender}
- Subject: {subject}

EMAIL CONTENT:
{full_text}

PORTFOLIO PROJECTS (match each item to one):
{project_list}

INSTRUCTIONS:
For CONSTRAINT items, extract:
  - description: The constraint/issue being reported on
  - project_key: Matched portfolio project key from the list above
  - project_name: Human-readable project name
  - status_note: The STATUS UPDATE text — what is the latest status/progress on \
this constraint? This is what will be appended as a note to the existing constraint.

For PRODUCTION items, extract:
  - project_key: Matched portfolio project key
  - project_name: Human-readable project name
  - metric: What is being measured (e.g., "piles_driven", "mw_installed", \
"racking_progress", "manpower", "modules_installed", "acres_graded", etc.)
  - value: The numeric value or percentage
  - detail: Full context sentence from the email

IMPORTANT CLASSIFICATION RULES:
- If an item talks about a BLOCKER, DELAY, RISK, HOLD, WAITING ON, PENDING, \
RFI, CONSTRAINT, ESCALATION, or ISSUE — it is a CONSTRAINT item.
- If an item reports a METRIC, COUNT, PERCENTAGE, RATE, or QUANTITY of work \
completed/in-progress — it is a PRODUCTION item.
- Some items may have BOTH (e.g., "250 piles installed this week but ground \
conditions are impacting production"). Split these: the metric goes to production, \
the issue goes to constraints.
- If truly ambiguous, classify as CONSTRAINT (safer — will get human review if \
no match found).

Return ONLY a JSON object with two arrays. No other text.
Wrap in ```json ... ``` code fences.

Example:
```json
{{
  "constraints": [
    {{
      "description": "Pile installation on hold due to PPP delays",
      "project_key": "blackford",
      "project_name": "Blackford",
      "status_note": "Delayed 7 days awaiting EOR response; team has escalated. ETA 3/2."
    }}
  ],
  "production": [
    {{
      "project_key": "salt-branch",
      "project_name": "Salt Branch",
      "metric": "piles_driven",
      "value": "1,200 this week",
      "detail": "Salt Branch drove 1,200 piles this week with 15 drill rigs."
    }}
  ]
}}
```
"""

        runner = get_runner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=180,
            no_tools=True,
        )

        if not result.success or not result.output:
            logger.error(f"Hauger content parsing agent failed: {result.error}")
            return {"constraints": [], "production": []}

        # Parse the JSON object from output
        parsed = self._parse_json_object(result.output)

        # Validate project keys
        for item in parsed.get("constraints", []):
            pk = item.get("project_key", "unknown")
            if pk not in PROJECTS and pk != "unknown":
                pk = match_project_key(item.get("project_name", "")) or \
                     match_project_key(item.get("description", "")) or "unknown"
                item["project_key"] = pk
            item["project_name"] = PROJECTS[pk]["name"] if pk in PROJECTS else "Unknown Project"

        for item in parsed.get("production", []):
            pk = item.get("project_key", "unknown")
            if pk not in PROJECTS and pk != "unknown":
                pk = match_project_key(item.get("project_name", "")) or \
                     match_project_key(item.get("detail", "")) or "unknown"
                item["project_key"] = pk
            item["project_name"] = PROJECTS[pk]["name"] if pk in PROJECTS else "Unknown Project"

        return parsed

    @staticmethod
    def _parse_json_object(raw: str) -> dict:
        """Extract a JSON object from Claude output (may be in code fences)."""
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else raw.strip()

        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            logger.warning("No JSON object found in Hauger parse output")
            return {"constraints": [], "production": []}

        try:
            data = json.loads(text[start : end + 1])
            if not isinstance(data, dict):
                return {"constraints": [], "production": []}
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Hauger JSON: {e}")
            return {"constraints": [], "production": []}

    # ------------------------------------------------------------------
    # Pass 2A: Match constraint items to existing, append notes
    # ------------------------------------------------------------------

    async def _match_and_note_constraints(
        self,
        constraint_items: list[dict],
        sender: str,
        subject: str,
    ) -> dict:
        """Match parsed constraint items to existing ConstraintsPro entries
        and append status notes. Items with no match are flagged for review.

        NEVER creates new constraints — only adds notes to existing ones.

        Returns:
            {"notes_added": int, "flagged": [{"description": ..., "project_name": ...}, ...]}
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import get_runner

        if not constraint_items:
            return {"notes_added": 0, "flagged": []}

        constraints_json = json.dumps(constraint_items, indent=2)

        prompt = f"""\
You need to match constraint status updates from a DSC summary email to EXISTING \
constraints in ConstraintsPro and append notes. You must NEVER create new constraints.

STEP 1: Call `projects_list` to get all project IDs and names.

STEP 2: For each constraint item below, find the matching project by name/key \
and call `constraints_list_by_project` to get all existing constraints for that project.

STEP 3: For each constraint item, find the BEST matching existing constraint by \
comparing descriptions. Look for constraints about the same issue/topic — they \
may be worded differently but are about the same underlying problem. Consider:
  - Same equipment/materials mentioned (e.g., "PD-10", "Shoals", "T-Line")
  - Same type of issue (e.g., both about pile installation delays)
  - Same parties involved (e.g., same owner/responsible party)

STEP 4: For each MATCHED constraint, call `constraints_add_note` with the \
status_note text from the item below, prefixed with:
  "DSC Update ({subject}): "

STEP 5: For items with NO good match (similarity too low, or project not found), \
mark them as "unmatched" — do NOT create new constraints.

CONSTRAINT STATUS UPDATES TO PROCESS:
{constraints_json}

Source email: From: {sender} | Subject: {subject}

CRITICAL RULES:
- You must ONLY use `constraints_add_note` — NEVER use `constraints_create`.
- A match should be based on the same underlying issue, not just keyword overlap.
- If in doubt, mark as "unmatched" — it is better to flag for human review than \
to add a note to the wrong constraint.
- For unmatched items, include the full description and project name in the output.

After processing, output a JSON summary wrapped in ```json ... ``` code fences:
```json
{{
  "matched": [
    {{
      "description": "...",
      "project_name": "...",
      "matched_constraint_id": "...",
      "matched_description": "...",
      "note_added": true
    }}
  ],
  "unmatched": [
    {{
      "description": "...",
      "project_name": "...",
      "reason": "No similar existing constraint found"
    }}
  ]
}}
```
"""

        runner = get_runner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=300,
        )

        if not result.success or not result.output:
            logger.error(f"Hauger constraint matching agent failed: {result.error}")
            return {"notes_added": 0, "flagged": constraint_items}

        parsed = self._parse_json_object(result.output)

        matched = parsed.get("matched", [])
        unmatched = parsed.get("unmatched", [])

        notes_added = sum(1 for m in matched if m.get("note_added"))
        flagged = [
            {
                "description": u.get("description", ""),
                "project_name": u.get("project_name", "Unknown"),
                "reason": u.get("reason", "No match found"),
            }
            for u in unmatched
        ]

        for m in matched:
            if m.get("note_added"):
                logger.info(
                    f"Hauger note added to constraint {m.get('matched_constraint_id', '?')}: "
                    f"{m.get('description', '')[:80]}"
                )

        for f_item in flagged:
            logger.info(
                f"Hauger constraint flagged for review: "
                f"{f_item.get('project_name', '?')} — {f_item.get('description', '')[:80]}"
            )

        return {"notes_added": notes_added, "flagged": flagged}

    # ------------------------------------------------------------------
    # Pass 2B: Store production data as intel
    # ------------------------------------------------------------------

    async def _store_production_intel(
        self,
        production_items: list[dict],
        sender: str,
        subject: str,
    ) -> int:
        """Store production data items in MemoryStore and project folder files.

        Each item is stored as:
          - MemoryStore entry: category="production_intel", with project_key and tags
          - Text file in projects/{key}/constraints/{date}_dsc_production.txt

        NEVER pushes production data to ConstraintsPro.

        Returns: number of items stored.
        """
        from bot.config import MEMORY_DB_PATH
        from bot.memory.store import MemoryStore

        if not production_items:
            return 0

        # Initialize memory store
        memory = MemoryStore(MEMORY_DB_PATH)
        await memory.initialize()

        today = datetime.now(CT).strftime("%Y-%m-%d")
        stored = 0

        # Group production items by project for file output
        by_project: dict[str, list[dict]] = {}

        try:
            for item in production_items:
                pk = item.get("project_key", "unknown")
                pname = item.get("project_name", "Unknown Project")
                metric = item.get("metric", "unknown")
                value = item.get("value", "")
                detail = item.get("detail", "")

                # Store in MemoryStore
                summary = f"{pname}: {metric} = {value}"
                tags = ",".join(filter(None, [
                    "production_intel",
                    f"project:{pk}" if pk != "unknown" else None,
                    f"metric:{metric}" if metric else None,
                    f"source:hauger_dsc",
                ]))

                try:
                    await memory.save(
                        category="production_intel",
                        summary=summary,
                        detail=f"{detail}\n\nSource: {sender} | {subject} | {today}",
                        project_key=pk if pk != "unknown" else None,
                        source="hauger_dsc",
                        tags=tags,
                    )
                    stored += 1
                except Exception:
                    logger.exception(f"Failed to save production intel to memory: {summary}")

                # Group for project file output
                if pk != "unknown":
                    by_project.setdefault(pk, []).append(item)

            # Write production data to project folder files
            for pk, items in by_project.items():
                try:
                    proj_dir = PROJECTS_DIR / pk / "constraints"
                    proj_dir.mkdir(parents=True, exist_ok=True)

                    filepath = proj_dir / f"{today}_dsc_production.txt"

                    lines = [
                        f"DSC Production Report — {PROJECTS[pk]['name']}",
                        f"Date: {today}",
                        f"Source: {sender} ({subject})",
                        f"{'=' * 60}",
                        "",
                    ]
                    for item in items:
                        metric = item.get("metric", "unknown")
                        value = item.get("value", "")
                        detail = item.get("detail", "")
                        lines.append(f"  {metric}: {value}")
                        if detail:
                            lines.append(f"    {detail}")
                        lines.append("")

                    content = "\n".join(lines)

                    # Append if file exists (multiple projects may write to same date)
                    if filepath.exists():
                        existing = filepath.read_text()
                        content = existing + "\n" + content
                    filepath.write_text(content)

                    logger.info(f"Production intel saved to {filepath}")
                except Exception:
                    logger.exception(
                        f"Failed to save production intel file for {pk}"
                    )

        finally:
            await memory.close()

        return stored

    # ------------------------------------------------------------------
    # Hauger notification
    # ------------------------------------------------------------------

    async def _notify_hauger_results(
        self,
        bot,
        chat_id: int,
        notes_added: int,
        flagged: list[dict],
        production_stored: int,
        sender: str,
        subject: str,
    ) -> None:
        """Send Telegram notification with Hauger DSC processing results.

        Format: "X existing constraints updated with notes, Y items flagged
        for review, Z production data points stored as intel"
        """
        if not bot or not chat_id:
            return

        sender_esc = escape(sender)
        subject_esc = escape(subject)

        # Build flagged items list
        flagged_lines = ""
        if flagged:
            flag_parts = []
            for f_item in flagged[:10]:  # Cap display at 10
                desc = escape((f_item.get("description") or "")[:120])
                proj = escape(f_item.get("project_name", "Unknown"))
                reason = escape(f_item.get("reason", ""))
                flag_parts.append(f"  - [{proj}] {desc}")
                if reason:
                    flag_parts.append(f"    Reason: {reason}")
            flagged_lines = "\n".join(flag_parts)

        msg = (
            f"<b>Hauger DSC Summary Processed</b>\n\n"
            f"From: {sender_esc}\n"
            f"Subject: <i>{subject_esc}</i>\n\n"
            f"<b>{notes_added}</b> existing constraint(s) updated with notes\n"
            f"<b>{len(flagged)}</b> item(s) flagged for review\n"
            f"<b>{production_stored}</b> production data point(s) stored as intel\n"
        )

        if flagged_lines:
            msg += f"\n<b>Flagged for review:</b>\n{flagged_lines}\n"

        msg += "\nConstraint notes added to ConstraintsPro. Production data saved to memory + project files."

        # Telegram limit
        if len(msg) > 4000:
            msg = msg[:3990] + "\n\n[... truncated]"

        try:
            await bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            try:
                fallback = (
                    f"Hauger DSC Summary: {notes_added} constraints updated with notes, "
                    f"{len(flagged)} flagged for review, {production_stored} production "
                    f"data points stored. Source: {sender} ({subject})"
                )
                await bot.send_message(chat_id=chat_id, text=fallback)
            except Exception:
                logger.exception("Failed to send Hauger notification to Telegram")
