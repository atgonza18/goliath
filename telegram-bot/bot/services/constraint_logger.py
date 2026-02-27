"""
Constraint Auto-Logger — Email to ConstraintsPro bridge.

When a constraint-related email arrives (e.g., from Josh Hauger or with
"constraint" in the subject), this module:
  1. Parses the email body for individual constraints using Claude CLI
  2. Matches each constraint to a portfolio project
  3. Creates entries in ConstraintsPro via the constraints_manager agent's MCP tools
  4. Notifies the user in Telegram with a summary for review

This runs as a background task spawned from the email poller so it never
blocks the 45-second polling cycle.
"""

import asyncio
import json
import logging
import re
from html import escape
from typing import Optional

from bot.config import PROJECTS, REPORT_CHAT_ID, match_project_key

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
        from bot.agents.runner import SubagentRunner

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

        runner = SubagentRunner()
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
        from bot.agents.runner import SubagentRunner

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

        runner = SubagentRunner()
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
