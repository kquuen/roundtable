"""Agent Registry — factory pattern for skill_id → Agent class mapping.

Replaces hardcoded agent imports in orchestrator.py, enabling:
- Dynamic agent discovery (5 built-in → 131 skills)
- Plugin-based registration
- Test-friendly injection
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from roundtable.agents import Agent
    from roundtable.providers import ProviderAdapter


class AgentRegistry:
    """Central registry mapping skill_id → Agent class.

    Usage:
        registry = AgentRegistry()
        registry.register(ProductManager)  # auto-detects skill_id from class
        agent = registry.create("product_manager", provider=provider)
    """

    def __init__(self):
        self._agents: dict[str, type["Agent"]] = {}

    def register(self, agent_cls: type["Agent"]) -> None:
        """Register an Agent class by its skill_id.

        The skill_id is auto-detected by instantiating a throwaway instance.
        """
        # Instantiate without provider just to read skill.skill_id
        temp = agent_cls()
        self._agents[temp.agent_id] = agent_cls

    def create(
        self,
        skill_id: str,
        provider: "ProviderAdapter | None" = None,
    ) -> "Agent":
        """Create an Agent instance for the given skill_id.

        Raises KeyError if skill_id is not registered.
        """
        if skill_id not in self._agents:
            raise KeyError(
                f"Skill '{skill_id}' not registered. "
                f"Available: {list(self._agents)}"
            )
        return self._agents[skill_id](provider=provider)

    def list_all(self) -> list[str]:
        """Return all registered skill IDs."""
        return list(self._agents)

    def get_cls(self, skill_id: str) -> type["Agent"]:
        """Return the Agent class (without instantiation)."""
        if skill_id not in self._agents:
            raise KeyError(f"Skill '{skill_id}' not registered")
        return self._agents[skill_id]

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, skill_id: str) -> bool:
        return skill_id in self._agents


# ── Module-level singleton ──

_registry: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    """Get or lazily initialize the global agent registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
        _init_builtin_agents(_registry)
    return _registry


def _init_builtin_agents(registry: AgentRegistry) -> None:
    """Register all 5 built-in expert agents."""
    from roundtable.agents import (
        ProductManager, Architect, ProjectManager,
        BusinessAnalyst, SupervisorAgent,
    )
    registry.register(ProductManager)
    registry.register(Architect)
    registry.register(ProjectManager)
    registry.register(BusinessAnalyst)
    registry.register(SupervisorAgent)


def reset_registry() -> None:
    """Reset the global registry (for testing)."""
    global _registry
    _registry = None
