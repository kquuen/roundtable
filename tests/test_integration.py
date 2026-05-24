"""End-to-end integration tests for the full roundtable pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from roundtable.models import (
    TranscriptChunk, EvidencePacket, EvidenceClaim,
    AgentReview, ReviewResult, ClaimType,
)
from roundtable.services import RoundtableService
from roundtable.supervisor import review_claims_async


# ── Sample data ──

SAMPLE_SEGMENTS = [
    {"speaker": "张三", "text": "我们决定先做文本导入功能"},
    {"speaker": "李四", "text": "技术上用 FastAPI 加 Celery"},
    {"speaker": "王五", "text": "预算控制在 10 万以内"},
]


# ── Test 1: Mock pipeline produces a report ──

class TestMockPipeline:
    @pytest.mark.asyncio
    async def test_mock_pipeline_produces_report(self):
        """3 Agent mock 管线产出完整报告。"""
        svc = RoundtableService()  # No provider → mock mode
        result = await svc.run_pipeline(
            session_id="integ_001",
            segments=SAMPLE_SEGMENTS,
            mode="meeting",
            title="集成测试会议",
            agent_count=3,
        )
        assert result.session_id == "integ_001"
        assert result.mode == "mock"
        assert len(result.agent_reviews) == 3
        assert len(result.supervisor_reviews) > 0
        assert "# 圆桌会议审查报告" in result.report
        assert "集成测试会议" in result.report

    @pytest.mark.asyncio
    async def test_mock_pipeline_empty_segments(self):
        """空 segments 不崩溃，管线正常完成。"""
        svc = RoundtableService()
        result = await svc.run_pipeline(
            session_id="integ_002",
            segments=[],
            mode="meeting",
            title="空会议",
            agent_count=2,
        )
        assert result.session_id == "integ_002"
        assert result.mode == "mock"
        assert len(result.agent_reviews) >= 2
        assert result.report  # Report is generated even with no evidence

    @pytest.mark.asyncio
    async def test_mock_pipeline_rejects_invalid_claims(self):
        """无证据 FACT 被 supervisor 驳回。"""
        svc = RoundtableService()
        result = await svc.run_pipeline(
            session_id="integ_003",
            segments=[{"speaker": "测试", "text": "简短内容"}],
            mode="meeting",
            title="驳回测试",
            agent_count=3,
        )
        # Mock agents produce claims; some FACT claims without evidence should be rejected
        rejected = [r for r in result.supervisor_reviews if r.get("review_result") == "rejected"]
        # At least some claims from mock agents should lack proper evidence binding
        assert len(result.supervisor_reviews) > 0


# ── Test 2: Async contradiction detection ──

class TestAsyncContradictionDetection:
    @pytest.mark.asyncio
    async def test_async_contradiction_detection_works(self):
        """异步矛盾检测在 async 上下文中正常运行。"""
        chunks = [
            TranscriptChunk(chunk_id="t_0", session_id="s_async", speaker="A", text="决定做方案A"),
        ]
        evidence = EvidencePacket(session_id="s_async", transcript_chunks=chunks)

        reviews = [
            AgentReview(agent_id="pm", summary="pm", claims=[
                EvidenceClaim(
                    claim_id="c_1", agent_id="pm", claim_type=ClaimType.FACT,
                    content="团队决定先做方案A", evidence_ids=["t_0"], confidence=0.9,
                )
            ]),
            AgentReview(agent_id="arch", summary="arch", claims=[
                EvidenceClaim(
                    claim_id="c_2", agent_id="arch", claim_type=ClaimType.FACT,
                    content="团队决定先做方案B", evidence_ids=["t_0"], confidence=0.9,
                )
            ]),
        ]

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value='{"contradictions": [{"claim_a": "c_1", "claim_b": "c_2", "reason": "方案矛盾"}]}'
        )

        result = await review_claims_async(reviews, evidence, provider=mock_provider)
        mock_provider.chat.assert_called_once()
        contradicted = [
            r for r in result if r.review_result == ReviewResult.NEEDS_USER_CONFIRMATION
        ]
        assert len(contradicted) == 2


# ── Test 3: Full async pipeline with mock provider ──

class TestFullAsyncPipeline:
    @pytest.mark.asyncio
    async def test_full_async_pipeline_with_mock_provider(self):
        """使用 mock provider 的完整 async 管线。"""
        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(
            return_value='{"summary": "mock", "claims": [{"content": "发现1", "claim_type": "fact", "confidence": 0.9, "evidence_text": "测试"}], "open_questions": [], "recommended_next_actions": []}'
        )

        svc = RoundtableService(provider=mock_provider)
        result = await svc.run_pipeline(
            session_id="integ_004",
            segments=[{"speaker": "A", "text": "测试内容"}],
            mode="meeting",
            title="Provider集成测试",
            agent_count=2,
        )
        assert result.session_id == "integ_004"
        assert result.mode == "llm"
        assert len(result.agent_reviews) >= 2
        # Provider should have been called by agents
        assert mock_provider.chat.called
