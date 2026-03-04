"""
Proactive Follow-Up Engine — Solution-oriented constraint follow-up with daily PDF report.

Philosophy: We are HELPING people resolve constraints, not escalating AT them.
This replaces the old "Escalation Engine" with a collaborative, solution-oriented approach.

This is now the SINGLE source of truth for all follow-ups (as of 2026-03-01).
Both constraint-based AND commitment-based follow-ups are consolidated into ONE PDF.

Daily workflow:
  1. Pull ALL open constraints from ConstraintsPro (full universe)
  2. Pull pending commitment follow-ups from the followup queue DB (action items from meetings)
  3. Categorize each constraint by type: CONSTRUCTION, PROCUREMENT, ENGINEERING, PERMITTING, SCHEDULE
  4. Route to specialist "brain" for solution-oriented draft generation
  5. Generate ONE consolidated PDF report organized by project and priority
     (includes a "Commitment Follow-Ups" section at the end for meeting action items)
  6. Send the PDF to Telegram — user copies drafts and pastes into emails
  7. NO auto-send. NO approve/reject buttons. Just clean, copy-paste-ready drafts.

Schedule:
  - 5 PM CT Mon-Fri (end of day — what to chase tomorrow morning)
  - 4:15 PM CT Sunday (prep for Monday)

Tiered follow-up (helpfulness tiers, NOT threat tiers):
  Tier 1: "Hey, this is coming up — here's a suggestion to get ahead of it"
  Tier 2: "Still open — have you considered X or Y? Happy to help coordinate"
  Tier 3: "Looping in [leader] so we can get more resources on this"

The tone is always collaborative, never punitive.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import aiosqlite

from bot.config import REPO_ROOT, MEMORY_DB_PATH, PROJECTS, FOLLOWUP_DB_PATH, REPLY_LOG_AWARENESS_HOURS

logger = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")

# ---------------------------------------------------------------------------
# Constraint categories and their specialist agents
# ---------------------------------------------------------------------------

CONSTRAINT_CATEGORIES = {
    "CONSTRUCTION": {
        "agent": "construction_manager",
        "keywords": [
            "pile", "piling", "tracker", "racking", "module", "panel",
            "grading", "civil", "fencing", "erosion", "swppp", "substation",
            "trenching", "cable", "wire", "conduit", "install", "crew",
            "labor", "mobilization", "demobilization", "site prep",
            "foundation", "concrete", "backfill", "compaction",
            "commissioning", "energization", "testing", "punch list",
            "construction", "field", "site", "build", "work",
        ],
    },
    "PROCUREMENT": {
        "agent": "cost_analyst",
        "keywords": [
            "procurement", "purchase", "order", "delivery", "shipment",
            "vendor", "supplier", "material", "equipment", "lead time",
            "invoice", "payment", "cost", "budget", "price", "tariff",
            "po ", "purchase order", "rfp", "rfq", "bid", "quote",
            "supply chain", "warehouse", "inventory", "shortage",
            "backorder", "expedite", "freight", "logistics",
        ],
    },
    "ENGINEERING": {
        "agent": "construction_manager",
        "keywords": [
            "engineering", "design", "drawing", "ifc", "ifd",
            "redline", "as-built", "spec", "specification",
            "calculation", "study", "review", "stamp", "seal",
            "electrical", "structural", "geotechnical", "geotech",
            "single line", "one line", "layout", "plan set",
        ],
    },
    "PERMITTING": {
        "agent": "construction_manager",
        "keywords": [
            "permit", "permitting", "approval", "authority",
            "jurisdiction", "ahj", "inspection", "compliance",
            "environmental", "wetland", "endangered", "cultural",
            "easement", "right of way", "row", "interconnection",
            "utility", "eia", "nepa", "county", "state", "federal",
            "zoning", "variance", "conditional use",
        ],
    },
    "SCHEDULE": {
        "agent": "scheduling_expert",
        "keywords": [
            "schedule", "critical path", "float", "delay", "slip",
            "milestone", "deadline", "substantial completion",
            "mechanical completion", "cod", "ntp", "notice to proceed",
            "baseline", "lookahead", "recovery", "acceleration",
            "liquidated damage", "ld", "timeline", "duration",
            "predecessor", "successor", "lag", "p6", "primavera",
        ],
    },
}


# ---------------------------------------------------------------------------
# SQLite schema for follow-up state tracking
# ---------------------------------------------------------------------------

FOLLOWUP_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS proactive_followup_state (
    constraint_id       TEXT PRIMARY KEY,
    project_key         TEXT,
    priority            TEXT,
    category            TEXT DEFAULT 'CONSTRUCTION',
    last_followup_date  TEXT,
    followup_tier       INTEGER DEFAULT 0,
    last_draft          TEXT,
    cooldown_until      TEXT,
    owner               TEXT,
    description         TEXT,
    need_by_date        TEXT,
    notes_history       TEXT,
    status              TEXT DEFAULT 'open',
    created_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
);
"""


# ---------------------------------------------------------------------------
# Specialist follow-up draft prompts (by constraint category)
# ---------------------------------------------------------------------------

SPECIALIST_PROMPTS = {
    "CONSTRUCTION": """\
You are a senior construction manager drafting a {tier_label} follow-up for a solar construction constraint.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Category: Construction
- Notes history: {notes_history}
{tier_context}

CRITICAL INSTRUCTIONS:
- Think like a construction manager who has been on 100+ jobsites.
- Propose SPECIFIC, ACTIONABLE solutions based on the constraint type.
- If it's a crew/labor issue: suggest crew size adjustments, shift changes, or sub-mobilization.
- If it's a site condition: suggest mitigation approaches (dewatering, soil stabilization, etc.).
- If it's sequencing: suggest re-sequencing options that could unblock the path.
- NEVER write generic "just checking in" or "any update?" messages.
- The follow-up must demonstrate that you UNDERSTAND the constraint and have ideas to help.

{tone_instruction}

Write ONLY the email body. Start with "Hi {owner_first}," and end with "Thanks, Aaron".
Plain text only, no HTML/markdown. 4-6 sentences. Be specific and solution-oriented.
""",

    "PROCUREMENT": """\
You are a cost/procurement analyst drafting a {tier_label} follow-up for a procurement constraint.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Category: Procurement
- Notes history: {notes_history}
{tier_context}

CRITICAL INSTRUCTIONS:
- Think like a procurement specialist who knows solar supply chains inside and out.
- Propose SPECIFIC solutions: split PO for partial delivery, alternative vendors, expedite fees, etc.
- If it's a delivery delay: suggest alternate sourcing, partial shipments, or schedule re-sequence.
- If it's a cost issue: suggest value engineering options, bulk discount opportunities, or phased ordering.
- If it's lead time: calculate if expediting is worth the cost vs. schedule impact.
- NEVER write generic "just checking in" messages.

{tone_instruction}

Write ONLY the email body. Start with "Hi {owner_first}," and end with "Thanks, Aaron".
Plain text only, no HTML/markdown. 4-6 sentences. Be specific and solution-oriented.
""",

    "ENGINEERING": """\
You are a senior construction manager drafting a {tier_label} follow-up for an engineering constraint.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Category: Engineering
- Notes history: {notes_history}
{tier_context}

CRITICAL INSTRUCTIONS:
- Think like someone who manages the interface between engineering and construction.
- Propose SPECIFIC solutions: partial IFC release, fast-track design-build approach, etc.
- If it's a drawing review: suggest parallel review tracks or phased releases.
- If it's a design issue: ask about alternatives that avoid the redesign entirely.
- If it's a calculation/study: suggest whether field conditions data could accelerate the study.
- NEVER write generic "just checking in" messages.

{tone_instruction}

Write ONLY the email body. Start with "Hi {owner_first}," and end with "Thanks, Aaron".
Plain text only, no HTML/markdown. 4-6 sentences. Be specific and solution-oriented.
""",

    "PERMITTING": """\
You are a senior construction manager drafting a {tier_label} follow-up for a permitting constraint.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Category: Permitting
- Notes history: {notes_history}
{tier_context}

CRITICAL INSTRUCTIONS:
- Think like someone who navigates jurisdictional approvals for a living.
- Propose SPECIFIC solutions: pre-application meetings, phased permits, parallel submissions.
- If it's an AHJ delay: suggest escalation through political channels or pre-inspection conferences.
- If it's environmental: suggest phased clearing, seasonal work windows, or mitigation credits.
- If it's interconnection: suggest utility liaison meetings or independent engineer review.
- NEVER write generic "just checking in" messages.

{tone_instruction}

Write ONLY the email body. Start with "Hi {owner_first}," and end with "Thanks, Aaron".
Plain text only, no HTML/markdown. 4-6 sentences. Be specific and solution-oriented.
""",

    "SCHEDULE": """\
You are a CPM scheduling expert drafting a {tier_label} follow-up for a schedule constraint.

Constraint details:
- Project: {project}
- Description: {description}
- Owner/Responsible: {owner}
- Priority: {priority}
- Days open: {days_open}
- Need-by date: {need_by_date}
- Category: Schedule
- Notes history: {notes_history}
{tier_context}

CRITICAL INSTRUCTIONS:
- Think like a scheduling guru who lives and breathes critical path methodology.
- Propose SPECIFIC solutions: activity crashing, resource leveling, re-sequencing, fast-tracking.
- If it's a float issue: quantify the impact and suggest where to recover days.
- If it's a milestone slip: identify which predecessor activities are driving the delay.
- If it's resource-driven: suggest shift work, additional crews, or equipment changes.
- NEVER write generic "just checking in" messages.

{tone_instruction}

Write ONLY the email body. Start with "Hi {owner_first}," and end with "Thanks, Aaron".
Plain text only, no HTML/markdown. 4-6 sentences. Be specific and solution-oriented.
""",
}


# Tier-specific tone instructions
TIER_TONES = {
    1: (
        "Tier 1 (Helpful Suggestion)",
        "Tone: Warm and collaborative. You're a teammate offering help, not a boss checking up.\n"
        "Style: 'Hey, this constraint is coming up on the radar — here is an idea that might help...' / "
        "'Wanted to flag this early so we can get ahead of it. One thought...'",
        "",
    ),
    2: (
        "Tier 2 (Firmer with Alternatives)",
        "Tone: Still collaborative but more direct. You've already reached out once. Show urgency.\n"
        "Style: 'Following up on this again — it has been open {days_open} days. Have you considered X or Y? "
        "Happy to help coordinate if that would be useful.' / 'This one is still open and getting closer to "
        "the need-by date. Here are two options I see...'",
        "Previous follow-up: Tier 1 sent on {last_followup_date}. No resolution yet.",
    ),
    3: (
        "Tier 3 (Loop in Leadership)",
        "Tone: Respectful but urgent. You're bringing in additional resources, not threatening.\n"
        "Style: 'Looping in [leadership] because this constraint has been open {days_open} days and we "
        "could use more resources/support to get it resolved. Here is what I think we need...' / "
        "'I want to bring this to the team because it is impacting the schedule and I think we need "
        "a coordinated push to resolve it.'",
        "Previous follow-ups: Tier 1 and Tier 2 sent. This is the third outreach. "
        "Last follow-up was on {last_followup_date}.",
    ),
}


# ---------------------------------------------------------------------------
# ProactiveFollowUpEngine — the core service
# ---------------------------------------------------------------------------

class ProactiveFollowUpEngine:
    """Generates solution-oriented follow-up drafts and a daily consolidated PDF report.

    Replaces the old EscalationTracker with a collaborative, specialist-driven approach.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open SQLite DB and create the state table if needed."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        # Concurrency and performance pragmas (previously missing)
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA synchronous = NORMAL")
        await self._db.execute("PRAGMA cache_size = -8000")
        await self._db.execute("PRAGMA mmap_size = 67108864")
        await self._db.execute("PRAGMA temp_store = MEMORY")
        await self._db.executescript(FOLLOWUP_STATE_SCHEMA)
        await self._db.commit()
        logger.info(f"Proactive follow-up engine initialized at {self.db_path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------
    # Pull FULL constraint universe from ConstraintsPro
    # ------------------------------------------------------------------

    async def get_all_open_constraints(self) -> list[dict]:
        """Pull ALL open constraints from ConstraintsPro via per-project agent calls.

        Instead of one monolithic agent call that times out iterating 12 projects,
        we fire individual agent calls per project in parallel batches. Each call
        only needs to run ONE MCP tool (constraints_list_by_project) so it finishes
        quickly and reliably.

        Returns the full universe — HIGH, MEDIUM, and LOW priority.
        """
        all_constraints: list[dict] = []

        # Build the list of (project_key, project_name) from config
        project_list = [
            (key, info["name"]) for key, info in PROJECTS.items()
        ]

        # Run in parallel batches of 4 to avoid overwhelming the system
        BATCH_SIZE = 4
        for i in range(0, len(project_list), BATCH_SIZE):
            batch = project_list[i : i + BATCH_SIZE]
            tasks = [
                self._get_constraints_for_project(proj_key, proj_name)
                for proj_key, proj_name in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for (proj_key, proj_name), result in zip(batch, results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Exception pulling constraints for {proj_name}: {result}"
                    )
                    continue
                if result:
                    logger.info(
                        f"Got {len(result)} constraints from {proj_name}"
                    )
                    all_constraints.extend(result)
                else:
                    logger.info(f"No open constraints for {proj_name}")

        logger.info(
            f"Total constraints pulled across {len(project_list)} projects: "
            f"{len(all_constraints)}"
        )
        return all_constraints

    async def _get_constraints_for_project(
        self, project_key: str, project_name: str
    ) -> list[dict]:
        """Pull open constraints for a SINGLE project via constraints_manager agent.

        This is a focused, fast call — the agent only needs to:
          1. Call constraints_list_by_project for the given project
          2. Return the results as JSON

        Typically completes in 15-30 seconds instead of 240+ seconds for all projects.
        """
        from bot.agents.definitions import CONSTRAINTS_MANAGER
        from bot.agents.runner import get_runner

        prompt = (
            f"Pull all OPEN constraints for the project '{project_name}' "
            f"(project key: {project_key}) from ConstraintsPro.\n\n"
            "Steps:\n"
            "1. Call projects_list to find the project ID for this project\n"
            f"2. Call constraints_list_by_project for '{project_name}'\n"
            "3. Include ALL open/in-progress constraints (HIGH, MEDIUM, and LOW priority)\n\n"
            "Return the results as a JSON array. Each object must have these exact fields:\n"
            '  {"id": "...", "project": "' + project_name + '", '
            '"project_key": "' + project_key + '", '
            '"description": "...", "owner": "...", "priority": "HIGH|MEDIUM|LOW", '
            '"status": "open", "need_by_date": "YYYY-MM-DD or null", '
            '"days_open": N, '
            '"notes": "most recent notes or empty string"}\n\n'
            "Output ONLY the JSON array, no other text. If no constraints match, output [].\n"
            "Wrap the JSON in ```json ... ``` code fences."
        )

        runner = get_runner()
        result = await runner.run(
            agent=CONSTRAINTS_MANAGER,
            task_prompt=prompt,
            timeout=120,
        )

        if not result.success or not result.output:
            logger.error(
                f"Failed to pull constraints for {project_name}: {result.error}"
            )
            return []

        constraints = self._parse_constraint_json(result.output)

        # Ensure project fields are set correctly on every constraint
        for c in constraints:
            c.setdefault("project", project_name)
            c.setdefault("project_key", project_key)

        return constraints

    @staticmethod
    def _parse_constraint_json(raw: str) -> list[dict]:
        """Extract a JSON array from Claude's output (may be wrapped in code fences)."""
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        text = fence_match.group(1).strip() if fence_match else raw.strip()

        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            logger.warning("No JSON array found in constraints output")
            return []

        try:
            data = json.loads(text[start:end + 1])
            if not isinstance(data, list):
                return []
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse constraints JSON: {e}")
            return []

    # ------------------------------------------------------------------
    # Categorize constraints by type
    # ------------------------------------------------------------------

    @staticmethod
    def categorize_constraint(constraint: dict) -> str:
        """Determine the category for a constraint based on its description and any existing category.

        Returns one of: CONSTRUCTION, PROCUREMENT, ENGINEERING, PERMITTING, SCHEDULE
        """
        # If the constraint already has a valid category from ConstraintsPro, use it
        existing = constraint.get("category", "").upper()
        if existing in CONSTRAINT_CATEGORIES:
            return existing

        # Otherwise, classify by keyword matching
        desc = (constraint.get("description", "") + " " + constraint.get("notes", "")).lower()

        best_category = "CONSTRUCTION"  # default
        best_score = 0

        for category, info in CONSTRAINT_CATEGORIES.items():
            score = sum(1 for kw in info["keywords"] if kw in desc)
            if score > best_score:
                best_score = score
                best_category = category

        return best_category

    # ------------------------------------------------------------------
    # Determine follow-up tier
    # ------------------------------------------------------------------

    async def determine_tier(self, constraint: dict) -> Optional[int]:
        """Determine which follow-up tier to use for a constraint.

        Returns:
            1, 2, or 3 for the follow-up tier.
            None if the constraint is in cooldown or maxed out.
        """
        cid = constraint.get("id", "")
        cursor = await self._db.execute(
            "SELECT * FROM proactive_followup_state WHERE constraint_id = ?",
            (cid,),
        )
        row = await cursor.fetchone()
        state = dict(row) if row else None

        now = datetime.now(CT)

        if state is None:
            # Never followed up — start at Tier 1
            return 1

        current_tier = state.get("followup_tier", 0)

        # Already at max tier — no further follow-up
        if current_tier >= 3:
            return None

        # Check cooldown (5 days between tiers)
        cooldown_until = state.get("cooldown_until")
        if cooldown_until:
            try:
                cooldown_dt = datetime.fromisoformat(cooldown_until)
                if now < cooldown_dt:
                    logger.debug(
                        f"Constraint {cid} in cooldown until {cooldown_until}"
                    )
                    return None
            except (ValueError, TypeError):
                pass

        # Cooldown expired — advance to next tier
        return current_tier + 1

    # ------------------------------------------------------------------
    # Generate solution-oriented follow-up draft via specialist agent
    # ------------------------------------------------------------------

    async def generate_followup_draft(
        self, constraint: dict, tier: int, category: str
    ) -> Optional[str]:
        """Generate a solution-oriented follow-up draft using the appropriate specialist agent.

        Routes to construction_manager, scheduling_expert, or cost_analyst
        based on the constraint category.
        """
        from bot.agents.definitions import (
            CONSTRUCTION_MANAGER, SCHEDULING_EXPERT, COST_ANALYST, NIMROD,
        )
        from bot.agents.runner import get_runner

        # Select the specialist agent
        agent_map = {
            "construction_manager": CONSTRUCTION_MANAGER,
            "scheduling_expert": SCHEDULING_EXPERT,
            "cost_analyst": COST_ANALYST,
        }
        agent_name = CONSTRAINT_CATEGORIES.get(category, {}).get("agent", "construction_manager")
        agent = agent_map.get(agent_name, NIMROD)

        # Build the prompt context
        owner = constraint.get("owner", "Unassigned")
        owner_first = owner.split()[0] if owner and owner != "Unassigned" else "team"

        tier_label, tone_instruction, tier_context_template = TIER_TONES.get(tier, TIER_TONES[1])

        # Get the last follow-up date from state
        last_followup_date = "N/A"
        cid = constraint.get("id", "")
        cursor = await self._db.execute(
            "SELECT last_followup_date FROM proactive_followup_state WHERE constraint_id = ?",
            (cid,),
        )
        row = await cursor.fetchone()
        if row and row["last_followup_date"]:
            last_followup_date = row["last_followup_date"]

        tier_context = tier_context_template.format(
            days_open=constraint.get("days_open", "?"),
            last_followup_date=last_followup_date,
        ) if tier_context_template else ""

        ctx = {
            "project": constraint.get("project", "Unknown Project"),
            "description": constraint.get("description", "No description"),
            "owner": owner,
            "owner_first": owner_first,
            "priority": constraint.get("priority", "UNKNOWN"),
            "days_open": constraint.get("days_open", "?"),
            "need_by_date": constraint.get("need_by_date") or "Not specified",
            "notes_history": constraint.get("notes", "") or "No notes available",
            "tier_label": tier_label,
            "tone_instruction": tone_instruction.format(
                days_open=constraint.get("days_open", "?"),
            ),
            "tier_context": tier_context,
        }

        # Select the category-specific prompt
        prompt_template = SPECIALIST_PROMPTS.get(category, SPECIALIST_PROMPTS["CONSTRUCTION"])
        prompt = prompt_template.format(**ctx)

        runner = get_runner()
        result = await runner.run(
            agent=agent,
            task_prompt=prompt,
            timeout=120,
            no_tools=True,
        )

        if result.success and result.output:
            # Clean up any structured blocks
            text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", result.output, flags=re.DOTALL)
            return text.strip()

        logger.error(f"Failed to draft follow-up: {result.error}")
        return None

    # ------------------------------------------------------------------
    # Batch draft generation — generates drafts for multiple constraints at once
    # ------------------------------------------------------------------

    async def generate_batch_followup_drafts(
        self, items: list[dict], category: str
    ) -> dict[str, str]:
        """Generate follow-up drafts for a BATCH of constraints in one Claude call.

        Instead of calling Claude once per constraint (135 calls = 45+ minutes),
        this groups constraints by category and generates all drafts in one call
        per category (3-5 calls total = ~3-5 minutes).

        Args:
            items: List of dicts with "constraint", "tier", "category" keys.
            category: The constraint category (CONSTRUCTION, PROCUREMENT, etc.)

        Returns:
            Dict mapping constraint_id → draft text
        """
        from bot.agents.definitions import (
            CONSTRUCTION_MANAGER, SCHEDULING_EXPERT, COST_ANALYST, NIMROD,
        )
        from bot.agents.runner import get_runner

        # Select the specialist agent
        agent_map = {
            "construction_manager": CONSTRUCTION_MANAGER,
            "scheduling_expert": SCHEDULING_EXPERT,
            "cost_analyst": COST_ANALYST,
        }
        agent_name = CONSTRAINT_CATEGORIES.get(category, {}).get("agent", "construction_manager")
        agent = agent_map.get(agent_name, NIMROD)

        # Build the batch prompt
        prompt_template = SPECIALIST_PROMPTS.get(category, SPECIALIST_PROMPTS["CONSTRUCTION"])

        # Build constraint blocks
        constraint_blocks = []
        for idx, item in enumerate(items):
            c = item["constraint"]
            owner = c.get("owner", "Unassigned")
            owner_first = owner.split()[0] if owner and owner != "Unassigned" else "team"
            tier = item.get("tier", 1)
            tier_label = TIER_TONES.get(tier, TIER_TONES[1])[0]

            cid = c.get("id", f"unknown_{idx}")
            block = (
                f"[{idx + 1}] ID: {cid}\n"
                f"    Project: {c.get('project', 'Unknown')}\n"
                f"    Priority: {c.get('priority', 'MEDIUM')}\n"
                f"    Days open: {c.get('days_open', '?')}\n"
                f"    Need-by date: {c.get('need_by_date') or 'Not set'}\n"
                f"    Description: {c.get('description', 'No description')}\n"
                f"    Owner: {owner}\n"
                f"    Follow-up tier: {tier_label}\n"
            )
            notes = c.get("notes", "")
            if notes and notes.strip():
                block += f"    Latest notes: {notes[:300]}\n"
            constraint_blocks.append(block)

        # Format the specialist prompt for batch mode
        batch_prompt = (
            f"{prompt_template.split('Constraint details:')[0]}\n"  # Role preamble only
            f"Generate a solution-oriented follow-up email draft for EACH of the "
            f"following {len(items)} constraints.\n\n"
            f"Each draft MUST:\n"
            f"- Start with 'Hi <owner_first_name>,'\n"
            f"- End with 'Thanks, Aaron'\n"
            f"- Be 4-6 sentences, plain text only\n"
            f"- Propose SPECIFIC, ACTIONABLE solutions (NOT generic 'just checking in')\n"
            f"- Reference the specific project and constraint details\n\n"
            f"For each constraint, output the draft between markers:\n"
            f"===DRAFT_START id=<constraint_id>===\n"
            f"<the email draft>\n"
            f"===DRAFT_END===\n\n"
            f"CONSTRAINTS:\n\n"
            f"{''.join(constraint_blocks)}"
        )

        runner = get_runner()
        result = await runner.run(
            agent=agent,
            task_prompt=batch_prompt,
            timeout=300,  # 5 minutes for a batch
            no_tools=True,
        )

        if not result.success or not result.output:
            logger.error(f"Batch draft generation failed for {category}: {result.error}")
            return {}

        # Parse the drafts
        drafts = {}
        pattern = r'===DRAFT_START\s+id=([^=]+?)===\s*\n(.*?)\n===DRAFT_END==='
        matches = re.findall(pattern, result.output, re.DOTALL)
        for cid, draft_text in matches:
            cid = cid.strip()
            draft_text = draft_text.strip()
            if draft_text:
                # Clean up any structured blocks Claude might add
                draft_text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", draft_text, flags=re.DOTALL)
                draft_text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", draft_text, flags=re.DOTALL)
                drafts[cid] = draft_text.strip()

        logger.info(
            f"Batch draft generation for {category}: {len(drafts)}/{len(items)} drafts generated"
        )
        return drafts

    # ------------------------------------------------------------------
    # Record follow-up state
    # ------------------------------------------------------------------

    async def record_followup(
        self, constraint: dict, tier: int, category: str, draft: str
    ) -> None:
        """Record that a follow-up draft was generated for this constraint."""
        cid = constraint.get("id", "")
        now_str = datetime.now(CT).strftime("%Y-%m-%dT%H:%M:%S")
        cooldown = (datetime.now(CT) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")

        await self._db.execute(
            """
            INSERT INTO proactive_followup_state
                (constraint_id, project_key, priority, category,
                 last_followup_date, followup_tier, last_draft,
                 cooldown_until, owner, description, need_by_date,
                 notes_history, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(constraint_id) DO UPDATE SET
                priority = excluded.priority,
                category = excluded.category,
                last_followup_date = excluded.last_followup_date,
                followup_tier = excluded.followup_tier,
                last_draft = excluded.last_draft,
                cooldown_until = excluded.cooldown_until,
                owner = excluded.owner,
                description = excluded.description,
                need_by_date = excluded.need_by_date,
                notes_history = excluded.notes_history,
                updated_at = excluded.updated_at
            """,
            (
                cid,
                constraint.get("project_key", constraint.get("project", "")),
                constraint.get("priority", ""),
                category,
                now_str,
                tier,
                draft[:3000],
                cooldown,
                constraint.get("owner", ""),
                constraint.get("description", ""),
                constraint.get("need_by_date"),
                constraint.get("notes", ""),
                now_str,
            ),
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Pull commitment-based follow-ups from the followup queue DB
    # ------------------------------------------------------------------

    @staticmethod
    def get_pending_commitments() -> list[dict]:
        """Pull pending commitment-based follow-ups from the followup queue DB.

        These are action items from meetings tracked in followup.py's SQLite DB.
        We include overdue and due-today items in the consolidated PDF report
        so the user has ONE source of truth for what to chase tomorrow.

        Uses synchronous sqlite3 (not aiosqlite) since the followup DB is a
        separate file and we only need a quick read query.
        """
        import sqlite3

        if not FOLLOWUP_DB_PATH.exists():
            return []

        try:
            conn = sqlite3.connect(str(FOLLOWUP_DB_PATH))
            conn.row_factory = sqlite3.Row
            today = datetime.now(CT).strftime("%Y-%m-%d")

            # Pull overdue + due-today items
            cursor = conn.execute(
                "SELECT * FROM follow_ups WHERE follow_up_date <= ? AND status = 'pending' "
                "ORDER BY follow_up_date ASC",
                (today,),
            )
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()

            logger.info(f"Pulled {len(rows)} pending commitment follow-up(s) from followup queue DB")
            return rows
        except Exception:
            logger.exception("Failed to query followup queue DB for pending commitments")
            return []

    # ------------------------------------------------------------------
    # Generate consolidated PDF report
    # ------------------------------------------------------------------

    def generate_pdf_report(
        self, constraints_with_drafts: list[dict], output_path: Path,
        commitment_items: list[dict] | None = None,
    ) -> bool:
        """Generate a professional consolidated PDF report.

        Args:
            constraints_with_drafts: List of dicts, each with:
                constraint, tier, category, draft
            output_path: Where to write the PDF.
            commitment_items: Optional list of dicts from the followup queue DB.
                These are commitment-based follow-ups (action items from meetings)
                that get a separate section at the end of the PDF.

        Returns:
            True on success, False on failure.
        """
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT, TA_CENTER
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import inch
            from reportlab.platypus import (
                BaseDocTemplate, Frame, HRFlowable,
                NextPageTemplate, PageBreak, PageTemplate,
                Paragraph, Spacer, Table, TableStyle,
            )
        except ImportError:
            logger.error("reportlab not installed — cannot generate PDF")
            return False

        # Brand colors
        NAVY = colors.HexColor("#1B365D")
        DARK_NAVY = colors.HexColor("#0F1F3D")
        RED = colors.HexColor("#CC0000")
        AMBER = colors.HexColor("#CC8800")
        GREEN = colors.HexColor("#228B22")
        LIGHT_GREY = colors.HexColor("#F5F5F5")
        MID_GREY = colors.HexColor("#E0E0E0")
        WHITE = colors.white
        BLACK = colors.black
        SOFT_RED_BG = colors.HexColor("#FFF0F0")
        SOFT_AMBER_BG = colors.HexColor("#FFF8E8")
        SOFT_GREEN_BG = colors.HexColor("#F0FFF0")
        TABLE_HEADER_BG = colors.HexColor("#1B365D")
        TABLE_ALT_ROW = colors.HexColor("#F2F6FA")
        ACCENT_BLUE = colors.HexColor("#3A7BD5")

        # Tier colors
        TIER_COLORS = {
            1: colors.HexColor("#2E86AB"),   # Helpful blue
            2: colors.HexColor("#CC8800"),   # Amber
            3: colors.HexColor("#CC0000"),   # Urgent red
        }

        # Priority colors
        PRIORITY_COLORS = {
            "HIGH": RED,
            "MEDIUM": AMBER,
            "LOW": GREEN,
        }

        _base_styles = getSampleStyleSheet()

        def _style(name, **kw):
            return ParagraphStyle(name, parent=_base_styles["Normal"], **kw)

        # Styles
        STYLE_TITLE = _style("PFTitle", fontName="Helvetica-Bold", fontSize=22,
                             textColor=WHITE, leading=28)
        STYLE_SUBTITLE = _style("PFSubtitle", fontName="Helvetica", fontSize=11,
                                textColor=colors.HexColor("#B0C4DE"), leading=14)
        STYLE_H1 = _style("PFH1", fontName="Helvetica-Bold", fontSize=14,
                          textColor=NAVY, leading=20, spaceBefore=16, spaceAfter=6)
        STYLE_H2 = _style("PFH2", fontName="Helvetica-Bold", fontSize=11,
                          textColor=NAVY, leading=16, spaceBefore=10, spaceAfter=4)
        STYLE_BODY = _style("PFBody", fontName="Helvetica", fontSize=9,
                            textColor=BLACK, leading=12, spaceAfter=3)
        STYLE_BODY_BOLD = _style("PFBodyBold", fontName="Helvetica-Bold", fontSize=9,
                                 textColor=BLACK, leading=12, spaceAfter=3)
        STYLE_DRAFT = _style("PFDraft", fontName="Courier", fontSize=8.5,
                             textColor=colors.HexColor("#333333"), leading=11,
                             leftIndent=12, rightIndent=12, spaceBefore=4, spaceAfter=4,
                             backColor=colors.HexColor("#F8F9FA"),
                             borderWidth=0.5, borderColor=MID_GREY, borderPadding=6)
        STYLE_SMALL = _style("PFSmall", fontName="Helvetica", fontSize=7.5,
                             textColor=colors.HexColor("#666666"), leading=10)
        STYLE_TIER = _style("PFTier", fontName="Helvetica-Bold", fontSize=8.5,
                            textColor=WHITE, leading=11)
        STYLE_TABLE_HEADER = _style("PFTH", fontName="Helvetica-Bold", fontSize=8,
                                    textColor=WHITE, leading=10)
        STYLE_TABLE_CELL = _style("PFTC", fontName="Helvetica", fontSize=8,
                                  textColor=BLACK, leading=10)
        # Reply-awareness banner style — green-tinted box to flag constraints
        # that already received email replies (so the user reviews before following up)
        REPLY_BANNER_BG = colors.HexColor("#E8F5E9")
        REPLY_BANNER_BORDER = colors.HexColor("#4CAF50")
        STYLE_REPLY_BANNER = _style(
            "PFReplyBanner", fontName="Helvetica-Bold", fontSize=8.5,
            textColor=colors.HexColor("#2E7D32"), leading=12,
            leftIndent=8, rightIndent=8, spaceBefore=4, spaceAfter=4,
            backColor=REPLY_BANNER_BG,
            borderWidth=0.5, borderColor=REPLY_BANNER_BORDER, borderPadding=6,
        )
        STYLE_REPLY_DETAIL = _style(
            "PFReplyDetail", fontName="Helvetica", fontSize=8,
            textColor=colors.HexColor("#37474F"), leading=11,
            leftIndent=12, rightIndent=8, spaceBefore=1, spaceAfter=2,
        )

        today = datetime.now(CT)
        date_str = today.strftime("%B %d, %Y")
        file_date = today.strftime("%Y-%m-%d")

        # Organize by project, then by priority
        by_project = {}
        for item in constraints_with_drafts:
            c = item["constraint"]
            project = c.get("project", "Unknown Project")
            if project not in by_project:
                by_project[project] = []
            by_project[project].append(item)

        # Sort within each project: HIGH first, then MEDIUM, then LOW
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        for project in by_project:
            by_project[project].sort(
                key=lambda x: (
                    priority_order.get(x["constraint"].get("priority", "LOW"), 3),
                    -(x["constraint"].get("days_open", 0) or 0),
                )
            )

        # Page setup
        w, h = letter

        def _header_footer(canvas, doc):
            canvas.saveState()
            # Header bar
            canvas.setFillColor(NAVY)
            canvas.rect(0, h - 45, w, 45, stroke=0, fill=1)
            canvas.setFillColor(WHITE)
            canvas.setFont("Helvetica-Bold", 10)
            canvas.drawString(0.75 * inch, h - 30, "GOLIATH End-of-Day Follow-Up Report")
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#B0C4DE"))
            canvas.drawRightString(w - 0.75 * inch, h - 30, date_str)
            # Footer
            canvas.setStrokeColor(MID_GREY)
            canvas.setLineWidth(0.5)
            canvas.line(0.75 * inch, 0.5 * inch, w - 0.75 * inch, 0.5 * inch)
            canvas.setFillColor(colors.HexColor("#999999"))
            canvas.setFont("Helvetica", 6.5)
            canvas.drawString(0.75 * inch, 0.35 * inch,
                              "Generated by GOLIATH — Copy-paste drafts into emails. No auto-send.")
            canvas.drawRightString(w - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
            canvas.restoreState()

        def _first_page(canvas, doc):
            canvas.saveState()
            # Tall header
            header_h = 90
            canvas.setFillColor(NAVY)
            canvas.rect(0, h - header_h, w, header_h, stroke=0, fill=1)
            # Accent line
            canvas.setFillColor(ACCENT_BLUE)
            canvas.rect(0, h - header_h, w, 3, stroke=0, fill=1)
            # Title
            canvas.setFillColor(WHITE)
            canvas.setFont("Helvetica-Bold", 22)
            canvas.drawString(0.75 * inch, h - 40, "End-of-Day Follow-Up Report")
            canvas.setFont("Helvetica", 11)
            canvas.setFillColor(colors.HexColor("#B0C4DE"))
            commitment_count = len(commitment_items) if commitment_items else 0
            subtitle = (
                f"{date_str}  |  {len(constraints_with_drafts)} constraints  |  "
                f"{len(by_project)} projects"
            )
            if commitment_count:
                subtitle += f"  |  {commitment_count} commitments"
            canvas.drawString(0.75 * inch, h - 58, subtitle)
            canvas.setFont("Helvetica", 9)
            canvas.setFillColor(colors.HexColor("#8FAFD0"))
            canvas.drawString(0.75 * inch, h - 74,
                              "What to chase tomorrow — copy-paste drafts organized by project and priority")
            # Footer
            canvas.setStrokeColor(MID_GREY)
            canvas.setLineWidth(0.5)
            canvas.line(0.75 * inch, 0.5 * inch, w - 0.75 * inch, 0.5 * inch)
            canvas.setFillColor(colors.HexColor("#999999"))
            canvas.setFont("Helvetica", 6.5)
            canvas.drawString(0.75 * inch, 0.35 * inch,
                              "Generated by GOLIATH — Copy-paste drafts into emails. No auto-send.")
            canvas.drawRightString(w - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
            canvas.restoreState()

        first_frame = Frame(
            0.75 * inch, 0.65 * inch,
            w - 1.5 * inch, h - 1.65 * inch,
            id="first",
        )
        later_frame = Frame(
            0.75 * inch, 0.65 * inch,
            w - 1.5 * inch, h - 1.25 * inch,
            id="later",
        )

        first_tmpl = PageTemplate(id="First", frames=[first_frame], onPage=_first_page)
        later_tmpl = PageTemplate(id="Later", frames=[later_frame], onPage=_header_footer)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc = BaseDocTemplate(
            str(output_path),
            pagesize=letter,
            title=f"GOLIATH End-of-Day Follow-Up Report — {date_str}",
            author="GOLIATH System",
        )
        doc.addPageTemplates([first_tmpl, later_tmpl])

        elements = []

        # --- Executive Summary ---
        total = len(constraints_with_drafts)
        high_count = sum(1 for x in constraints_with_drafts if x["constraint"].get("priority") == "HIGH")
        medium_count = sum(1 for x in constraints_with_drafts if x["constraint"].get("priority") == "MEDIUM")
        low_count = total - high_count - medium_count

        tier_counts = {1: 0, 2: 0, 3: 0}
        replied_count = 0
        for item in constraints_with_drafts:
            t = item.get("tier", 1)
            tier_counts[t] = tier_counts.get(t, 0) + 1
            if item.get("recent_replies"):
                replied_count += 1

        elements.append(Paragraph("EXECUTIVE SUMMARY", STYLE_H1))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                                   spaceBefore=2, spaceAfter=6))

        summary_data = [
            [Paragraph("<b>Total</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>HIGH</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>MEDIUM</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>LOW</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>Tier 1</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>Tier 2</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>Tier 3</b>", STYLE_TABLE_HEADER),
             Paragraph("<b>Replies</b>", STYLE_TABLE_HEADER)],
            [Paragraph(str(total), STYLE_TABLE_CELL),
             Paragraph(str(high_count), STYLE_TABLE_CELL),
             Paragraph(str(medium_count), STYLE_TABLE_CELL),
             Paragraph(str(low_count), STYLE_TABLE_CELL),
             Paragraph(str(tier_counts.get(1, 0)), STYLE_TABLE_CELL),
             Paragraph(str(tier_counts.get(2, 0)), STYLE_TABLE_CELL),
             Paragraph(str(tier_counts.get(3, 0)), STYLE_TABLE_CELL),
             Paragraph(str(replied_count), STYLE_TABLE_CELL)],
        ]
        summary_table = Table(summary_data, colWidths=[0.9*inch, 0.7*inch, 0.85*inch, 0.65*inch, 0.65*inch, 0.65*inch, 0.65*inch, 0.75*inch])
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("GRID", (0, 0), (-1, -1), 0.5, MID_GREY),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 8))

        # Tier legend
        legend_parts = [
            "<b>Tier Legend:</b> "
            '<font color="#2E86AB">Tier 1 = Helpful suggestion</font> | '
            '<font color="#CC8800">Tier 2 = Firmer with alternatives</font> | '
            '<font color="#CC0000">Tier 3 = Loop in leadership</font>',
        ]
        elements.append(Paragraph(legend_parts[0], STYLE_SMALL))
        if replied_count:
            elements.append(Paragraph(
                f'<font color="#2E7D32"><b>Replies:</b> {replied_count} constraint(s) '
                f'received email replies in the last {REPLY_LOG_AWARENESS_HOURS}h '
                f'-- flagged with green banners below. Review before following up.</font>',
                STYLE_SMALL,
            ))
        elements.append(Spacer(1, 6))

        elements.append(NextPageTemplate("Later"))

        # --- Per-project sections ---
        for project_name in sorted(by_project.keys()):
            items = by_project[project_name]

            elements.append(HRFlowable(width="100%", thickness=1.5, color=NAVY,
                                       spaceBefore=10, spaceAfter=8))
            elements.append(Paragraph(
                f"{project_name} ({len(items)} constraint{'s' if len(items) != 1 else ''})",
                STYLE_H1,
            ))

            for idx, item in enumerate(items):
                c = item["constraint"]
                tier = item["tier"]
                category = item["category"]
                draft = item["draft"]

                priority = c.get("priority", "LOW")
                priority_color = PRIORITY_COLORS.get(priority, BLACK)
                tier_color = TIER_COLORS.get(tier, ACCENT_BLUE)
                tier_label = TIER_TONES.get(tier, TIER_TONES[1])[0]

                p_hex = priority_color.hexval() if hasattr(priority_color, "hexval") else str(priority_color)
                t_hex = tier_color.hexval() if hasattr(tier_color, "hexval") else str(tier_color)

                # Constraint header
                elements.append(Paragraph(
                    f'<font color="{p_hex}"><b>[{priority}]</b></font> '
                    f'{c.get("description", "No description")[:120]}',
                    STYLE_H2,
                ))

                # Metadata line
                owner = c.get("owner", "Unassigned")
                days_open = c.get("days_open", "?")
                need_by = c.get("need_by_date", "Not set")
                elements.append(Paragraph(
                    f'<b>Owner:</b> {owner}  |  '
                    f'<b>Days open:</b> {days_open}  |  '
                    f'<b>Need by:</b> {need_by}  |  '
                    f'<b>Category:</b> {category}  |  '
                    f'<font color="{t_hex}"><b>{tier_label}</b></font>',
                    STYLE_SMALL,
                ))

                # Notes history (if available)
                notes = c.get("notes", "")
                if notes:
                    elements.append(Spacer(1, 2))
                    elements.append(Paragraph(
                        f'<b>Latest notes:</b> <i>{notes[:200]}</i>',
                        STYLE_SMALL,
                    ))

                # Reply-awareness banner — if this constraint received an
                # email reply recently, flag it prominently so the user
                # reviews the reply before sending a follow-up
                recent_replies = item.get("recent_replies", [])
                if recent_replies:
                    elements.append(Spacer(1, 4))
                    # Primary banner
                    latest = recent_replies[0]
                    ts = latest.get("timestamp", "")[:10]
                    reply_sender = latest.get("sender_name") or latest.get("sender", "someone")
                    reply_sender_safe = reply_sender.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    elements.append(Paragraph(
                        f'REPLY RECEIVED {ts} from {reply_sender_safe} '
                        f'-- review before following up',
                        STYLE_REPLY_BANNER,
                    ))
                    # Reply summary detail
                    reply_summary = latest.get("reply_summary", "")
                    if reply_summary:
                        summary_safe = reply_summary[:250].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        elements.append(Paragraph(
                            f'<i>"{summary_safe}"</i>',
                            STYLE_REPLY_DETAIL,
                        ))
                    # If multiple replies, note that
                    if len(recent_replies) > 1:
                        elements.append(Paragraph(
                            f'<i>({len(recent_replies)} replies in the last {REPLY_LOG_AWARENESS_HOURS}h)</i>',
                            STYLE_REPLY_DETAIL,
                        ))

                # Draft (copy-paste ready)
                elements.append(Spacer(1, 4))
                draft_label_prefix = ""
                if recent_replies:
                    draft_label_prefix = "DRAFT (may be unnecessary) -- "
                elements.append(Paragraph(
                    f"<b>{draft_label_prefix}COPY-PASTE FOLLOW-UP DRAFT:</b>",
                    _style(f"DraftLabel{idx}", fontName="Helvetica-Bold", fontSize=8,
                           textColor=NAVY, leading=10, spaceBefore=2, spaceAfter=2),
                ))

                # Format the draft preserving line breaks
                draft_formatted = draft.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                draft_formatted = draft_formatted.replace("\n", "<br/>")
                elements.append(Paragraph(draft_formatted, STYLE_DRAFT))

                elements.append(Spacer(1, 8))

        # --- Commitment-Based Follow-Ups Section ---
        # These come from the followup queue DB (action items from meetings).
        # Added 2026-03-01 to consolidate ALL follow-ups into one PDF.
        if commitment_items:
            elements.append(HRFlowable(width="100%", thickness=2, color=NAVY,
                                       spaceBefore=14, spaceAfter=8))
            elements.append(Paragraph(
                f"COMMITMENT FOLLOW-UPS ({len(commitment_items)} pending)",
                STYLE_H1,
            ))
            elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                                       spaceBefore=2, spaceAfter=6))
            elements.append(Paragraph(
                "<i>Action items from meetings and conversations that need follow-up. "
                "These are commitment-based (someone promised something), not constraint-based.</i>",
                STYLE_SMALL,
            ))
            elements.append(Spacer(1, 6))

            # Group commitment items by project
            commitments_by_project: dict[str, list[dict]] = {}
            for ci in commitment_items:
                proj = ci.get("project_key") or "General"
                # Look up the display name from PROJECTS config
                proj_display = PROJECTS.get(proj, {}).get("name", proj) if proj != "General" else "General"
                commitments_by_project.setdefault(proj_display, []).append(ci)

            for proj_name in sorted(commitments_by_project.keys()):
                proj_items = commitments_by_project[proj_name]
                elements.append(Paragraph(
                    f"{proj_name} ({len(proj_items)} item{'s' if len(proj_items) != 1 else ''})",
                    STYLE_H2,
                ))

                for ci in proj_items:
                    commitment_text = (ci.get("commitment") or "No description")[:200]
                    commitment_text = commitment_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    owner = (ci.get("owner") or "Unassigned").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    committed_date = ci.get("committed_date", "?")
                    follow_up_date = ci.get("follow_up_date", "?")

                    today_str = datetime.now(CT).strftime("%Y-%m-%d")
                    is_overdue = follow_up_date and follow_up_date < today_str
                    overdue_tag = ' <font color="#CC0000"><b>[OVERDUE]</b></font>' if is_overdue else ""

                    elements.append(Paragraph(
                        f"{overdue_tag} {commitment_text}",
                        STYLE_BODY_BOLD,
                    ))
                    elements.append(Paragraph(
                        f"<b>Owner:</b> {owner}  |  "
                        f"<b>Committed:</b> {committed_date}  |  "
                        f"<b>Due:</b> {follow_up_date}",
                        STYLE_SMALL,
                    ))
                    elements.append(Spacer(1, 6))

        # Build the PDF
        try:
            doc.build(elements)
            logger.info(f"PDF report generated: {output_path}")
            return True
        except Exception:
            logger.exception("Failed to build PDF report")
            return False

    # ------------------------------------------------------------------
    # Main entry point — daily run
    # ------------------------------------------------------------------

    async def run_daily_report(self, bot, chat_id: int) -> int:
        """Run the full daily proactive follow-up cycle.

        1. Pull all open constraints from ConstraintsPro
        2. Pull pending commitment follow-ups from the followup queue DB
        3. Categorize and determine tier for each constraint
        4. Generate specialist drafts for those needing follow-up
        5. Generate ONE consolidated PDF with both constraint and commitment items
        6. Send PDF to Telegram

        The PDF is the single source of truth for "what do I need to chase tomorrow".

        Returns the number of constraints included in the report.
        """
        logger.info("Proactive follow-up daily report starting...")
        start = time.monotonic()

        try:
            # 1. Pull all open constraints
            constraints = await self.get_all_open_constraints()
            logger.info(f"Pulled {len(constraints)} open constraint(s) from ConstraintsPro")

            # 1b. Pull pending commitment follow-ups from the followup queue DB
            commitment_items = self.get_pending_commitments()

            # 1c. Build reply-awareness lookup — check which constraints
            #     already received email replies in the last 48 hours so
            #     the PDF can annotate them instead of generating blind drafts
            reply_lookup: dict[str, list[dict]] = {}
            try:
                from bot.services.reply_log import build_reply_lookup
                reply_lookup = build_reply_lookup()
                if reply_lookup:
                    logger.info(
                        f"Reply-awareness: {len(reply_lookup)} constraint(s) have "
                        f"recent email replies in the log"
                    )
            except Exception:
                logger.debug("Reply log unavailable — proceeding without reply-awareness", exc_info=True)

            if not constraints and not commitment_items:
                logger.info("No open constraints or commitment items — skipping report")
                await _send_telegram_message(
                    bot, chat_id,
                    "<b>Proactive Follow-Up Report</b>\n\n"
                    "<i>No open constraints or pending commitments. Nothing to report today.</i>"
                )
                return 0

            # 2. Categorize each constraint and determine tier
            items_to_draft = []
            for constraint in constraints:
                category = self.categorize_constraint(constraint)
                constraint["_category"] = category

                tier = await self.determine_tier(constraint)
                # Check for recent email replies to this constraint
                cid = constraint.get("id", "")
                recent_replies = reply_lookup.get(cid, [])

                if tier is None:
                    # In cooldown or maxed out — still include in report with current tier
                    cursor = await self._db.execute(
                        "SELECT followup_tier, last_draft FROM proactive_followup_state WHERE constraint_id = ?",
                        (cid,),
                    )
                    row = await cursor.fetchone()
                    if row and row["last_draft"]:
                        # Include with existing draft (already followed up, in cooldown)
                        items_to_draft.append({
                            "constraint": constraint,
                            "tier": row["followup_tier"],
                            "category": category,
                            "draft": row["last_draft"],
                            "cached": True,
                            "recent_replies": recent_replies,
                        })
                    continue

                items_to_draft.append({
                    "constraint": constraint,
                    "tier": tier,
                    "category": category,
                    "draft": None,
                    "cached": False,
                    "recent_replies": recent_replies,
                })

            if not items_to_draft and not commitment_items:
                logger.info("All constraints in cooldown and no commitment items — skipping report")
                return 0

            # 3. Generate drafts using BATCH mode — groups constraints by category
            #    and sends each group to the appropriate specialist agent in ONE call.
            #    This is 3-5 Claude calls instead of 135 individual ones.
            draft_count = 0

            # Group non-cached items by category for batch processing
            items_needing_drafts = [i for i in items_to_draft if not i["cached"]]
            by_category: dict[str, list[dict]] = {}
            for item in items_needing_drafts:
                cat = item["category"]
                by_category.setdefault(cat, []).append(item)

            logger.info(
                f"Batch drafting {len(items_needing_drafts)} constraints across "
                f"{len(by_category)} categories: {list(by_category.keys())}"
            )

            # Process each category batch
            for category, category_items in by_category.items():
                try:
                    # Split into sub-batches of 35 to avoid token limits
                    for batch_start in range(0, len(category_items), 35):
                        batch = category_items[batch_start : batch_start + 35]

                        batch_drafts = await self.generate_batch_followup_drafts(
                            batch, category
                        )

                        # Assign drafts to items
                        for item in batch:
                            cid = item["constraint"].get("id", "")
                            if cid in batch_drafts:
                                item["draft"] = batch_drafts[cid]
                                draft_count += 1

                                # Record the follow-up state
                                await self.record_followup(
                                    item["constraint"], item["tier"],
                                    item["category"], batch_drafts[cid],
                                )
                            else:
                                # Specialist missed this one — provide fallback
                                owner = item["constraint"].get("owner", "Team")
                                owner_first = owner.split()[0] if owner != "Unassigned" else "Team"
                                item["draft"] = (
                                    f"Hi {owner_first},\n\n"
                                    f"Following up on: {item['constraint'].get('description', 'N/A')}\n\n"
                                    f"Can you provide a status update? Happy to discuss.\n\n"
                                    f"Thanks, Aaron"
                                )

                        # Small delay between category batches
                        await asyncio.sleep(1)

                except Exception:
                    logger.exception(
                        f"Error in batch draft generation for {category}"
                    )
                    # Fallback: provide simple drafts for all items in this category
                    for item in category_items:
                        if not item.get("draft"):
                            item["draft"] = "[Draft generation error — follow up manually]"

            # Filter out items without drafts
            items_with_drafts = [i for i in items_to_draft if i.get("draft")]

            if not items_with_drafts and not commitment_items:
                logger.warning("No drafts generated and no commitment items — skipping PDF")
                return 0

            # 4. Generate the PDF (includes both constraint drafts and commitment items)
            today = datetime.now(CT)
            pdf_filename = f"{today.strftime('%Y-%m-%d')}-proactive-followup-report.pdf"
            pdf_path = REPO_ROOT / "reports" / pdf_filename

            success = self.generate_pdf_report(
                items_with_drafts, pdf_path,
                commitment_items=commitment_items or None,
            )
            if not success:
                logger.error("PDF generation failed")
                await _send_telegram_message(
                    bot, chat_id,
                    "<b>Proactive Follow-Up Report</b>\n\n"
                    f"Generated {len(items_with_drafts)} follow-up drafts but PDF generation failed. "
                    "Check logs for details."
                )
                return len(items_with_drafts)

            # 5. Send the PDF to Telegram
            from bot.scheduler import _send_telegram_document, _send_telegram

            # Build caption including commitment count and reply-awareness info
            commitment_note = ""
            if commitment_items:
                commitment_note = f"\n{len(commitment_items)} commitment follow-up(s) included"

            # Count constraints that have recent replies
            replied_count = sum(
                1 for i in items_with_drafts if i.get("recent_replies")
            )
            reply_note = ""
            if replied_count:
                reply_note = (
                    f"\n{replied_count} constraint(s) have recent email replies "
                    f"-- flagged in report"
                )

            sent = await _send_telegram_document(
                bot, chat_id, pdf_path,
                caption=(
                    f"<b>End-of-Day Follow-Up Report</b>\n"
                    f"{len(items_with_drafts)} constraints across "
                    f"{len(set(i['constraint'].get('project', '') for i in items_with_drafts))} projects"
                    f"{commitment_note}{reply_note}\n"
                    f"<i>Copy-paste drafts are ready inside. What to chase tomorrow.</i>"
                ),
            )

            if not sent:
                # Fallback: send as text summary
                await _send_telegram(
                    bot, chat_id,
                    f"<b>Daily Proactive Follow-Up Report</b>\n\n"
                    f"PDF generated at <code>{pdf_path}</code> but Telegram send failed.\n"
                    f"Contains {len(items_with_drafts)} follow-up drafts across "
                    f"{len(set(i['constraint'].get('project', '') for i in items_with_drafts))} projects."
                )

            duration = time.monotonic() - start
            logger.info(
                f"Proactive follow-up report complete: {len(items_with_drafts)} constraints, "
                f"{draft_count} new drafts generated, PDF sent in {duration:.1f}s"
            )

            # Log to memory
            await self._log_to_memory(items_with_drafts, draft_count)

            return len(items_with_drafts)

        except Exception:
            logger.exception("Proactive follow-up daily report failed")
            return 0

    # ------------------------------------------------------------------
    # Memory logging
    # ------------------------------------------------------------------

    async def _log_to_memory(self, items: list[dict], new_drafts: int) -> None:
        """Log the daily report run to memory."""
        try:
            from bot.memory.store import MemoryStore

            memory = MemoryStore(MEMORY_DB_PATH)
            await memory.initialize()

            projects = set(i["constraint"].get("project", "") for i in items)

            await memory.save(
                category="observation",
                summary=(
                    f"Daily Proactive Follow-Up Report: {len(items)} constraints, "
                    f"{new_drafts} new drafts across {len(projects)} projects"
                ),
                detail=(
                    f"Projects: {', '.join(sorted(projects))}\n"
                    f"New drafts generated: {new_drafts}\n"
                    f"Total items in report: {len(items)}"
                ),
                source="proactive_followup",
                tags="proactive_followup,daily_report",
            )

            await memory.close()
        except Exception:
            logger.exception("Failed to log proactive follow-up to memory")


# ---------------------------------------------------------------------------
# Helper: send a simple Telegram message (avoids circular import)
# ---------------------------------------------------------------------------

async def _send_telegram_message(bot, chat_id: int, text: str) -> None:
    """Send a message to Telegram with HTML fallback."""
    try:
        await bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            logger.exception(f"Failed to send Telegram message to {chat_id}")
