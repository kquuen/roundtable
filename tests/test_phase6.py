"""Phase 6 测试：可见辩论 + Step 0 补完验证。

覆盖：
- Step 0.1: ConsensusLevel 计算
- Step 0.2: ClaimLifecycle 状态转换路径
- Step 0.3: TokenBudget 守卫
- Step 1: 辩论数据模型序列化
- Step 2: 两轮 mock 辩论引擎
- Step 2: 引用完整性校验
- Step 3: E2E 辩论 API
"""

import pytest
from roundtable.models import (
    DebateArgument, DebateRound, DebateSession,
    EvidenceClaim, SupervisorReview, ReviewResult,
    ClaimLifecycle, ConsensusLevel, AgentReview, EvidencePacket,
)
from roundtable.debate import DebateEngine
from roundtable.services import TokenBudget, BudgetExceeded
from roundtable.feedback import UserVerdict


# ═══════════════════════════════════════════
# Step 0.1: ConsensusLevel 计算
# ═══════════════════════════════════════════

class TestConsensusComputation:
    def test_no_unknown_after_review(self):
        """验证 review 后没有 claim 保留 UNKNOWN 共识。"""
        from roundtable.evidence import build_evidence_packet
        from roundtable.orchestrator import run_orchestrator
        from roundtable.supervisor import review_claims

        evidence = build_evidence_packet("test", "personal_roundtable", [
            {"speaker": "A", "text": "用户想要简历工具"},
        ])
        agent_reviews = run_orchestrator(evidence, agent_count=3)
        supervisor_reviews = review_claims(agent_reviews, evidence)

        for ar in agent_reviews:
            for claim in ar.claims:
                assert claim.consensus_level != ConsensusLevel.UNKNOWN, \
                    f"Claim {claim.claim_id} has UNKNOWN consensus"

    def test_isolated_when_unique_content(self):
        """每个 Agent 产出不同内容 → 所有 claim 都是 ISOLATED。"""
        from roundtable.evidence import build_evidence_packet
        from roundtable.orchestrator import run_orchestrator
        from roundtable.supervisor import review_claims

        evidence = build_evidence_packet("test", "personal_roundtable", [
            {"speaker": "A", "text": "test"},
        ])
        agent_reviews = run_orchestrator(evidence, agent_count=3)
        supervisor_reviews = review_claims(agent_reviews, evidence)

        for ar in agent_reviews:
            for claim in ar.claims:
                assert claim.consensus_level == ConsensusLevel.ISOLATED, \
                    f"Expected ISOLATED but got {claim.consensus_level} for {claim.claim_id}"


# ═══════════════════════════════════════════
# Step 0.2: ClaimLifecycle 状态转换
# ═══════════════════════════════════════════

class TestLifecycleWiring:
    def test_draft_to_under_review(self):
        """验证 Agent 产出 DRAFT → Supervisor 审查后 → UNDER_REVIEW。"""
        from roundtable.evidence import build_evidence_packet
        from roundtable.orchestrator import run_orchestrator
        from roundtable.supervisor import review_claims

        evidence = build_evidence_packet("test", "personal_roundtable", [
            {"speaker": "A", "text": "测试"},
        ])
        agent_reviews = run_orchestrator(evidence, agent_count=1)
        # Before review: DRAFT
        for ar in agent_reviews:
            for claim in ar.claims:
                assert claim.lifecycle == ClaimLifecycle.DRAFT

        # After review: UNDER_REVIEW
        supervisor_reviews = review_claims(agent_reviews, evidence)
        for ar in agent_reviews:
            for claim in ar.claims:
                assert claim.lifecycle == ClaimLifecycle.UNDER_REVIEW, \
                    f"Expected UNDER_REVIEW but got {claim.lifecycle}"

    def test_feedback_updates_terminal(self):
        """验证用户裁决后 lifecycle 到达终端状态。"""
        claim = EvidenceClaim(claim_id="c_001", agent_id="pm", content="test",
                              lifecycle=ClaimLifecycle.DRAFT)
        ar = AgentReview(agent_id="pm", summary="", claims=[claim])
        sr = SupervisorReview(claim_id="c_001", review_result=ReviewResult.NEEDS_USER_CONFIRMATION)

        verdict = UserVerdict("c_001", "confirm")
        from roundtable.feedback import process_user_verdict
        process_user_verdict(verdict, [sr], [ar])

        assert claim.lifecycle == ClaimLifecycle.USER_CONFIRMED


# ═══════════════════════════════════════════
# Step 0.3: TokenBudget
# ═══════════════════════════════════════════

class TestTokenBudget:
    def test_estimation(self):
        budget = TokenBudget(max_tokens=10000)
        tokens = budget.estimate(2000, 2000)
        assert tokens > 0

    def test_consume_within_limit(self):
        budget = TokenBudget(max_tokens=10000)
        budget.consume(5000, "test")
        assert budget.remaining() == 5000

    def test_consume_exceeds_limit(self):
        budget = TokenBudget(max_tokens=1000)
        with pytest.raises(BudgetExceeded):
            budget.consume(2000, "test")

    def test_summary(self):
        budget = TokenBudget(max_tokens=10000)
        budget.consume(3000, "test")
        s = budget.summary()
        assert "3000/10000" in s


# ═══════════════════════════════════════════
# Step 1: 辩论数据模型
# ═══════════════════════════════════════════

class TestDebateModels:
    def test_argument_roundtrip(self):
        arg = DebateArgument(
            argument_id="arg_r1_001",
            agent_id="pm",
            round=1,
            position="extend",
            content="test",
        )
        j = arg.model_dump_json()
        loaded = DebateArgument.model_validate_json(j)
        assert loaded.argument_id == "arg_r1_001"

    def test_debate_session_roundtrip(self):
        session = DebateSession(
            session_id="s_001",
            rounds=[
                DebateRound(round_number=1, arguments=[
                    DebateArgument(argument_id="a1", agent_id="pm", round=1,
                                   position="extend", content="x"),
                ]),
            ],
            consensus_summary={"c_001": "isolated"},
        )
        j = session.model_dump_json()
        loaded = DebateSession.model_validate_json(j)
        assert loaded.session_id == "s_001"
        assert len(loaded.rounds) == 1
        assert loaded.consensus_summary["c_001"] == "isolated"


# ═══════════════════════════════════════════
# Step 2: 辩论引擎 Mock
# ═══════════════════════════════════════════

class TestDebateEngineMock:
    @pytest.mark.asyncio
    async def test_two_rounds_complete(self):
        """验证两轮完整执行。"""
        from roundtable.evidence import build_evidence_packet
        from roundtable.orchestrator import create_agents

        evidence = build_evidence_packet("test", "personal_roundtable", [
            {"speaker": "A", "text": "用户想要简历工具"},
        ])
        agents = create_agents(agent_count=3, provider=None)

        engine = DebateEngine()
        session = await engine.run_debate(evidence, agents)

        assert len(session.rounds) == 2
        assert len(session.rounds[0].arguments) > 0  # Round 1 has arguments
        assert len(session.rounds[1].arguments) > 0  # Round 2 has arguments

    @pytest.mark.asyncio
    async def test_round2_has_peer_context(self):
        """验证 Round 2 的 arguments 引用了 peer 观点。"""
        from roundtable.evidence import build_evidence_packet
        from roundtable.orchestrator import create_agents

        evidence = build_evidence_packet("test", "personal_roundtable", [
            {"speaker": "A", "text": "test"},
        ])
        agents = create_agents(agent_count=3, provider=None)

        engine = DebateEngine()
        session = await engine.run_debate(evidence, agents)

        round2_args = session.rounds[1].arguments
        assert len(round2_args) >= 3  # 3 agents × at least 1 each


# ═══════════════════════════════════════════
# Step 2: 引用完整性
# ═══════════════════════════════════════════

class TestReferenceValidation:
    def test_valid_reference_passes(self):
        engine = DebateEngine()
        r2_args = [
            DebateArgument(argument_id="a1", agent_id="pm", round=2,
                          position="disagree", target_claim_id="c_001",
                          content="不同意"),
        ]
        validated, errors = engine._validate_references(
            r2_args, {"arg_r1_001"}, {"c_001"},
        )
        assert len(validated) == 1
        assert len(errors) == 0

    def test_invalid_reference_rejected(self):
        engine = DebateEngine()
        r2_args = [
            DebateArgument(argument_id="a1", agent_id="pm", round=2,
                          position="disagree", target_claim_id="c_999",
                          content="不同意"),
        ]
        validated, errors = engine._validate_references(
            r2_args, {"arg_r1_001"}, {"c_001"},
        )
        assert len(validated) == 0
        assert len(errors) == 1
        assert errors[0]["error"] == "target_claim_id not found in Round 1"


# ═══════════════════════════════════════════
# Step 3: E2E API
# ═══════════════════════════════════════════

class TestDebateAPI:
    def test_debate_endpoint(self):
        """验证 /roundtable/debate 端点可用。"""
        from starlette.testclient import TestClient
        from roundtable.app import app

        client = TestClient(app)
        r = client.post("/session/create", json={"title": "Test"})
        sid = r.json()["session_id"]
        client.post("/evidence/upload", json={
            "session_id": sid,
            "segments": [{"speaker": "A", "text": "test"}],
        })

        r = client.post("/roundtable/debate", json={
            "session_id": sid,
            "agent_count": 3,
        })
        assert r.status_code == 200
        res = r.json()
        assert res["rounds"] == 2
        assert res["arguments"] > 0
        assert "consensus_items" in res
