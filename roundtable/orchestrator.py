"""Phase 2: Agent Orchestrator — synchronous multi-agent dispatch."""

from __future__ import annotations

from roundtable.models import AgentReview, EvidencePacket
from roundtable.agents import (
    ProductManager, Architect, ProjectManager, BusinessAnalyst, SupervisorAgent,
)


def run_orchestrator(
    evidence: EvidencePacket,
    agent_count: int = 5,
) -> list[AgentReview]:
    """Dispatch agents synchronously and collect reviews.

    Args:
        evidence: EvidencePacket to analyze
        agent_count: 1-5 agents to dispatch

    Returns:
        List of AgentReview from each agent
    """
    all_agents = [
        ProductManager(),
        Architect(),
        ProjectManager(),
        BusinessAnalyst(),
        SupervisorAgent(),
    ]

    selected = all_agents[:min(agent_count, len(all_agents))]
    reviews: list[AgentReview] = []

    for agent in selected:
        review = agent.analyze(evidence)
        reviews.append(review)

    return reviews
