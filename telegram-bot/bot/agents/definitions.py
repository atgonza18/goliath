"""Agent definitions — thin re-export from per-agent modules.

All agent definitions now live in bot.agents.agent_definitions/<agent>.py.
This file re-exports everything for backwards compatibility so that ALL
existing imports continue to work unchanged:

    from bot.agents.definitions import ALL_AGENTS, SUBAGENTS, AgentDefinition
    from bot.agents.definitions import NIMROD, CONSTRAINTS_MANAGER
"""

from bot.agents.agent_definitions import (  # noqa: F401
    AgentDefinition,
    ALL_AGENTS,
    SUBAGENTS,
    NIMROD,
    SCHEDULE_ANALYST,
    CONSTRAINTS_MANAGER,
    POD_ANALYST,
    REPORT_WRITER,
    EXCEL_EXPERT,
    CONSTRUCTION_MANAGER,
    SCHEDULING_EXPERT,
    COST_ANALYST,
    DEVOPS,
    RESEARCHER,
    FOLDER_ORGANIZER,
    TRANSCRIPT_PROCESSOR,
)
