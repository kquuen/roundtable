"""Phase 2: Agent Orchestrator — async concurrent multi-agent dispatch."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from roundtable.models import AgentReview, EvidencePacket
from roundtable.providers import BaseLLMProvider
from roundtable.registry import get_registry
from roundtable.utils import run_async_safely

# Default timeout per agent (seconds)
DEFAULT_AGENT_TIMEOUT = 30

logger = logging.getLogger("roundtable.orchestrator")

_PROVIDER_UNSET = object()


def create_agents(
    agent_count: int = 5,
    provider=_PROVIDER_UNSET,
    domain_name: str | None = None,
):
    """Create agents from the registry, selecting up to agent_count.

    When domain_name is provided, reads the agent list from DomainRegistry.
    Falls back to agent_count selection otherwise.
    """
    registry = get_registry()

    if domain_name:
        from roundtable.domain import DomainRegistry
        domain = DomainRegistry.get(domain_name)
        if domain and domain.agents:
            selected_ids = [sid for sid in domain.agents if sid in registry.list_all()]
            if not selected_ids:
                selected_ids = registry.list_all()[:min(domain.agent_count, len(registry.list_all()))]
            if provider is _PROVIDER_UNSET:
                return [registry.create(sid) for sid in selected_ids]
            return [registry.create(sid, provider=provider) for sid in selected_ids]

    all_ids = registry.list_all()
    selected_ids = all_ids[:min(agent_count, len(all_ids))]
    if provider is _PROVIDER_UNSET:
        return [registry.create(sid) for sid in selected_ids]
    return [registry.create(sid, provider=provider) for sid in selected_ids]


# Alias for backward compatibility
_create_agents = create_agents


def run_orchestrator(
    evidence: EvidencePacket,
    agent_count: int = 5,
    provider: BaseLLMProvider | None | object = _PROVIDER_UNSET,
    timeout: float = DEFAULT_AGENT_TIMEOUT,
    domain_name: str | None = None,
) -> list[AgentReview]:
    """Synchronous wrapper — dispatches agents and collects reviews.

    When provider is set, runs agents concurrently via asyncio.
    Without provider, runs mock agents synchronously.
    """
    if provider is not _PROVIDER_UNSET and provider is not None:
        return run_async_safely(
            run_orchestrator_async(
                evidence,
                agent_count=agent_count,
                provider=provider,
                timeout=timeout,
                domain_name=domain_name,
            ),
            name="run_orchestrator — use run_orchestrator_async() instead",
        )

    # Synchronous path stays deterministic/mock unless a provider is explicitly injected.
    selected = _create_agents(
        agent_count=agent_count,
        provider=None if provider is _PROVIDER_UNSET else provider,
        domain_name=domain_name,
    )

    reviews: list[AgentReview] = []
    for agent in selected:
        review = agent.analyze(evidence)
        reviews.append(review)
    return reviews


async def run_orchestrator_async(
    evidence: EvidencePacket,
    agent_count: int = 5,
    provider=_PROVIDER_UNSET,
    domain_name: str | None = None,
    timeout: float = DEFAULT_AGENT_TIMEOUT,
    budget=None,
    return_meta: bool = False,
):
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
    if provider is _PROVIDER_UNSET:
        selected = _create_agents(agent_count=agent_count, domain_name=domain_name)
    else:
        selected = _create_agents(agent_count=agent_count, provider=provider, domain_name=domain_name)
    llm_attempted = any(agent.provider is not None for agent in selected)

    # Inject budget into each agent's provider
    if budget is not None:
        for agent in selected:
            if agent.provider is not None:
                agent.provider.budget = budget

    logger.info("Dispatching %d agents concurrently (timeout=%ds, domain=%s)", len(selected), timeout, domain_name or "default")

    async def _run_one(agent) -> AgentReview:
        try:
            if agent.provider is not None:
                return await asyncio.wait_for(
                    agent.analyze_async(evidence, budget=budget),
                    timeout=timeout,
                )
            # Mock path: synchronous keyword-based analysis
            return agent.analyze(evidence)
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
    collected = list(reviews)
    if return_meta:
        return collected, {"llm_attempted": llm_attempted}
    return collected
