"""Phase 2: Skill Registry — SkillManifest loader and management.

Supports:
- Built-in hardcoded skills (always available, fast startup)
- YAML file-based skills (loaded from skills/ directory at startup)
- Hot-reload (POST /skills/reload)
"""

from __future__ import annotations

from pathlib import Path

from roundtable.models import SkillManifest


# ── Built-in skill definitions (fallback when no YAML files) ──

_BUILTIN_DEFAULTS: dict[str, SkillManifest] = {
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

# ── Active registry (seeded from YAML files + built-in fallback) ──

BUILTIN_SKILLS: dict[str, SkillManifest] = dict(_BUILTIN_DEFAULTS)
"""Active skill registry. Mutated by load_from_directory() and register_skill()."""


# ── Public API ──

def load_skill(skill_id: str) -> SkillManifest:
    """Load a skill manifest by ID.

    Looks up the active registry (BUILTIN_SKILLS), which may include
    YAML-loaded skills if load_from_directory() was called.
    """
    if skill_id not in BUILTIN_SKILLS:
        raise KeyError(
            f"Skill '{skill_id}' not found. Available: {list(BUILTIN_SKILLS)}"
        )
    return BUILTIN_SKILLS[skill_id]


def list_skills() -> list[str]:
    """Return all registered skill IDs."""
    return list(BUILTIN_SKILLS)


def register_skill(manifest: SkillManifest) -> None:
    """Register a new skill (in-memory, for this session only)."""
    BUILTIN_SKILLS[manifest.skill_id] = manifest


# ── YAML file loading (plugin system) ──

def load_from_directory(dir_path: str | Path | None = None) -> int:
    """Load skill YAML files from a directory into BUILTIN_SKILLS.

    Scans for *.yaml and *.yml files, parses them, and registers each
    as a SkillManifest. Existing entries with the same skill_id are
    overwritten.

    Args:
        dir_path: Path to skills directory (default: <project_root>/skills/)

    Returns:
        Number of skills loaded
    """
    if dir_path is None:
        # Default: skills/ directory next to this file's package
        dir_path = Path(__file__).resolve().parent.parent / "skills"

    skill_dir = Path(dir_path)
    if not skill_dir.is_dir():
        return 0

    count = 0
    for yaml_path in sorted(skill_dir.glob("*.yaml")):
        manifest = _parse_yaml_skill(yaml_path)
        if manifest:
            BUILTIN_SKILLS[manifest.skill_id] = manifest
            count += 1

    for yml_path in sorted(skill_dir.glob("*.yml")):
        manifest = _parse_yaml_skill(yml_path)
        if manifest:
            BUILTIN_SKILLS[manifest.skill_id] = manifest
            count += 1

    return count


def reload_skills(dir_path: str | Path | None = None) -> dict:
    """Full reload: reset to built-in defaults, then re-scan YAML directory.

    Returns:
        {"loaded": <count>, "skill_ids": [...]}
    """
    global BUILTIN_SKILLS
    BUILTIN_SKILLS = dict(_BUILTIN_DEFAULTS)
    count = load_from_directory(dir_path)
    return {
        "loaded": count,
        "total": len(BUILTIN_SKILLS),
        "skill_ids": list(BUILTIN_SKILLS),
    }


# ── Minimal YAML parser (no external dependency) ──

def _parse_yaml_skill(path: Path) -> SkillManifest | None:
    """Parse a simple YAML skill definition file.

    Supports the subset of YAML used by roundtable skill files:
    scalar key: value, and list items with `- item`.
    No nested objects, no anchors, no multi-line strings.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    data: dict = {}
    current_key: str | None = None
    current_list: list[str] = []

    for line in raw.splitlines():
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            continue

        # List item: `  - value`
        if stripped.startswith("- ") and current_key is not None:
            current_list.append(stripped[2:].strip())
            continue

        # Key: value pair (flush previous list if any)
        if ":" in stripped and not stripped.startswith("-"):
            # Flush pending list
            if current_key is not None and current_list:
                data[current_key] = current_list
                current_list = []

            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()

            if value:
                data[key] = value
            else:
                # Value is on next lines (list)
                current_key = key
                current_list = []

    # Flush final list
    if current_key is not None and current_list:
        data[current_key] = current_list

    # Validate required fields
    if "skill_id" not in data or "name" not in data:
        return None

    # Parse list-type fields (comma-separated string or YAML list)
    def _parse_list(raw_value) -> list[str]:
        if isinstance(raw_value, list):
            return raw_value
        if isinstance(raw_value, str) and raw_value:
            return [x.strip() for x in raw_value.split(",") if x.strip()]
        return []

    return SkillManifest(
        skill_id=str(data.get("skill_id", "")),
        name=str(data.get("name", "")),
        version=str(data.get("version", "1.0.0")),
        role=str(data.get("role", "")),
        allowed_claim_types=_parse_list(data.get("allowed_claim_types", [])),
        allowed_domains=_parse_list(data.get("allowed_domains", [])),
        forbidden=_parse_list(data.get("forbidden", [])),
    )
