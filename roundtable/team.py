"""Phase 4: Team Builder — session classification and team recommendation."""

from __future__ import annotations

from roundtable.models import EvidencePacket, TeamTemplate


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


def classify_session(evidence: EvidencePacket) -> str:
    """Classify a session based on transcript content keywords.

    Returns:
        Session type name: "探索", "产品", "技术", "个人"
    """
    text = " ".join(c.text for c in evidence.transcript_chunks)
    text_lower = text.lower()

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


def recommend_teams(session_type: str, top_n: int = 3) -> list[TeamTemplate]:
    """Recommend team templates based on session classification.

    Returns:
        Top-N matching team templates
    """
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
