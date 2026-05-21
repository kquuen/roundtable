"""Phase 0: Protocol model unit tests."""

import pytest
from datetime import datetime
from roundtable.models import (
    Session, SessionMode, SessionStatus,
    TranscriptChunk,
    ClaimType, EvidenceClaim, EvidencePacket,
    AgentReview, SkillManifest,
    ReviewResult, SupervisorReview,
    MemoryWrite, RoundtableRun, TeamTemplate,
)


class TestSession:
    def test_default_values(self):
        s = Session(session_id="s_123")
        assert s.session_id == "s_123"
        assert s.mode == SessionMode.MEETING
        assert s.status == SessionStatus.RECORDING
        assert s.title == ""

    def test_full_init(self):
        s = Session(
            session_id="s_456",
            mode=SessionMode.PERSONAL_ROUNDTABLE,
            title="Personal Thinking",
            status=SessionStatus.ANALYZING,
            started_at=datetime(2026, 5, 22, 10, 0, 0),
        )
        assert s.mode == "personal_roundtable"
        assert s.title == "Personal Thinking"


class TestTranscriptChunk:
    def test_chunk_creation(self):
        c = TranscriptChunk(
            chunk_id="t_000001",
            session_id="s_123",
            speaker="Zhang San",
            text="We do tech validation first.",
            asr_confidence=0.91,
        )
        data = c.model_dump()
        assert data["chunk_id"] == "t_000001"
        assert data["asr_confidence"] == 0.91

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            TranscriptChunk(chunk_id="t", session_id="s", asr_confidence=1.5)
        with pytest.raises(Exception):
            TranscriptChunk(chunk_id="t", session_id="s", asr_confidence=-0.1)


class TestEvidenceClaim:
    def test_fact_claim_with_evidence(self):
        c = EvidenceClaim(
            claim_id="c_001",
            agent_id="architect",
            claim_type=ClaimType.FACT,
            content="Team decided to do 2-week tech validation.",
            evidence_ids=["t_000482"],
            confidence=0.88,
        )
        assert c.claim_type == "fact"
        assert len(c.evidence_ids) == 1

    def test_recommendation_no_evidence_needed(self):
        c = EvidenceClaim(
            claim_id="c_002",
            agent_id="product_manager",
            claim_type=ClaimType.RECOMMENDATION,
            content="Add user testing session.",
            evidence_ids=[],
        )
        assert len(c.evidence_ids) == 0
        assert c.claim_type == "recommendation"


class TestEvidencePacket:
    def test_packet_with_chunks(self):
        chunks = [
            TranscriptChunk(chunk_id="t_0", session_id="s_1", speaker="Zhang", text="Tech validation first."),
        ]
        packet = EvidencePacket(session_id="s_1", transcript_chunks=chunks)
        assert len(packet.transcript_chunks) == 1


class TestSkillManifest:
    def test_defaults(self):
        m = SkillManifest(skill_id="architect", name="Architect")
        assert m.version == "1.0.0"
        assert "inference" in m.allowed_claim_types


class TestSupervisorReview:
    def test_approved(self):
        r = SupervisorReview(claim_id="c_001", review_result=ReviewResult.APPROVED, final_type="fact")
        assert r.review_result == "approved"

    def test_rejected(self):
        r = SupervisorReview(claim_id="c_002", review_result=ReviewResult.REJECTED, reason="No evidence")
        assert r.review_result == "rejected"
        assert r.reason == "No evidence"


class TestRoundtableRun:
    def test_default_budget(self):
        run = RoundtableRun(run_id="r_001", session_id="s_001")
        assert run.budget["max_tokens"] == 80000
        assert run.budget["max_cost_cny"] == 5


class TestTeamTemplate:
    def test_template(self):
        t = TeamTemplate(
            team_id="product_deep_dive",
            name="Product Deep Dive",
            description="Product plan refinement",
            suitable_scenarios=["product_requirements", "positioning"],
            recommended_agents=["product_manager", "ux", "growth"],
            capability_scores={"product_value": 90, "tech_feasibility": 70},
        )
        assert len(t.recommended_agents) == 3
