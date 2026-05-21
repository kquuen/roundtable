"""Phase 2: Skill Registry — SkillManifest loader and management."""

from __future__ import annotations

from roundtable.models import SkillManifest

# ── Built-in skill definitions ──

BUILTIN_SKILLS: dict[str, SkillManifest] = {
    "product_manager": SkillManifest(
        skill_id="product_manager",
        name="产品经理",
        version="1.0.0",
        role="product_analysis",
        allowed_claim_types=["inference", "recommendation", "extension"],
        allowed_domains=["product_strategy", "user_value", "prioritization", "market_fit"],
        forbidden=[
            "编造会议中未出现的承诺",
            "把建议写成事实",
            "在没有证据时判断团队真实意图",
            "断言技术架构的可行性",
        ],
    ),
    "architect": SkillManifest(
        skill_id="architect",
        name="架构师",
        version="1.0.0",
        role="technical_review",
        allowed_claim_types=["inference", "recommendation"],
        allowed_domains=["architecture", "coupling", "scalability", "security", "delivery"],
        forbidden=[
            "编造会议中未出现的承诺",
            "把建议写成事实",
            "在没有证据时判断团队真实意图",
            "对产品策略做判断",
        ],
    ),
    "project_manager": SkillManifest(
        skill_id="project_manager",
        name="项目经理",
        version="1.0.0",
        role="execution_planning",
        allowed_claim_types=["inference", "recommendation"],
        allowed_domains=["timeline", "resource", "risk", "dependency", "milestone"],
        forbidden=[
            "断言未确认的交付日期",
            "评估技术难度",
        ],
    ),
    "business_analyst": SkillManifest(
        skill_id="business_analyst",
        name="商业分析",
        version="1.0.0",
        role="business_analysis",
        allowed_claim_types=["inference", "recommendation", "extension"],
        allowed_domains=["market", "revenue", "competition", "business_model"],
        forbidden=[
            "在没有数据时断言市场规模",
            "把推测写成确定的市场结论",
        ],
    ),
    "supervisor": SkillManifest(
        skill_id="supervisor",
        name="主审查官",
        version="1.0.0",
        role="fact_checking",
        allowed_claim_types=["inference"],
        allowed_domains=["fact_verification", "evidence_checking", "claim_classification"],
        forbidden=[
            "提出新的专家建议",
            "越界进行产品策略判断",
        ],
    ),
}


def load_skill(skill_id: str) -> SkillManifest:
    """Load a skill manifest by ID.

    Currently loads from built-in registry.
    Future: load from YAML files or remote registry.
    """
    if skill_id not in BUILTIN_SKILLS:
        raise KeyError(f"Skill '{skill_id}' not found. Available: {list(BUILTIN_SKILLS)}")
    return BUILTIN_SKILLS[skill_id]


def list_skills() -> list[str]:
    """Return all registered skill IDs."""
    return list(BUILTIN_SKILLS)


def register_skill(manifest: SkillManifest) -> None:
    """Register a new skill (in-memory, for this session only)."""
    BUILTIN_SKILLS[manifest.skill_id] = manifest
