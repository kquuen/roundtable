"""Phase 2: Agent base class and built-in expert agents.

Supports both real LLM (via ProviderAdapter) and mock mode for testing.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from roundtable.models import (
    AgentReview, EvidenceClaim, EvidencePacket, ClaimType, SkillManifest,
    ClaimLifecycle,
)
from roundtable.providers import (
    ProviderAdapter, build_agent_prompt, build_debate_prompt,
    parse_agent_response,
)
from roundtable.skills import load_skill
from roundtable.linker import EvidenceLinker
from roundtable.utils import run_async_safely


class Agent:
    """Base agent class.

    When a ProviderAdapter is injected, uses real LLM for analysis.
    When provider is None, falls back to mock keyword-based analysis.
    """

    def __init__(self, skill_id: str, provider: ProviderAdapter | None = None):
        self.skill: SkillManifest = load_skill(skill_id)
        self.provider = provider

    @property
    def agent_id(self) -> str:
        return self.skill.skill_id

    def analyze(
        self,
        evidence: EvidencePacket,
        peer_reviews: list[AgentReview] | None = None,
    ) -> AgentReview:
        """Synchronous entry point. Delegates to async or mock path."""
        if self.provider is not None:
            return run_async_safely(
                self.analyze_async(evidence, peer_reviews=peer_reviews),
                name=f"Agent.analyze({self.agent_id}) — use analyze_async() instead",
            )
        if peer_reviews:
            return self._analyze_debate_mock(evidence, peer_reviews)
        return self._analyze_mock(evidence)

    async def analyze_async(
        self,
        evidence: EvidencePacket,
        peer_reviews: list[AgentReview] | None = None,
    ) -> AgentReview:
        """Async analysis using LLM provider."""
        if peer_reviews:
            system_prompt, user_message = build_debate_prompt(
                self.skill, evidence, peer_reviews,
            )
        else:
            system_prompt, user_message = build_agent_prompt(self.skill, evidence)

        raw = await self.provider.chat(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=2000,
        )

        parsed, error = parse_agent_response(raw, self.agent_id, evidence)
        if error:
            return AgentReview(
                agent_id=self.agent_id,
                summary=f"LLM 响应解析失败：{error}",
                claims=[],
                open_questions=["LLM 输出格式异常，请重试"],
                recommended_next_actions=["检查 API 响应格式"],
            )

        # Preserve raw claims data for debate position lookup (Bug 5)
        self._last_raw_claims = parsed.get("claims", [])

        # Resolve evidence_text → chunk_id via semantic linker
        evidence_map = await self._link_evidence(parsed, evidence)

        return self._dict_to_review(parsed, evidence, evidence_map)

    async def _link_evidence(
        self, parsed: dict, evidence: EvidencePacket
    ) -> dict[int, list[str]]:
        """Resolve evidence_text references to chunk_ids using EvidenceLinker.

        Collects all evidence_text strings from parsed claims,
        runs semantic matching, and returns claim_index → chunk_ids map.
        """
        claims_data = parsed.get("claims", [])
        evidence_texts: list[str] = []
        text_indices: list[int] = []

        for i, c in enumerate(claims_data):
            et = c.get("evidence_text", "")
            if et and et.strip():
                evidence_texts.append(et.strip())
                text_indices.append(i)

        if not evidence_texts:
            return {}

        linker = EvidenceLinker(provider=self.provider)
        chunks = evidence.transcript_chunks

        # Use async link (LLM) when provider available, sync fallback otherwise
        if self.provider is not None:
            results = await linker.link(evidence_texts, list(chunks))
        else:
            results = linker.link_sync(evidence_texts, list(chunks))

        # Build claim_index → chunk_ids map
        evidence_map: dict[int, list[str]] = {}
        for j, idx in enumerate(text_indices):
            if j < len(results):
                evidence_map[idx] = results[j]

        return evidence_map

    def _dict_to_review(
        self,
        data: dict,
        evidence: EvidencePacket,
        evidence_map: dict[int, list[str]] | None = None,
    ) -> AgentReview:
        """Convert parsed JSON dict to AgentReview with proper types.

        Uses pre-computed evidence_map (from semantic linker) when available;
        falls back to naive substring matching otherwise.
        """
        if evidence_map is None:
            evidence_map = {}

        chunk_text_map = {c.chunk_id: c.text for c in evidence.transcript_chunks}

        claims = []
        for i, c in enumerate(data.get("claims", [])):
            # Determine evidence_ids: prefer linker result, fall back to substring
            evidence_ids = evidence_map.get(i, [])
            if not evidence_ids:
                evidence_text = c.get("evidence_text", "")
                if evidence_text:
                    # Fallback: naive substring matching (for mock path)
                    for cid, ctext in chunk_text_map.items():
                        if evidence_text[:30] in ctext or ctext[:30] in evidence_text:
                            evidence_ids.append(cid)
                            break

            # Map claim_type
            raw_type = c.get("claim_type", "inference")
            try:
                claim_type = ClaimType(raw_type)
            except ValueError:
                claim_type = ClaimType.INFERENCE

            claim = EvidenceClaim(
                claim_id=f"c_{self.agent_id}_{i:03d}",
                agent_id=self.agent_id,
                claim_type=claim_type,
                content=c.get("content", ""),
                evidence_ids=evidence_ids,
                confidence=float(c.get("confidence", 0.5)),
                lifecycle=ClaimLifecycle.DRAFT,
            )
            claims.append(claim)

        return AgentReview(
            agent_id=self.agent_id,
            summary=data.get("summary", f"{self.skill.name} 分析完成"),
            claims=claims,
            open_questions=data.get("open_questions", []),
            recommended_next_actions=data.get("recommended_next_actions", []),
        )

    # ── Mock analysis (keyword-based, no LLM required) ──

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        """Keyword-based mock analysis. Used when no provider is configured.

        Subclasses may override this for role-specific keyword logic.
        Default: generic keyword detection.
        """
        return self._analyze_debate_mock(evidence, [])

    def _analyze_debate_mock(
        self, evidence: EvidencePacket, peer_reviews: list,
    ) -> AgentReview:
        """模板化 Mock 辩论 — 不依赖 LLM，只需结构完整。

        每个 Agent 看到 peer 的 claim → 按角色模板生成 agree/disagree/extend。
        保证数据流和状态转换与真实路径一致，测试覆盖不归零。
        """
        claims = []
        skill_name = self.skill.name

        for pr in peer_reviews:
            for i, claim in enumerate(pr.claims[:2]):  # 只看每个 peer 的前 2 个 claim
                # 按角色模板生成不同立场
                if i % 3 == 0:
                    position = "agree"
                    template = f"[Mock辩论] {skill_name} 同意 {pr.agent_id} 的观点：{claim.content[:60]}"
                elif i % 3 == 1:
                    position = "disagree"
                    template = f"[Mock辩论] {skill_name} 对 {pr.agent_id} 的 {claim.claim_id} 持不同意见：{claim.content[:60]}"
                else:
                    position = "extend"
                    template = f"[Mock辩论] {skill_name} 延伸 {pr.agent_id} 的观点：{claim.content[:60]}"

                claims.append(EvidenceClaim(
                    claim_id=f"c_{self.agent_id}_debate_{len(claims):03d}",
                    agent_id=self.agent_id,
                    claim_type=ClaimType.INFERENCE,
                    content=template,
                    evidence_ids=claim.evidence_ids[:1],
                    confidence=0.6,
                    lifecycle=ClaimLifecycle.DRAFT,
                ))

        return AgentReview(
            agent_id=self.agent_id,
            summary=f"[Mock] {skill_name} 完成辩论轮，回应了 {len(peer_reviews)} 位专家",
            claims=claims,
        )


# ── Built-in agents ──


class ProductManager(Agent):
    """Product strategy analysis agent."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("product_manager", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        chunks = evidence.transcript_chunks
        claims = []
        # Bilingual keywords: Chinese + English
        decision_keywords = [
            "决定", "确定", "先做", "不做", "只做", "改为", "优先",
            "decide", "priority", "mvp", "scope", "defer", "only", "first",
        ]
        for c in chunks:
            for kw in decision_keywords:
                if kw in c.text.lower():
                    claims.append(EvidenceClaim(
                        claim_id=f"c_pm_{len(claims):03d}",
                        agent_id=self.agent_id,
                        claim_type=ClaimType.FACT,
                        content=f"Product decision detected: {c.text[:80]}",
                        evidence_ids=[c.chunk_id],
                        confidence=0.90,
                    ))
                    break
        # Fallback: always extract at least one signal from input
        if not claims and chunks:
            topics = [c.text[:50] for c in chunks[:3]]
            claims.append(EvidenceClaim(
                claim_id="c_pm_000",
                agent_id=self.agent_id,
                claim_type=ClaimType.INFERENCE,
                content=f"Discussion topics identified: {'; '.join(topics)}",
                evidence_ids=[chunks[0].chunk_id],
                confidence=0.65,
            ))
        if claims:
            claims.append(EvidenceClaim(
                claim_id=f"c_pm_{len(claims):03d}",
                agent_id=self.agent_id,
                claim_type=ClaimType.RECOMMENDATION,
                content="根据讨论内容，建议优先聚焦 MVP 核心范围，将非关键特性推迟到后续阶段。",
                evidence_ids=[],
                confidence=0.78,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"从会议中识别出 {len(claims)} 个产品信号。",
            claims=claims,
            open_questions=["MVP 优先级是否与用户需求对齐？"],
            recommended_next_actions=["与利益相关方确认 MVP 范围。"],
        )


class Architect(Agent):
    """Technical architecture analysis agent."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("architect", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        chunks = evidence.transcript_chunks
        claims = []
        tech_keywords = [
            "协议", "后端", "前端", "数据库", "API", "并发", "成本", "token", "Agent",
            "protocol", "backend", "frontend", "database", "concurrency", "cost", "architecture",
        ]
        for c in chunks:
            for kw in tech_keywords:
                if kw in c.text.lower():
                    claims.append(EvidenceClaim(
                        claim_id=f"c_arch_{len(claims):03d}",
                        agent_id=self.agent_id,
                        claim_type=ClaimType.INFERENCE if kw.lower() == "agent" else ClaimType.FACT,
                        content=f"Technical concern: {kw} - {c.text[:60]}",
                        evidence_ids=[c.chunk_id],
                        confidence=0.85,
                    ))
                    break
        # Fallback: always extract at least one signal
        if not claims and chunks:
            claims.append(EvidenceClaim(
                claim_id="c_arch_000",
                agent_id=self.agent_id,
                claim_type=ClaimType.INFERENCE,
                content=f"Technical context from input: {chunks[0].text[:80]}",
                evidence_ids=[chunks[0].chunk_id],
                confidence=0.60,
            ))
        if claims:
            claims.append(EvidenceClaim(
                claim_id=f"c_arch_{len(claims):03d}",
                agent_id=self.agent_id,
                claim_type=ClaimType.RECOMMENDATION,
                content="建议先锁定核心协议定义，再逐步扩展到完整的技能注册体系。",
                evidence_ids=[],
                confidence=0.80,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"发现 {len(claims)} 个技术信号，已给出架构建议。",
            claims=claims,
            open_questions=["Agent 并发调度的上限是多少？"],
            recommended_next_actions=["锁定 TranscriptChunk 和 EvidenceClaim 协议。"],
        )


class ProjectManager(Agent):
    """Project execution planning agent."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("project_manager", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        chunks = evidence.transcript_chunks
        claims = []
        # Detect timeline/scheduling signals
        timeline_kw = [
            "周", "天", "月", "排期", "交付", "上线", "发布", "里程碑",
            "week", "sprint", "phase", "deliver", "milestone", "timeline", "launch",
        ]
        for c in chunks:
            for kw in timeline_kw:
                if kw in c.text.lower():
                    claims.append(EvidenceClaim(
                        claim_id=f"c_pjm_{len(claims):03d}",
                        agent_id=self.agent_id,
                        claim_type=ClaimType.FACT,
                        content=f"时间线信号：{c.text[:80]}",
                        evidence_ids=[c.chunk_id],
                        confidence=0.80,
                    ))
                    break
        # Always add execution recommendation based on input
        if chunks:
            claims.append(EvidenceClaim(
                claim_id=f"c_pjm_{len(claims):03d}",
                agent_id=self.agent_id,
                claim_type=ClaimType.RECOMMENDATION,
                content="建议将工作拆分为 2 周冲刺，每个阶段有明确交付物。",
                evidence_ids=[],
                confidence=0.75,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"从讨论中发现 {len(claims)} 个执行规划信号。",
            claims=claims,
            open_questions=["团队规模是多少？", "是否有外部依赖？"],
            recommended_next_actions=["将工作拆分为 2 周冲刺。", "设置每周审查检查点。"],
        )


class BusinessAnalyst(Agent):
    """Business analysis agent."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("business_analyst", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        chunks = evidence.transcript_chunks
        claims = []
        biz_kw = [
            "用户", "市场", "客户", "收入", "付费", "增长", "竞品", "差异化",
            "user", "market", "customer", "revenue", "pricing", "growth", "competitor", "differentiat",
        ]
        for c in chunks:
            for kw in biz_kw:
                if kw in c.text.lower():
                    claims.append(EvidenceClaim(
                        claim_id=f"c_ba_{len(claims):03d}",
                        agent_id=self.agent_id,
                        claim_type=ClaimType.INFERENCE,
                        content=f"Business signal ({kw}): {c.text[:80]}",
                        evidence_ids=[c.chunk_id],
                        confidence=0.72,
                    ))
                    break
        if claims:
            claims.append(EvidenceClaim(
                claim_id=f"c_ba_{len(claims):03d}",
                agent_id=self.agent_id,
                claim_type=ClaimType.RECOMMENDATION,
                content="建议瞄准付费意愿最高的早期用户群体，提供结构化分析服务。",
                evidence_ids=[],
                confidence=0.70,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"从讨论中识别出 {len(claims)} 个商业信号。",
            claims=claims,
            open_questions=["定价模型是什么？", "竞品响应周期有多长？"],
            recommended_next_actions=["进行 5 次用户访谈。", "验证付费意愿。"],
        )


class SupervisorAgent(Agent):
    """Fact-checking supervisor agent (not the final Supervisor module)."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("supervisor", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        return AgentReview(
            agent_id=self.agent_id,
            summary="Supervisor agent confirms: this POC run is valid. All claims should go through the review module.",
            claims=[],
            open_questions=[],
            recommended_next_actions=["Run claims through supervisor.py review module."],
        )
