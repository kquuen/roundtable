"""Phase 4: Team Builder — session classification and team recommendation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

from roundtable.models import EvidencePacket, TeamTemplate
from roundtable.providers import ProviderAdapter
from roundtable.utils import run_async_safely


# ── Built-in team templates ──

BUILTIN_TEAMS: list[TeamTemplate] = [
    TeamTemplate(
        team_id="broad_opportunity",
        name="广域机会发现队",
        description="不确定会议能延伸出什么价值时使用",
        suitable_scenarios=["探索", "头脑风暴", "战略讨论"],
        recommended_agents=[
            "product_manager", "architect", "business_analyst",
            "project_manager", "supervisor",
        ],
        capability_scores={
            "产品价值": 80, "技术可行性": 60, "商业判断": 85,
            "执行落地": 65, "风险审查": 70, "用户洞察": 75,
        },
    ),
    TeamTemplate(
        team_id="product_deep_dive",
        name="产品深挖队",
        description="产品方案细化，需求讨论",
        suitable_scenarios=["产品需求", "用户价值", "功能评估"],
        recommended_agents=[
            "product_manager", "business_analyst",
            "project_manager", "supervisor",
        ],
        capability_scores={
            "产品价值": 90, "技术可行性": 50, "商业判断": 80,
            "执行落地": 60, "风险审查": 55, "用户洞察": 90,
        },
    ),
    TeamTemplate(
        team_id="tech_review",
        name="技术审查队",
        description="技术方案、架构评估",
        suitable_scenarios=["技术方案", "架构评审", "重构讨论"],
        recommended_agents=[
            "architect", "product_manager",
            "project_manager", "supervisor",
        ],
        capability_scores={
            "产品价值": 60, "技术可行性": 90, "商业判断": 40,
            "执行落地": 80, "风险审查": 85, "用户洞察": 40,
        },
    ),
    TeamTemplate(
        team_id="personal_creative",
        name="个人创意队",
        description="一个人口述想法，逐步成型",
        suitable_scenarios=["个人思考", "创意发散", "写作"],
        recommended_agents=[
            "product_manager", "business_analyst",
            "supervisor",
        ],
        capability_scores={
            "产品价值": 75, "技术可行性": 30, "商业判断": 70,
            "执行落地": 40, "风险审查": 35, "用户洞察": 85,
        },
    ),
]


def classify_session(
    evidence: EvidencePacket,
    provider: Optional[ProviderAdapter] = None,
) -> str:
    """Classify a session based on transcript content.

    When provider is available, uses LLM for semantic classification.
    Falls back to keyword matching when provider is None.

    Returns:
        Session type name: "探索", "产品", "技术", "个人"
    """
    if provider is not None:
        try:
            return run_async_safely(
                _classify_with_llm(evidence, provider),
                name="classify_session — use classify_session_async() in async context",
            )
        except RuntimeError:
            logger.warning("分类跳过：在事件循环中调用了同步 classify_session")
        except Exception:
            pass  # Fall through to keyword fallback

    return _classify_with_keywords(evidence)


async def classify_session_async(
    evidence: EvidencePacket,
    provider: ProviderAdapter,
) -> str:
    """Async classification — always uses LLM."""
    return await _classify_with_llm(evidence, provider)


# ── LLM-based classification ──

async def _classify_with_llm(
    evidence: EvidencePacket,
    provider: ProviderAdapter,
) -> str:
    """Use LLM to semantically classify the session type."""
    text = " ".join(c.text for c in evidence.transcript_chunks)

    if evidence.mode == "personal_roundtable":
        return "个人"

    # Truncate to ~2000 chars to stay within token budget
    text = text[:2000]

    system = (
        "You are a session classifier. Classify the meeting transcript into "
        "one of these types:\n"
        '- "技术" (technical) — architecture, backend, frontend, protocols, performance\n'
        '- "产品" (product) — user needs, features, UX, design, product strategy\n'
        '- "探索" (exploration) — brainstorming, open-ended discussion, ideation\n'
        '- "个人" (personal) — solo thoughts, creative writing, personal reflection\n\n'
        "Return ONLY a JSON object: {\"type\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}"
    )

    raw = await provider.chat(
        system_prompt=system,
        user_message=f"Classify this transcript:\n\n{text}",
        max_tokens=200,
        temperature=0.1,
    )

    return _parse_classification(raw)


def _parse_classification(raw: str) -> str:
    """Parse LLM classification response. Returns the type string."""
    valid_types = {"技术", "产品", "探索", "个人"}
    cleaned = raw.strip()

    # Try JSON parse
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and data.get("type") in valid_types:
            return data["type"]
    except json.JSONDecodeError:
        pass

    # Try regex extraction
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict) and data.get("type") in valid_types:
                return data["type"]
        except json.JSONDecodeError:
            pass

    # Fallback: keyword match on raw response
    for t in valid_types:
        if t in cleaned:
            return t

    return "探索"


# ── Keyword-based fallback ──

def _classify_with_keywords(evidence: EvidencePacket) -> str:
    """Keyword-based session classification (original algorithm)."""
    text = " ".join(c.text for c in evidence.transcript_chunks)

    if evidence.mode == "personal_roundtable":
        return "个人"

    scores = {"技术": 0, "产品": 0, "探索": 0}
    tech_kw = ["架构", "后端", "前端", "技术", "协议", "并发", "性能"]
    product_kw = ["产品", "用户", "需求", "功能", "体验", "设计"]
    explore_kw = ["探索", "试一下", "不一定", "可能", "也许"]

    for kw in tech_kw:
        if kw in text:
            scores["技术"] += 1
    for kw in product_kw:
        if kw in text:
            scores["产品"] += 1
    for kw in explore_kw:
        if kw in text:
            scores["探索"] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "探索"


def recommend_teams(session_type: str = "", top_n: int = 3, domain_name: str | None = None) -> list[TeamTemplate]:
    """Recommend team templates based on session classification or domain.

    When domain_name is provided, reads agent list from DomainRegistry.
    Falls back to session_type string mapping (backward compatible).

    Returns:
        Top-N matching team templates
    """
    # New path: domain-driven
    if domain_name:
        from roundtable.domain import DomainRegistry
        domain = DomainRegistry.get(domain_name)
        if domain:
            return [TeamTemplate(
                team_id=domain.name,
                name=domain.display,
                description=domain.description,
                suitable_scenarios=[domain.name],
                recommended_agents=domain.agents,
                capability_scores={},
            )]

    # Old path: string mapping (backward compatible)
    type_to_team = {
        "技术": ["tech_review", "broad_opportunity", "product_deep_dive"],
        "产品": ["product_deep_dive", "broad_opportunity", "tech_review"],
        "探索": ["broad_opportunity", "product_deep_dive", "personal_creative"],
        "个人": ["personal_creative", "broad_opportunity"],
    }

    team_lookup = {t.team_id: t for t in BUILTIN_TEAMS}
    preferred = type_to_team.get(session_type, ["broad_opportunity"])
    return [team_lookup[tid] for tid in preferred[:top_n] if tid in team_lookup]


def get_team(team_id: str) -> TeamTemplate | None:
    """Get a team template by ID."""
    for t in BUILTIN_TEAMS:
        if t.team_id == team_id:
            return t
    return None


def list_teams() -> list[str]:
    """Return all team IDs."""
    return [t.team_id for t in BUILTIN_TEAMS]
