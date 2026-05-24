"""Phase 7C: 领域适配 — 根据输入内容自动选择 Agent 组合。

domains.yaml 驱动，不需改代码即可新增领域。
classify_session() 保留不动（向后兼容），classify_domain() 返回完整配置。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from roundtable.models import DomainConfig, EvidencePacket

logger = logging.getLogger("roundtable.domain")

_DOMAINS_YAML = Path(__file__).resolve().parent.parent / "skills" / "domains.yaml"


class DomainRegistry:
    """从 YAML 加载领域定义，支持热加载和关键词分类。"""

    _configs: list[DomainConfig] = []
    _loaded = False

    @classmethod
    def load(cls) -> list[DomainConfig]:
        """加载 domains.yaml，缓存结果。"""
        if cls._loaded and cls._configs:
            return cls._configs

        if not _DOMAINS_YAML.exists():
            logger.warning("domains.yaml not found at %s, using built-in fallback", _DOMAINS_YAML)
            cls._configs = cls._fallback_configs()
            cls._loaded = True
            return cls._configs

        try:
            data = yaml.safe_load(_DOMAINS_YAML.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to load domains.yaml: %s, using fallback", e)
            cls._configs = cls._fallback_configs()
            cls._loaded = True
            return cls._configs

        configs = []
        for d in data.get("domains", []):
            configs.append(DomainConfig(
                name=d.get("name", ""),
                display=d.get("display", ""),
                description=d.get("description", ""),
                keywords=d.get("keywords", []),
                agents=d.get("agents", []),
                agent_count=d.get("agent_count", 5),
                prompt_modifier=d.get("prompt_modifier", ""),
                forbidden_overrides=d.get("forbidden_overrides", {}),
            ))

        cls._configs = configs
        cls._loaded = True
        logger.info("Loaded %d domains from domains.yaml", len(configs))
        return configs

    @classmethod
    def list_all(cls) -> list[DomainConfig]:
        return cls.load()

    @classmethod
    def get(cls, name: str) -> DomainConfig | None:
        for d in cls.load():
            if d.name == name:
                return d
        return None

    @classmethod
    def reload(cls) -> list[DomainConfig]:
        """强制重新加载 YAML。"""
        cls._loaded = False
        return cls.load()

    @classmethod
    def _fallback_configs(cls) -> list[DomainConfig]:
        """内置 fallback — YAML 不可用时使用。"""
        return [
            DomainConfig(
                name="personal_roundtable", display="个人圆桌",
                description="单人思考",
                keywords=["个人", "思考", "想法", "我"],
                agents=["product_manager", "architect", "business_analyst"],
                agent_count=3,
            ),
            DomainConfig(
                name="team_meeting", display="团队会议",
                description="多人协作",
                keywords=["会议", "团队", "项目", "需求"],
                agents=["product_manager", "architect", "project_manager",
                       "business_analyst", "quality_assurance"],
                agent_count=5,
            ),
            DomainConfig(
                name="tech_review", display="技术评审",
                description="技术评审",
                keywords=["架构", "代码", "性能", "数据库", "API"],
                agents=["architect", "quality_assurance", "security_reviewer"],
                agent_count=3,
            ),
            DomainConfig(
                name="product_brainstorm", display="产品头脑风暴",
                description="产品方向讨论",
                keywords=["产品", "用户", "体验", "功能", "竞品"],
                agents=["product_manager", "business_analyst", "architect"],
                agent_count=3,
            ),
        ]


async def classify_domain(
    evidence: EvidencePacket,
    provider=None,
) -> DomainConfig:
    """根据输入内容分类到最匹配的领域。

    关键词匹配驱动（从 YAML 配置读取关键词列表）。
    fallback → personal_roundtable。

    与 classify_session() 独立——后者返回 string 保留向后兼容。
    """
    text = " ".join(c.text for c in evidence.transcript_chunks).lower()

    # 个人圆桌特殊判断
    if evidence.mode == "personal_roundtable":
        return DomainRegistry.get("personal_roundtable") or DomainRegistry.load()[0]

    # LLM 分类（provider 可用时）
    if provider is not None:
        try:
            return await _classify_with_llm(text, provider)
        except Exception:
            logger.warning("LLM domain classification failed, falling back to keywords")

    # 关键词匹配
    return _classify_with_keywords(text)


async def _classify_with_llm(text: str, provider) -> DomainConfig:
    """LLM 语义分类。"""
    domains = DomainRegistry.load()
    domain_list = "\n".join(
        f'- "{d.name}" ({d.display}): {d.description}'
        for d in domains
    )

    system = (
        "You are a domain classifier. Classify the input into one of these domains:\n"
        f"{domain_list}\n\n"
        "Return ONLY a JSON object: {\"domain\": \"...\", \"confidence\": 0.0-1.0}"
    )

    raw = await provider.chat(
        system_prompt=system,
        user_message=f"Classify:\n\n{text[:2000]}",
        max_tokens=200,
        temperature=0.1,
    )

    import json
    try:
        result = json.loads(raw.strip())
        domain_name = result.get("domain", "personal_roundtable")
    except json.JSONDecodeError:
        domain_name = "personal_roundtable"

    domain = DomainRegistry.get(domain_name)
    return domain if domain else DomainRegistry.get("personal_roundtable")


def _classify_with_keywords(text: str) -> DomainConfig:
    """关键词匹配分类——从 YAML 配置读取关键词，而非硬编码。"""
    domains = DomainRegistry.load()
    best: DomainConfig | None = None
    best_score = 0

    for domain in domains:
        score = sum(1 for kw in domain.keywords if kw in text)
        if score > best_score:
            best_score = score
            best = domain

    # fallback → personal_roundtable
    if best is None or best_score == 0:
        best = DomainRegistry.get("personal_roundtable")

    return best or domains[0]
