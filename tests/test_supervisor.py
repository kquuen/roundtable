"""Phase 3: Supervisor + Report Composer tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from roundtable.models import (
    TranscriptChunk, EvidencePacket, EvidenceClaim,
    AgentReview, SupervisorReview, ReviewResult, ClaimType,
)
from roundtable.supervisor import review_claims, review_claims_async, summarize_review
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

    def test_generates_english_report(self):
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
        report = compose_report(
            [agent_review], [supervisor_review],
            session_title="Test Meeting", lang="en",
        )
        assert "# Roundtable Review Report" in report
        assert "## Summary" in report
        assert "## Meeting Facts" in report
        assert "## Open Questions" in report
        assert "Review Statistics" in report
        # Should NOT contain Chinese section titles
        assert "圆桌会议审查报告" not in report
        assert "摘要" not in report


class TestReviewClaimsAsync:
    @pytest.mark.asyncio
    async def test_contradiction_detection_runs_in_async_context(self):
        """验证在 async 上下文中矛盾检测能正常工作。"""
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_1", speaker="A", text="test")]
        evidence = EvidencePacket(session_id="s_1", transcript_chunks=chunks)

        reviews = [
            AgentReview(agent_id="pm", summary="test", claims=[
                EvidenceClaim(claim_id="c_1", agent_id="pm", claim_type=ClaimType.FACT,
                             content="团队决定先做文本导入", evidence_ids=["t_0"], confidence=0.9)
            ]),
            AgentReview(agent_id="arch", summary="test2", claims=[
                EvidenceClaim(claim_id="c_2", agent_id="arch", claim_type=ClaimType.FACT,
                             content="团队决定先做实时ASR", evidence_ids=["t_0"], confidence=0.9)
            ]),
        ]

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value='{"contradictions": [{"claim_a": "c_1", "claim_b": "c_2", "reason": "矛盾"}]}'
        )

        result = await review_claims_async(reviews, evidence, provider=mock_provider)
        mock_provider.chat.assert_called_once()
        for r in result:
            if r.claim_id in ("c_1", "c_2"):
                assert r.review_result == ReviewResult.NEEDS_USER_CONFIRMATION

    @pytest.mark.asyncio
    async def test_no_contradiction_approved(self):
        """无矛盾时 approved 保持。"""
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_1", speaker="A", text="test")]
        evidence = EvidencePacket(session_id="s_1", transcript_chunks=chunks)

        reviews = [
            AgentReview(agent_id="pm", summary="test", claims=[
                EvidenceClaim(claim_id="c_1", agent_id="pm", claim_type=ClaimType.FACT,
                             content="test", evidence_ids=["t_0"], confidence=0.9)
            ]),
            AgentReview(agent_id="arch", summary="test2", claims=[
                EvidenceClaim(claim_id="c_2", agent_id="arch", claim_type=ClaimType.FACT,
                             content="another", evidence_ids=["t_0"], confidence=0.9)
            ]),
        ]

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(return_value='{"contradictions": []}')

        result = await review_claims_async(reviews, evidence, provider=mock_provider)
        approved = sum(1 for r in result if r.review_result == ReviewResult.APPROVED)
        assert approved == 2
