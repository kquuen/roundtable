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

        return self._dict_to_review(parsed, evidence)

    def _dict_to_review(
        self, data: dict, evidence: EvidencePacket
    ) -> AgentReview:
        """Convert parsed JSON dict to AgentReview with proper types."""
        valid_chunk_ids = {c.chunk_id for c in evidence.transcript_chunks}
        chunk_text_map = {c.chunk_id: c.text for c in evidence.transcript_chunks}

        claims = []
        for i, c in enumerate(data.get("claims", [])):
            # Determine evidence_ids
            evidence_ids = []
            evidence_text = c.get("evidence_text", "")
            if evidence_text:
                # Try to find which chunk contains this evidence text
                for cid, ctext in chunk_text_map.items():
                    if evidence_text[:30] in ctext or ctext[:30] in evidence_text:
                        evidence_ids.append(cid)
                        break
                # If no match found, still store the text for later review
                if not evidence_ids and evidence_text.strip():
                    evidence_ids = []  # supervisor will handle missing evidence

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
        decision_keywords = ["决定", "确定", "先做", "不做", "只做", "改为"]
        for c in chunks:
            for kw in decision_keywords:
                if kw in c.text:
                    claims.append(EvidenceClaim(
                        claim_id=f"c_pm_{len(claims):03d}",
                        agent_id=self.agent_id,
                        claim_type=ClaimType.FACT,
                        content=f"Product decision detected: {c.text[:80]}...",
                        evidence_ids=[c.chunk_id],
                        confidence=0.90,
                    ))
                    break
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
            summary=f"Identified {len(claims)} product signals from the meeting.",
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
        tech_keywords = ["协议", "后端", "前端", "数据库", "API", "并发", "成本", "token", "Agent"]
        for c in chunks:
            for kw in tech_keywords:
                if kw in c.text:
                    claims.append(EvidenceClaim(
                        claim_id=f"c_arch_{len(claims):03d}",
                        agent_id=self.agent_id,
                        claim_type=ClaimType.INFERENCE if kw == "Agent" else ClaimType.FACT,
                        content=f"Technical concern: {kw} - {c.text[:60]}...",
                        evidence_ids=[c.chunk_id],
                        confidence=0.85,
                    ))
                    break
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
            summary=f"Found {len(claims)} technical signals and provided architecture recommendations.",
            claims=claims,
            open_questions=["What is the concurrency limit for agent dispatch?"],
            recommended_next_actions=["Lock down TranscriptChunk and EvidenceClaim protocols."],
        )


class ProjectManager(Agent):
    """Project execution planning agent."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("project_manager", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        return AgentReview(
            agent_id=self.agent_id,
            summary="This meeting did not explicitly set timelines, but the direction implies a 10-12 week MVP cycle.",
            claims=[
                EvidenceClaim(
                    claim_id="c_pjm_000",
                    agent_id=self.agent_id,
                    claim_type=ClaimType.RECOMMENDATION,
                    content="Recommend 6-phase delivery: Protocol -> Evidence -> Skills -> Team -> Supervisor -> Report.",
                    evidence_ids=[],
                    confidence=0.75,
                ),
            ],
            open_questions=["What is the team size?", "Are there external dependencies?"],
            recommended_next_actions=["Break work into 2-week sprints.", "Set up weekly review checkpoints."],
        )


class BusinessAnalyst(Agent):
    """Business analysis agent."""

    def __init__(self, provider: ProviderAdapter | None = None):
        super().__init__("business_analyst", provider=provider)

    def _analyze_mock(self, evidence: EvidencePacket) -> AgentReview:
        return AgentReview(
            agent_id=self.agent_id,
            summary="The product pivot from meeting notes to AI expert roundtable is a strong differentiator.",
            claims=[
                EvidenceClaim(
                    claim_id="c_ba_000",
                    agent_id=self.agent_id,
                    claim_type=ClaimType.INFERENCE,
                    content="The 'game-like expert team assembly' metaphor lowers adoption barriers vs raw Agent frameworks.",
                    evidence_ids=[],
                    confidence=0.72,
                ),
                EvidenceClaim(
                    claim_id="c_ba_001",
                    agent_id=self.agent_id,
                    claim_type=ClaimType.RECOMMENDATION,
                    content="Target early adopters in product management and consulting first — they have the highest willingness-to-pay for structured analysis.",
                    evidence_ids=[],
                    confidence=0.70,
                ),
            ],
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
