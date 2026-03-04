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
    # Per-agent model override.  When set, this model is used instead of the
    # global AGENT_MODEL default.  Use for agents that need heavier reasoning
    # (e.g., Opus for complex constraint analysis, schedule recovery planning).
    model: str = None
    # Per-agent effort level for extended thinking depth.
    # Controls how much "thinking" the model does before responding.
    # Values: "low", "medium", "high", "max", or None (SDK default).
    # Use "max" for deep analytical work, "high" for coding/extraction,
    # leave None for routine tasks (routing, formatting, scanning).
    effort: str = None
