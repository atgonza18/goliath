from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentDefinition:
    """Definition of a specialized subagent."""

    name: str
    display_name: str
    description: str
    system_prompt: str
    timeout: float = None
    can_write_files: bool = False
