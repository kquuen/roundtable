"""Phase 5 测试：人在回路 + 状态机 + 三级边界分类。

覆盖：
- 新增枚举（ClaimLifecycle, ConsensusLevel, BoundaryClass）
- feedback.py（UserVerdict, apply_bulk_verdicts, process_user_correction）
- 新 API 端点（/pending, /review/confirm, /feedback, /memory/confirm）
- classify_boundary_crossing 三级分类
- 完整状态机链路
- report.py 边界标签输出
"""

import pytest
from roundtable.models import (
    EvidenceClaim, SupervisorReview, ReviewResult, ClaimType,
    ClaimLifecycle, ConsensusLevel, BoundaryClass,
    AgentReview, EvidencePacket, PipelineResult,
)
from roundtable.feedback import (
    UserVerdict, UserCorrection,
    process_user_verdict, apply_bulk_verdicts,
    process_user_correction, process_user_answer,
    update_memory_confirmation, get_pending_items,
)
from roundtable.supervisor import classify_boundary_crossing


# ═══════════════════════════════════════════
# 新增枚举测试
# ═══════════════════════════════════════════

class TestClaimLifecycle:
    def test_all_values_valid(self):
        assert ClaimLifecycle.DRAFT.value == "draft"
        assert ClaimLifecycle.NEEDS_USER.value == "needs_user"
        assert ClaimLifecycle.USER_CONFIRMED.value == "user_confirmed"
        assert ClaimLifecycle.USER_REJECTED.value == "user_rejected"

    def test_evidence_claim_defaults(self):
        claim = EvidenceClaim(claim_id="c_001", agent_id="pm", content="test")
        assert claim.lifecycle == ClaimLifecycle.DRAFT
        assert claim.consensus_level == ConsensusLevel.UNKNOWN


class TestConsensusLevel:
    def test_all_values(self):
        assert ConsensusLevel.STRONG.value == "strong"
        assert ConsensusLevel.MAJORITY.value == "majority"
        assert ConsensusLevel.ISOLATED.value == "isolated"
        assert ConsensusLevel.CONTRADICTED.value == "contradicted"
        assert ConsensusLevel.UNKNOWN.value == "unknown"


class TestBoundaryClass:
    def test_all_values(self):
        assert BoundaryClass.SAFE.value == "safe"
        assert BoundaryClass.BORDERLINE.value == "borderline"
        assert BoundaryClass.VIOLATION.value == "violation"

    def test_supervisor_review_has_boundary_field(self):
        sr = SupervisorReview(
            claim_id="c_001",
            review_result=ReviewResult.APPROVED,
        )
        assert sr.boundary_classification is None

    def test_supervisor_review_with_boundary(self):
        sr = SupervisorReview(
            claim_id="c_001",
            review_result=ReviewResult.REJECTED,
            reason="test",
            boundary_classification=BoundaryClass.VIOLATION,
        )
        assert sr.boundary_classification == BoundaryClass.VIOLATION


# ═══════════════════════════════════════════
# PipelineResult 测试
# ═══════════════════════════════════════════

class TestPipelineResult:
    def test_defaults(self):
        pr = PipelineResult(session_id="s_001")
        assert pr.pending_confirmation_count == 0
        assert pr.user_decisions_applied == 0

    def test_with_pending(self):
        pr = PipelineResult(
            session_id="s_001",
            pending_confirmation_count=3,
            user_decisions_applied=2,
        )
        assert pr.pending_confirmation_count == 3
        assert pr.user_decisions_applied == 2


# ═══════════════════════════════════════════
# feedback.py — UserVerdict 测试
# ═══════════════════════════════════════════

class TestUserVerdict:
    def test_confirm(self):
        v = UserVerdict.from_dict({"claim_id": "c_001", "decision": "confirm"})
        assert v.claim_id == "c_001"
        assert v.decision == "confirm"

    def test_reject(self):
        v = UserVerdict.from_dict({"claim_id": "c_002", "decision": "reject", "note": "不对"})
        assert v.decision == "reject"
        assert v.note == "不对"

    def test_retype(self):
        v = UserVerdict.from_dict({"claim_id": "c_003", "decision": "retype", "new_type": "fact"})
        assert v.decision == "retype"
        assert v.new_type == "fact"

    def test_invalid_decision(self):
        with pytest.raises(ValueError, match="Invalid decision"):
            UserVerdict.from_dict({"claim_id": "c_001", "decision": "approve"})

    def test_retype_requires_new_type(self):
        with pytest.raises(ValueError, match="retype requires new_type"):
            UserVerdict.from_dict({"claim_id": "c_001", "decision": "retype"})


class TestProcessUserVerdict:
    def test_confirm_updates_review_and_lifecycle(self):
        claim = EvidenceClaim(claim_id="c_001", agent_id="pm", content="test")
        ar = AgentReview(agent_id="pm", summary="", claims=[claim])
        sr = SupervisorReview(claim_id="c_001", review_result=ReviewResult.NEEDS_USER_CONFIRMATION, reason="矛盾")

        verdict = UserVerdict(claim_id="c_001", decision="confirm")
        result = process_user_verdict(verdict, [sr], [ar])

        assert result["updated"] is True
        assert result["new_status"] == "approved"
        assert claim.lifecycle == ClaimLifecycle.USER_CONFIRMED

    def test_reject_updates_review_and_lifecycle(self):
        claim = EvidenceClaim(claim_id="c_002", agent_id="architect", content="test")
        ar = AgentReview(agent_id="architect", summary="", claims=[claim])
        sr = SupervisorReview(claim_id="c_002", review_result=ReviewResult.NEEDS_USER_CONFIRMATION, reason="模糊")

        verdict = UserVerdict(claim_id="c_002", decision="reject", note="可以忽略")
        result = process_user_verdict(verdict, [sr], [ar])

        assert result["updated"] is True
        assert sr.review_result == ReviewResult.REJECTED
        assert "用户驳回" in sr.reason
        assert claim.lifecycle == ClaimLifecycle.USER_REJECTED

    def test_unknown_claim_id(self):
        sr = SupervisorReview(claim_id="c_001", review_result=ReviewResult.NEEDS_USER_CONFIRMATION)
        verdict = UserVerdict(claim_id="c_999", decision="confirm")
        result = process_user_verdict(verdict, [sr], [])
        assert result["updated"] is False
        assert "claim not found" in result["error"]


class TestApplyBulkVerdicts:
    def test_bulk_applies_all(self):
        claims = [
            EvidenceClaim(claim_id="c_1", agent_id="pm", content="a"),
            EvidenceClaim(claim_id="c_2", agent_id="pm", content="b"),
        ]
        ar = AgentReview(agent_id="pm", summary="", claims=claims)
        srs = [
            SupervisorReview(claim_id="c_1", review_result=ReviewResult.NEEDS_USER_CONFIRMATION),
            SupervisorReview(claim_id="c_2", review_result=ReviewResult.NEEDS_USER_CONFIRMATION),
        ]
        verdicts = [
            UserVerdict(claim_id="c_1", decision="confirm"),
            UserVerdict(claim_id="c_2", decision="reject"),
        ]
        result = apply_bulk_verdicts(verdicts, srs, [ar])
        assert result["applied"] == 2
        assert result["failed"] == 0


# ═══════════════════════════════════════════
# feedback.py — UserCorrection 测试
# ═══════════════════════════════════════════

class TestUserCorrection:
    def test_from_dict(self):
        c = UserCorrection.from_dict({
            "target": "用户倾向保守",
            "correction": "上次保守是因为预算限制",
            "reason": "不是性格偏好",
        })
        assert "保守" in c.target
        assert "预算" in c.correction

    def test_process_records(self):
        c = UserCorrection(target="推断A", correction="更正A", reason="")
        result = process_user_correction(c)
        assert result["recorded"] is True
        assert result["target"] == "推断A"


class TestProcessUserAnswer:
    def test_records_answer(self):
        result = process_user_answer("s_001", "目标用户是谁？", "应届生")
        assert result["recorded"] is True
        assert result["session_id"] == "s_001"


# ═══════════════════════════════════════════
# 待办查询测试
# ═══════════════════════════════════════════

class TestGetPendingItems:
    def test_returns_needs_confirmation_claims(self):
        claims = [
            EvidenceClaim(claim_id="c_1", agent_id="pm", content="需要确认A", confidence=0.8),
            EvidenceClaim(claim_id="c_2", agent_id="architect", content="通过B", confidence=0.9),
        ]
        ar = AgentReview(agent_id="pm", summary="", claims=claims)
        srs = [
            SupervisorReview(claim_id="c_1", review_result=ReviewResult.NEEDS_USER_CONFIRMATION, reason="矛盾"),
            SupervisorReview(claim_id="c_2", review_result=ReviewResult.APPROVED),
        ]
        pending = get_pending_items(srs, [ar])
        assert len(pending) == 1
        assert pending[0]["claim_id"] == "c_1"

    def test_empty_when_no_pending(self):
        claims = [EvidenceClaim(claim_id="c_1", agent_id="pm", content="ok")]
        ar = AgentReview(agent_id="pm", summary="", claims=claims)
        srs = [SupervisorReview(claim_id="c_1", review_result=ReviewResult.APPROVED)]
        pending = get_pending_items(srs, [ar])
        assert len(pending) == 0


# ═══════════════════════════════════════════
# classify_boundary_crossing 测试
# ═══════════════════════════════════════════

class TestClassifyBoundaryCrossing:
    def test_safe_when_no_forbidden(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm", content="这是产品分析")
        result = classify_boundary_crossing(claim, [])
        assert result == BoundaryClass.SAFE

    def test_violation_when_architect_claims_architecture(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm",
                              content="系统架构必须采用微服务", claim_type=ClaimType.FACT)
        forbidden = ["断言技术架构的可行性"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.VIOLATION

    def test_borderline_when_hedging(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm",
                              content="也许可以考虑微服务架构", claim_type=ClaimType.INFERENCE)
        forbidden = ["断言技术架构的可行性"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.BORDERLINE

    def test_borderline_for_assertion_in_fact_claim(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm",
                              content="应该先做文本导入", claim_type=ClaimType.FACT)
        forbidden = ["把建议写成事实"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.BORDERLINE

    def test_violation_for_assertive_fact(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm",
                              content="毫无疑问，我们应该做文本导入功能", claim_type=ClaimType.FACT)
        forbidden = ["把建议写成事实"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.VIOLATION

    def test_borderline_for_product_strategy_by_architect(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="architect",
                              content="用户价值可能需要重新评估")
        forbidden = ["对产品策略做判断"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.BORDERLINE

    def test_violation_for_product_strategy_assertion(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="architect",
                              content="竞品对比很明显我们赢了")
        forbidden = ["对产品策略做判断"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.VIOLATION

    def test_supervisor_borderline(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="supervisor",
                              content="建议增加安全性审查")
        forbidden = ["提出新的专家建议"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.BORDERLINE

    def test_project_manager_date_violation(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="project_manager",
                              content="3月15日可以交付", claim_type=ClaimType.INFERENCE)
        forbidden = ["断言未确认的交付日期"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.VIOLATION

    def test_project_manager_date_borderline(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="project_manager",
                              content="也许下个月可以开始", claim_type=ClaimType.INFERENCE)
        forbidden = ["断言未确认的交付日期"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.BORDERLINE


# ═══════════════════════════════════════════
# 报告标签输出测试
# ═══════════════════════════════════════════

class TestReportTags:
    def test_report_includes_pending_count(self):
        """验证 PipelineResult 携带 pending 数量。"""
        pr = PipelineResult(
            session_id="s_test",
            pending_confirmation_count=2,
            report="# Test Report",
        )
        assert pr.pending_confirmation_count == 2

    def test_report_compose_with_boundary(self):
        """验证报告生成包含边界标签。"""
        from roundtable.report import compose_report
        claims = [
            EvidenceClaim(claim_id="c_1", agent_id="pm", content="边界模糊的架构观点",
                         claim_type=ClaimType.INFERENCE, lifecycle=ClaimLifecycle.DRAFT),
        ]
        ar = AgentReview(agent_id="pm", summary="测试", claims=claims)
        srs = [
            SupervisorReview(claim_id="c_1", review_result=ReviewResult.APPROVED,
                           boundary_classification=BoundaryClass.BORDERLINE),
        ]
        report = compose_report([ar], srs, session_title="边界测试")
        # 报告不应崩溃，且包含内容
        assert "边界模糊的架构观点" in report
        assert "边界模糊" in report


# ═══════════════════════════════════════════
# UserCorrection 集成测试
# ═══════════════════════════════════════════

class TestFeedbackModule:
    def test_process_user_correction_returns_structured(self):
        corr = UserCorrection(target="推断A", correction="更正A", reason="理由")
        result = process_user_correction(corr)
        assert result["recorded"] is True
        assert "timestamp" in result

    def test_process_user_answer_returns_structured(self):
        result = process_user_answer("s_001", "Q?", "A!")
        assert result["recorded"] is True
        assert result["session_id"] == "s_001"
        assert "timestamp" in result


# ═══════════════════════════════════════════
# 边界情况
# ═══════════════════════════════════════════

class TestEdgeCases:
    def test_empty_forbidden_list(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm",
                              content="架构必须用微服务", claim_type=ClaimType.FACT)
        result = classify_boundary_crossing(claim, [])
        assert result == BoundaryClass.SAFE

    def test_forbidden_not_matching(self):
        claim = EvidenceClaim(claim_id="c_1", agent_id="pm",
                              content="用户喜欢这个功能", claim_type=ClaimType.INFERENCE)
        forbidden = ["断言技术架构的可行性"]
        result = classify_boundary_crossing(claim, forbidden)
        assert result == BoundaryClass.SAFE

    def test_empty_pending_list(self):
        pending = get_pending_items([], [])
        assert pending == []

    def test_verdict_for_nonexistent_claim(self):
        sr = SupervisorReview(claim_id="c_001", review_result=ReviewResult.APPROVED)
        verdict = UserVerdict(claim_id="c_999", decision="confirm")
        result = process_user_verdict(verdict, [sr], [])
        assert result["updated"] is False
