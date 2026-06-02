"""Tests for Phase 3: Sentinel — circuit breaker, hallucination detection, health monitoring."""

import pytest
import uuid
from datetime import datetime, timezone

from roundtable.sentinel import CircuitBreaker, get_circuit_breaker, HallucinationDetector
from roundtable.models import DebateStep, EvidenceClaim, CircuitState
from roundtable import db


@pytest.fixture(autouse=True)
def setup_db():
    db.init_db()


def _ensure_session(session_id: str):
    try:
        conn = db._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, mode, title, status, created_by) VALUES (?, ?, ?, ?, ?)",
            (session_id, "meeting", "Test", "analyzing", "test_user"),
        )
        conn.commit()
    finally:
        conn.close()


class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker("test_agent_1")
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_record_success(self):
        cb = CircuitBreaker("test_agent_2")
        cb.record_success()
        health = db.get_agent_health("test_agent_2")
        assert health is not None
        assert health["circuit_state"] == "closed"

    def test_record_failure_below_threshold(self):
        cb = CircuitBreaker("test_agent_3")
        for _ in range(2):
            cb.record_failure()
        assert cb.state == CircuitState.CLOSED  # Need 5+ calls

    def test_circuit_opens_after_failures(self):
        cb = CircuitBreaker("test_agent_4")
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_circuit_half_open_after_cooldown(self):
        cb = CircuitBreaker("test_agent_5")
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # Simulate cooldown by manipulating internal state
        import time
        cb._open_time = time.time() - 61
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.can_execute() is True

    def test_half_open_success_closes(self):
        cb = CircuitBreaker("test_agent_6")
        for _ in range(5):
            cb.record_failure()
        cb._state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker("test_agent_7")
        for _ in range(5):
            cb.record_failure()
        cb._state = CircuitState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_reset(self):
        cb = CircuitBreaker("test_agent_8")
        for _ in range(5):
            cb.record_failure()
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_singleton(self):
        cb1 = get_circuit_breaker("shared_agent")
        cb2 = get_circuit_breaker("shared_agent")
        assert cb1 is cb2


class TestHallucinationDetector:
    def test_unsourced_numbers(self):
        _ensure_session("session_1")
        detector = HallucinationDetector()
        step = DebateStep(
            step_id="s1", group_id="g1", step_number=1, agent_id="pm",
            step_type="statement", content="市场规模达到 100 亿元，增长率 25%",
            sources=[],
        )
        flags = detector.check_step(step, "session_1")
        types = [f["type"] for f in flags]
        assert "unsourced_numbers" in types

    def test_no_unsourced_when_sources_present(self):
        detector = HallucinationDetector()
        step = DebateStep(
            step_id="s1", group_id="g1", step_number=1, agent_id="pm",
            step_type="statement", content="市场规模 100 亿元",
            sources=["艾瑞咨询报告"],
        )
        flags = detector.check_step(step, "session_2")
        types = [f["type"] for f in flags]
        assert "unsourced_numbers" not in types

    def test_low_confidence_unlabeled(self):
        _ensure_session("session_3")
        detector = HallucinationDetector()
        step = DebateStep(
            step_id="s1", group_id="g1", step_number=1, agent_id="pm",
            step_type="statement", content="这个产品一定会成功",
            confidence=0.3,
        )
        flags = detector.check_step(step, "session_3")
        types = [f["type"] for f in flags]
        assert "low_confidence_unlabeled" in types

    def test_low_confidence_with_speculation_label(self):
        detector = HallucinationDetector()
        step = DebateStep(
            step_id="s1", group_id="g1", step_number=1, agent_id="pm",
            step_type="statement", content="推测这个产品可能会成功",
            confidence=0.3,
        )
        flags = detector.check_step(step, "session_4")
        types = [f["type"] for f in flags]
        assert "low_confidence_unlabeled" not in types

    def test_repetition(self):
        _ensure_session("session_5")
        detector = HallucinationDetector()
        step1 = DebateStep(
            step_id="s1", group_id="g1", step_number=1, agent_id="pm",
            step_type="statement", content="这是一个非常重要的功能需求，建议优先实现",
        )
        step2 = DebateStep(
            step_id="s2", group_id="g1", step_number=2, agent_id="pm",
            step_type="challenge", content="这是一个非常重要的功能需求，建议优先实现",
        )
        flags1 = detector.check_step(step1, "session_5")
        flags2 = detector.check_step(step2, "session_5")
        types = [f["type"] for f in flags2]
        assert "repetition" in types

    def test_assertive_language(self):
        _ensure_session("session_6")
        detector = HallucinationDetector()
        step = DebateStep(
            step_id="s1", group_id="g1", step_number=1, agent_id="pm",
            step_type="statement", content="我们必须使用微服务架构，毫无疑问",
        )
        flags = detector.check_step(step, "session_6")
        types = [f["type"] for f in flags]
        assert "assertive_language" in types

    def test_claim_forbidden_topic(self):
        _ensure_session("session_7")
        detector = HallucinationDetector()
        claim = EvidenceClaim(
            claim_id="c1", agent_id="arch", claim_type="fact",
            content="这个产品对市场策略做判断，断言技术架构的可行性",
            evidence_ids=[],
        )
        flags = detector.check_claim(claim, ["断言技术架构的可行性", "对产品策略做判断"], "session_7")
        types = [f["type"] for f in flags]
        assert "forbidden_topic" in types

    def test_contradiction_detection(self):
        _ensure_session("session_8")
        detector = HallucinationDetector()
        steps = [
            DebateStep(step_id="s1", group_id="g1", step_number=1, agent_id="pm",
                       step_type="statement", content="这个方案是可行的"),
            DebateStep(step_id="s2", group_id="g1", step_number=2, agent_id="pm",
                       step_type="challenge", content="这个方案是不可行的"),
        ]
        flags = detector.check_contradictions(steps, "session_8")
        assert len(flags) >= 1
        assert flags[0]["type"] == "contradiction"


class TestSentinelDatabase:
    def test_agent_health_crud(self):
        aid = f"agent_{uuid.uuid4().hex[:8]}"
        db.upsert_agent_health(aid, status="healthy", success_delta=3, failure_delta=1)
        h = db.get_agent_health(aid)
        assert h is not None
        assert h["success_count"] == 3
        assert h["failure_count"] == 1

    def test_list_agent_health(self):
        db.upsert_agent_health("agent_y", status="degraded")
        health = db.list_agent_health()
        ids = [h["agent_id"] for h in health]
        assert "agent_y" in ids

    def test_reset_agent_health(self):
        db.upsert_agent_health("agent_z", failure_delta=10)
        db.reset_agent_health("agent_z")
        h = db.get_agent_health("agent_z")
        assert h["failure_count"] == 0
        assert h["circuit_state"] == "closed"

    def test_sentinel_alerts(self):
        _ensure_session("s_alert")
        alert_id = f"alt_{uuid.uuid4().hex[:8]}"
        db.insert_sentinel_alert(alert_id, "s_alert", "hallucination", "high", "pm", "c1", "发现幻觉")
        alerts = db.get_sentinel_alerts(session_id="s_alert")
        assert len(alerts) >= 1
        assert alerts[0]["severity"] == "high"

    def test_acknowledge_alert(self):
        _ensure_session("s_ack")
        alert_id = f"alt_{uuid.uuid4().hex[:8]}"
        db.insert_sentinel_alert(alert_id, "s_ack", "timeout", "medium", None, None, "超时")
        ok = db.acknowledge_alert(alert_id)
        assert ok is True
        alerts = db.get_sentinel_alerts(session_id="s_ack")
        assert alerts[0]["acknowledged"] is True
