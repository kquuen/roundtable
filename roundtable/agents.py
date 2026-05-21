"""Phase 2: Agent base class and built-in expert agents.

POC mode: uses rule-engine simulation instead of real LLM calls.
Production mode: replace analyze() with LLM API call.
"""

from __future__ import annotations

from roundtable.models import (
    AgentReview, EvidenceClaim, EvidencePacket, ClaimType, SkillManifest,
)
from roundtable.skills import load_skill


class Agent:
    """Base agent class. Subclasses define analysis logic per role."""

    def __init__(self, skill_id: str):
        self.skill: SkillManifest = load_skill(skill_id)

    @property
    def agent_id(self) -> str:
        return self.skill.skill_id

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
        """Override in subclass to implement expert analysis."""
        raise NotImplementedError


# ── Built-in agents ──


class ProductManager(Agent):
    """Product strategy analysis agent."""

    def __init__(self):
        super().__init__("product_manager")

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
        chunks = evidence.transcript_chunks
        claims = []

        # Identify product decisions from evidence
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

        # Always add at least one recommendation
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

    def __init__(self):
        super().__init__("architect")

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
        chunks = evidence.transcript_chunks
        claims = []

        # Identify technical decisions
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

    def __init__(self):
        super().__init__("project_manager")

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
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

    def __init__(self):
        super().__init__("business_analyst")

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
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

    def __init__(self):
        super().__init__("supervisor")

    def analyze(self, evidence: EvidencePacket) -> AgentReview:
        return AgentReview(
            agent_id=self.agent_id,
            summary="Supervisor agent confirms: this POC run is valid. All claims should go through the review module.",
            claims=[],
            open_questions=[],
            recommended_next_actions=["Run claims through supervisor.py review module."],
        )
