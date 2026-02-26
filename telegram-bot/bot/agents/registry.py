from bot.agents.definitions import AgentDefinition, ALL_AGENTS, SUBAGENTS


class AgentRegistry:
    """Lookup and list agent definitions."""

    def get(self, name: str) -> AgentDefinition | None:
        return ALL_AGENTS.get(name)

    def get_subagent(self, name: str) -> AgentDefinition | None:
        return SUBAGENTS.get(name)

    def list_all(self) -> list[AgentDefinition]:
        return list(ALL_AGENTS.values())

    def list_subagents(self) -> list[AgentDefinition]:
        return list(SUBAGENTS.values())

    def subagent_names(self) -> list[str]:
        return list(SUBAGENTS.keys())

    def subagent_descriptions(self) -> str:
        """Format subagent list for prompt injection."""
        lines = []
        for agent in SUBAGENTS.values():
            lines.append(f"- {agent.name}: {agent.description}")
        return "\n".join(lines)
