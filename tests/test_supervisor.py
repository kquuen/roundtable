"""Phase 3: Supervisor + Report Composer tests."""

import pytest
from roundtable.models import (
    TranscriptChunk, EvidencePacket, EvidenceClaim,
    AgentReview, SupervisorReview, ReviewResult, ClaimType,
)
from roundtable.supervisor import review_claims, summarize_review
from roundtable.report import compose_report


class TestSupervisorReviewClaims:
    def test_fact_with_evidence_approved(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_1", speaker="A", text="We decided on MVP scope.")]
        evidence = EvidencePacket(session_id="s_1", transcript_chunks=chunks)
        claim = EvidenceClaim(
            claim_id="c_0", agent_id="pm", claim_type=ClaimType.FACT,
            content="MVP scope decided.", evidence_ids=["t_0"], confidence=0.9,
        )
        review = AgentReview(agent_id="pm", summary="test", claims=[claim])
        results = review_claims([review], evidence)
        assert len(results) == 1
        assert results[0].review_result == ReviewResult.APPROVED

    def test_fact_without_evidence_rejected(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_2", speaker="A", text="test")]
        evidence = EvidencePacket(session_id="s_2", transcript_chunks=chunks)
        claim = EvidenceClaim(
            claim_id="c_0", agent_id="pm", claim_type=ClaimType.FACT,
            content="Budget is 5M.", evidence_ids=[], confidence=0.5,
        )
        review = AgentReview(agent_id="pm", summary="test", claims=[claim])
        results = review_claims([review], evidence)
        assert results[0].review_result == ReviewResult.REJECTED

    def test_fact_with_invalid_evidence_rejected(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_3", speaker="A", text="test")]
        evidence = EvidencePacket(session_id="s_3", transcript_chunks=chunks)
        claim = EvidenceClaim(
            claim_id="c_0", agent_id="pm", claim_type=ClaimType.FACT,
            content="Nothing.", evidence_ids=["t_999999"], confidence=0.9,
        )
        review = AgentReview(agent_id="pm", summary="test", claims=[claim])
        results = review_claims([review], evidence)
        assert results[0].review_result == ReviewResult.REJECTED

    def test_recommendation_auto_approved(self):
        evidence = EvidencePacket(session_id="s_4", transcript_chunks=[])
        claim = EvidenceClaim(
            claim_id="c_0", agent_id="arch", claim_type=ClaimType.RECOMMENDATION,
            content="Use FastAPI.", evidence_ids=[],
        )
        review = AgentReview(agent_id="arch", summary="test", claims=[claim])
        results = review_claims([review], evidence)
        assert results[0].review_result == ReviewResult.APPROVED

    def test_summarize_review(self):
        reviews = [
            SupervisorReview(claim_id="c_0", review_result=ReviewResult.APPROVED),
            SupervisorReview(claim_id="c_1", review_result=ReviewResult.APPROVED),
            SupervisorReview(claim_id="c_2", review_result=ReviewResult.REJECTED, reason="no ev"),
        ]
        s = summarize_review(reviews)
        assert s["total"] == 3
        assert s["approved"] == 2
        assert s["rejected"] == 1


class TestReportComposer:
    def test_generates_report(self):
        claim = EvidenceClaim(
            claim_id="c_0", agent_id="pm", claim_type=ClaimType.FACT,
            content="MVP scope decided.", evidence_ids=["t_0"], confidence=0.9,
        )
        agent_review = AgentReview(
            agent_id="pm",
            summary="Good meeting.",
            claims=[claim],
            open_questions=["What next?"],
            recommended_next_actions=["Implement Phase 0."],
        )
        supervisor_review = SupervisorReview(
            claim_id="c_0", review_result=ReviewResult.APPROVED, final_type="fact",
        )
        report = compose_report([agent_review], [supervisor_review], session_title="Test Meeting")
        assert "# 圆桌会议审查报告" in report
        assert "Test Meeting" in report
        assert "MVP scope" in report
        assert "审查统计" in report
