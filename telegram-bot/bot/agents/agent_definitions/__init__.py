"""Per-agent definition modules, re-exported as a single public API."""

from bot.agents.agent_definitions.base import AgentDefinition

from bot.agents.agent_definitions.nimrod import NIMROD
from bot.agents.agent_definitions.schedule_analyst import SCHEDULE_ANALYST
from bot.agents.agent_definitions.constraints_manager import CONSTRAINTS_MANAGER
from bot.agents.agent_definitions.pod_analyst import POD_ANALYST
from bot.agents.agent_definitions.report_writer import REPORT_WRITER
from bot.agents.agent_definitions.excel_expert import EXCEL_EXPERT
from bot.agents.agent_definitions.construction_manager import CONSTRUCTION_MANAGER
from bot.agents.agent_definitions.scheduling_expert import SCHEDULING_EXPERT
from bot.agents.agent_definitions.cost_analyst import COST_ANALYST
from bot.agents.agent_definitions.devops import DEVOPS
from bot.agents.agent_definitions.researcher import RESEARCHER
from bot.agents.agent_definitions.folder_organizer import FOLDER_ORGANIZER
from bot.agents.agent_definitions.transcript_processor import TRANSCRIPT_PROCESSOR

ALL_AGENTS = {
    "nimrod": NIMROD,
    "schedule_analyst": SCHEDULE_ANALYST,
    "constraints_manager": CONSTRAINTS_MANAGER,
    "pod_analyst": POD_ANALYST,
    "report_writer": REPORT_WRITER,
    "excel_expert": EXCEL_EXPERT,
    "construction_manager": CONSTRUCTION_MANAGER,
    "scheduling_expert": SCHEDULING_EXPERT,
    "cost_analyst": COST_ANALYST,
    "devops": DEVOPS,
    "researcher": RESEARCHER,
    "folder_organizer": FOLDER_ORGANIZER,
    "transcript_processor": TRANSCRIPT_PROCESSOR,
}

SUBAGENTS = {k: v for k, v in ALL_AGENTS.items() if k != "nimrod"}

__all__ = [
    "AgentDefinition",
    "ALL_AGENTS",
    "SUBAGENTS",
    "NIMROD",
    "SCHEDULE_ANALYST",
    "CONSTRAINTS_MANAGER",
    "POD_ANALYST",
    "REPORT_WRITER",
    "EXCEL_EXPERT",
    "CONSTRUCTION_MANAGER",
    "SCHEDULING_EXPERT",
    "COST_ANALYST",
    "DEVOPS",
    "RESEARCHER",
    "FOLDER_ORGANIZER",
    "TRANSCRIPT_PROCESSOR",
]
