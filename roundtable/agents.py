"""Phase 2: Agent base class and built-in expert agents.

Supports both real LLM (via ProviderAdapter) and mock mode for testing.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from roundtable.models import (
    AgentReview, EvidenceClaim, EvidencePacket, ClaimType, SkillManifest,
)
from roundtable.providers import (
    ProviderAdapter, build_agent_prompt, parse_agent_response,
)
from roundtable.skills import load_skill
from roundtable.linker import EvidenceLinker


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

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
        """Synchronous entry point. Delegates to async or mock path."""
        if self.provider is not None:
            return asyncio.run(self.analyze_async(evidence))
        return self._analyze_mock(evidence)

    async def analyze_async(self, evidence: EvidencePacket) -> AgentReview:
        """Async analysis using LLM provider."""
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
        raise NotImplementedError(
            f"Agent '{self.agent_id}' has no mock analysis implementation "
            f"and no ProviderAdapter configured. "
            f"Either inject a ProviderAdapter or override _analyze_mock()."
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
                content="Based on the discussion, prioritize the MVP scope and defer non-critical features to later phases.",
                evidence_ids=[claims[0].claim_id],
                confidence=0.78,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"Identified {len(claims)} product signal(s) from the meeting.",
            claims=claims,
            open_questions=["Are the MVP priorities aligned with user needs?"],
            recommended_next_actions=["Validate MVP scope with stakeholders."],
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
                content="Define the core protocols first, then extend to full 131-skill registry.",
                evidence_ids=[],
                confidence=0.80,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"Found {len(claims)} technical signal(s) and provided architecture recommendations.",
            claims=claims,
            open_questions=["What is the concurrency limit for agent dispatch?"],
            recommended_next_actions=["Lock down TranscriptChunk and EvidenceClaim protocols."],
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
                        content=f"Timeline signal: {c.text[:80]}",
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
                content="Recommend breaking work into 2-week sprints with clear deliverables per phase.",
                evidence_ids=[],
                confidence=0.75,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"Found {len(claims)} execution signal(s) from the discussion.",
            claims=claims,
            open_questions=["What is the team size?", "Are there external dependencies?"],
            recommended_next_actions=["Break work into 2-week sprints.", "Set up weekly review checkpoints."],
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
                content="Target early adopters with highest willingness-to-pay for structured analysis.",
                evidence_ids=[],
                confidence=0.70,
            ))
        return AgentReview(
            agent_id=self.agent_id,
            summary=f"Identified {len(claims)} business signal(s) from the discussion.",
            claims=claims,
            open_questions=["What is the pricing model?", "Competitor response time?"],
            recommended_next_actions=["Run 5 user interviews.", "Validate willingness-to-pay."],
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
