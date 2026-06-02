"""Phase 1: Agent Registry V2 — Dynamic matching with Jaccard similarity.

Replaces hardcoded team templates with:
- config/agents/registry.json  →  AgentManifest definitions
- config/agents/profiles/*.md  →  Personality files
- Jaccard + methodology bonus  →  Dynamic agent matching
- User confirmation             →  Adjustable group composition
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from roundtable.models import AgentManifest, AgentMatchResult, AgentGroup, GroupRecommendation

logger = logging.getLogger(__name__)

# ── Constants ──

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "agents" / "registry.json"
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent.parent / "config" / "agents" / "profiles"

# 中文停用词（简化版）
_STOPWORDS = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "那", "个",
    "为", "之", "与", "及", "等", "从", "将", "把", "被", "让",
    "给", "向", "往", "于", "而", "却", "但是", "因为", "所以", "如果",
    "可以", "需要", "进行", "通过", "使用", "基于", "针对", "关于",
    "一下", "一些", "一种", "一个", "这个", "那个", "什么", "怎么",
    "帮助", "想", "做", "用", "来", "想", "觉得", "认为", "感觉",
}

_MIN_MATCH_SCORE = 0.15  # 最低匹配分（宁缺毋滥门槛）
_MAX_GROUP_SIZE = 5      # 每组最多 Agent 数
_SUPERVISOR_ID = "supervisor"


# ── Registry Loader ──

class AgentRegistryV2:
    """Loads and caches agent definitions from registry.json."""

    def __init__(self, registry_path: Path | str | None = None):
        self._path = Path(registry_path) if registry_path else DEFAULT_REGISTRY_PATH
        self._agents: dict[str, AgentManifest] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning("Agent registry not found: %s", self._path)
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for entry in data.get("agents", []):
                try:
                    agent = AgentManifest.model_validate(entry)
                    self._agents[agent.id] = agent
                except Exception as e:
                    logger.warning("Invalid agent entry %r: %s", entry.get("id"), e)
            logger.info("Loaded %d agents from registry", len(self._agents))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load agent registry: %s", e)

    def get(self, agent_id: str) -> Optional[AgentManifest]:
        return self._agents.get(agent_id)

    def list_all(self) -> list[AgentManifest]:
        return list(self._agents.values())

    def list_active(self) -> list[AgentManifest]:
        return [a for a in self._agents.values() if a.is_active]

    def reload(self) -> int:
        self._agents.clear()
        self._load()
        return len(self._agents)


# ── Keyword Extraction ──

def _tokenize(text: str) -> list[str]:
    """Extract meaningful tokens from Chinese/English mixed text."""
    text = text.lower().strip()
    # English words
    english_tokens = re.findall(r"[a-z0-9]+(?:_[a-z0-9]+)*", text)
    # Chinese characters (2-4 char phrases via sliding window)
    chinese_chars = re.findall(r"[\u4e00-\u9fff]+", text)
    chinese_tokens: list[str] = []
    for phrase in chinese_chars:
        # 2-char and 3-char substrings
        for length in (3, 2):
            for i in range(len(phrase) - length + 1):
                sub = phrase[i:i + length]
                if sub not in _STOPWORDS:
                    chinese_tokens.append(sub)
        # Also keep 4-char phrases if they look like terms
        for i in range(len(phrase) - 3):
            sub = phrase[i:i + 4]
            chinese_tokens.append(sub)
    tokens = english_tokens + chinese_tokens
    # Deduplicate while preserving order
    seen = set()
    result: list[str] = []
    for t in tokens:
        if t not in seen and len(t) >= 2:
            seen.add(t)
            result.append(t)
    return result


def extract_keywords(text: str) -> list[str]:
    """Extract unique, meaningful keywords from user input."""
    return _tokenize(text)


# ── Similarity Scoring ──

def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity: |A ∩ B| / |A ∪ B|."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _methodology_bonus(user_tokens: set[str], agent: AgentManifest) -> float:
    """Award small bonus if user input mentions agent's methodology keywords."""
    if not agent.methodology:
        return 0.0
    method_tokens = _tokenize(agent.methodology)
    method_set = set(method_tokens)
    if not method_set:
        return 0.0
    matches = len(user_tokens & method_set)
    # Bonus: 0.02 per matched methodology term, max 0.1
    return min(0.1, matches * 0.02)


def match_agent(user_tokens: set[str], agent: AgentManifest) -> AgentMatchResult:
    """Score a single agent against user keywords."""
    agent_kw_set = set(k.lower() for k in agent.keywords)
    score = jaccard_similarity(user_tokens, agent_kw_set)
    matched = sorted(user_tokens & agent_kw_set)
    bonus = _methodology_bonus(user_tokens, agent)
    final = min(1.0, score + bonus)

    reason_parts: list[str] = []
    if matched:
        reason_parts.append(f"匹配关键词: {', '.join(matched)}")
    if bonus > 0:
        reason_parts.append(f"方法论加分 +{bonus:.2f}")
    if final >= 0.5:
        reason_parts.append("高度相关")
    elif final >= 0.3:
        reason_parts.append("中度相关")
    elif final >= _MIN_MATCH_SCORE:
        reason_parts.append("轻度相关")
    else:
        reason_parts.append("相关性不足")

    return AgentMatchResult(
        agent=agent,
        match_score=round(score, 4),
        matched_keywords=matched,
        methodology_bonus=round(bonus, 4),
        final_score=round(final, 4),
        reason="；".join(reason_parts),
    )


# ── Grouping Logic ──

def _cluster_by_domains(agents: list[AgentManifest]) -> list[list[AgentManifest]]:
    """Cluster agents by domain overlap using greedy merge."""
    if not agents:
        return []
    if len(agents) <= _MAX_GROUP_SIZE:
        return [agents]

    # Build domain overlap graph
    clusters: list[list[AgentManifest]] = []
    remaining = list(agents)

    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        seed_domains = set(seed.domains)

        i = 0
        while i < len(remaining) and len(cluster) < _MAX_GROUP_SIZE:
            candidate = remaining[i]
            cand_domains = set(candidate.domains)
            overlap = len(seed_domains & cand_domains)
            if overlap > 0 or len(seed_domains & cand_domains) / max(len(seed_domains | cand_domains), 1) > 0.2:
                cluster.append(candidate)
                remaining.pop(i)
            else:
                i += 1
        clusters.append(cluster)

    return clusters


def build_groups(matched: list[AgentMatchResult]) -> list[AgentGroup]:
    """Assign matched agents into debate groups.

    Rules:
    - Supervisor is added to every group automatically
    - If total <= 5 agents: single group
    - If > 5: cluster by domain overlap
    """
    if not matched:
        return []

    agents = [m.agent for m in matched]
    supervisor = next((a for a in agents if a.id == _SUPERVISOR_ID), None)
    non_supervisor = [a for a in agents if a.id != _SUPERVISOR_ID]

    if len(non_supervisor) <= _MAX_GROUP_SIZE:
        group_agents = list(non_supervisor)
        if supervisor and supervisor not in group_agents:
            group_agents.append(supervisor)
        top_topic = non_supervisor[0].domains[0] if non_supervisor else "综合分析"
        return [
            AgentGroup(
                group_id="g_001",
                group_name=f"{top_topic}综合评估组",
                topic=top_topic,
                agents=group_agents,
                rationale="匹配到的 Agent 数量较少，合并为一组进行综合评估。",
            )
        ]

    clusters = _cluster_by_domains(non_supervisor)
    groups: list[AgentGroup] = []
    for idx, cluster in enumerate(clusters, 1):
        group_agents = list(cluster)
        if supervisor and supervisor not in group_agents:
            group_agents.append(supervisor)
        top_domain = cluster[0].domains[0] if cluster[0].domains else "综合"
        groups.append(
            AgentGroup(
                group_id=f"g_{idx:03d}",
                group_name=f"{top_domain}专项评估组",
                topic=top_domain,
                agents=group_agents,
                rationale=f"基于领域重叠度聚类：{', '.join(a.name for a in cluster)}",
            )
        )
    return groups


# ── Main Matcher ──

class AgentMatcher:
    """Orchestrates keyword extraction → matching → grouping → recommendation."""

    def __init__(self, registry: AgentRegistryV2 | None = None):
        self.registry = registry or AgentRegistryV2()

    def match(
        self,
        input_text: str,
        session_id: str = "",
        min_score: float = _MIN_MATCH_SCORE,
        max_agents: int = 10,
    ) -> GroupRecommendation:
        """Full matching pipeline.

        Args:
            input_text: User idea, transcript, or query
            session_id: Optional session identifier
            min_score: Minimum final_score to include (宁缺毋滥)
            max_agents: Maximum number of agents to return
        """
        keywords = extract_keywords(input_text)
        user_tokens = set(keywords)

        active = self.registry.list_active()
        results: list[AgentMatchResult] = []

        for agent in active:
            result = match_agent(user_tokens, agent)
            if result.final_score >= min_score:
                results.append(result)

        # Sort by final_score desc
        results.sort(key=lambda r: r.final_score, reverse=True)
        top_results = results[:max_agents]

        # Build groups
        groups = build_groups(top_results)

        ungrouped: list[str] = []
        if len(results) > max_agents:
            ungrouped = [r.agent.name for r in results[max_agents:]]

        return GroupRecommendation(
            session_id=session_id,
            input_text=input_text,
            extracted_keywords=keywords,
            matched_agents=top_results,
            groups=groups,
            ungrouped_reason=(
                f"以下 Agent 匹配度较低未入选: {', '.join(ungrouped)}"
                if ungrouped else ""
            ),
        )

    def reload(self) -> int:
        """Reload registry from disk."""
        return self.registry.reload()


# ── Convenience singleton ──

_matcher: AgentMatcher | None = None


def get_matcher() -> AgentMatcher:
    """Get or lazily initialize the global agent matcher."""
    global _matcher
    if _matcher is None:
        _matcher = AgentMatcher()
    return _matcher
