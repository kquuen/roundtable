"""Phase 2: Agent Orchestrator — async concurrent multi-agent dispatch."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from roundtable.models import AgentReview, EvidencePacket
from roundtable.providers import ProviderAdapter
from roundtable.agents import (
    ProductManager, Architect, ProjectManager, BusinessAnalyst, SupervisorAgent,
)

# Default timeout per agent (seconds)
DEFAULT_AGENT_TIMEOUT = 30

logger = logging.getLogger("roundtable.orchestrator")


def run_orchestrator(
    evidence: EvidencePacket,
    agent_count: int = 5,
    provider: ProviderAdapter | None = None,
    timeout: float = DEFAULT_AGENT_TIMEOUT,
) -> list[AgentReview]:
    """Synchronous wrapper — dispatches agents and collects reviews.

    When provider is set, runs agents concurrently via asyncio.
    Without provider, runs mock agents synchronously.
    """
    if provider is not None:
        return asyncio.run(
            run_orchestrator_async(evidence, agent_count, provider, timeout)
        )

    # Mock path: synchronous keyword-based agents
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


async def run_orchestrator_async(
    evidence: EvidencePacket,
    agent_count: int = 5,
    provider: ProviderAdapter | None = None,
    timeout: float = DEFAULT_AGENT_TIMEOUT,
) -> list[AgentReview]:
    """Async concurrent dispatch — all agents run in parallel via asyncio.gather().

    Each agent gets its own timeout. If an agent times out or errors,
    a fallback review is inserted so the pipeline doesn't fail entirely.

    Args:
        evidence: EvidencePacket to analyze
        agent_count: 1-5 agents to dispatch
        provider: LLM provider adapter
        timeout: Per-agent timeout in seconds

    Returns:
        List of AgentReview from each agent
    """
    all_agents = [
        ProductManager(provider=provider),
        Architect(provider=provider),
        ProjectManager(provider=provider),
        BusinessAnalyst(provider=provider),
        SupervisorAgent(provider=provider),
    ]
    selected = all_agents[:min(agent_count, len(all_agents))]
    logger.info("Dispatching %d agents concurrently (timeout=%ds)", len(selected), timeout)

    async def _run_one(agent) -> AgentReview:
        try:
            return await asyncio.wait_for(
                agent.analyze_async(evidence),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Agent %s timed out after %ds", agent.agent_id, timeout)
            return AgentReview(
                agent_id=agent.agent_id,
                summary=f"[超时] {agent.skill.name} 在 {timeout}s 内未完成分析",
                claims=[],
                open_questions=["分析超时，建议减少 agent_count 或增加超时时间"],
                recommended_next_actions=[],
            )
        except Exception as e:
            logger.error("Agent %s failed: %s", agent.agent_id, e)
            return AgentReview(
                agent_id=agent.agent_id,
                summary=f"[错误] {agent.skill.name} 分析失败：{e}",
                claims=[],
                open_questions=[],
                recommended_next_actions=[],
            )

    reviews = await asyncio.gather(*[_run_one(a) for a in selected])
    return list(reviews)
