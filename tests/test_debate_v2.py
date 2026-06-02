"""Tests for Phase 2: Structured 4-Step Debate Engine V2."""

import pytest
import uuid
from datetime import datetime, timezone

from roundtable.debate_v2 import DebateEngineV2
from roundtable.models import (
    AgentManifest, AgentGroup, DebateStep,
    EvidencePacket, TranscriptChunk,
    DebateStepType, AgreementLevel,
)
from roundtable import db


def _ensure_session(session_id: str):
    """Create a minimal session record so FK constraints pass."""
    try:
        conn = db._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, mode, title, status, created_by) VALUES (?, ?, ?, ?, ?)",
            (session_id, "meeting", "Test", "analyzing", "test_user"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Reset in-memory sequence counter before each test."""
    db.reset_sequence("test_session")


class TestDebateEngineV2:
    def test_mock_step1(self):
        engine = DebateEngineV2()
        raw = engine._mock_response("Step 1: 开场陈述")
        parsed = engine._safe_json_parse(raw, {})
        assert "methodology" in parsed
        assert "key_questions" in parsed
        assert "confidence" in parsed

    def test_mock_step2(self):
        engine = DebateEngineV2()
        raw = engine._mock_response("Step 2: 强制质疑")
        parsed = engine._safe_json_parse(raw, {})
        assert "challenges" in parsed

    def test_mock_step3(self):
        engine = DebateEngineV2()
        raw = engine._mock_response("Step 3: 新视角")
        parsed = engine._safe_json_parse(raw, {})
        assert "new_angles" in parsed

    def test_mock_step4(self):
        engine = DebateEngineV2()
        raw = engine._mock_response("Step 4: 回应")
        parsed = engine._safe_json_parse(raw, {})
        assert "final_position" in parsed
        assert "consensus_assessment" in parsed

    def test_safe_json_parse_with_markdown(self):
        engine = DebateEngineV2()
        raw = '```json\n{"key": "value"}\n```'
        parsed = engine._safe_json_parse(raw, {})
        assert parsed == {"key": "value"}

    def test_safe_json_parse_invalid(self):
        engine = DebateEngineV2()
        raw = "not json"
        default = {"default": True}
        parsed = engine._safe_json_parse(raw, default)
        assert parsed == default

    def test_summarize_evidence(self):
        engine = DebateEngineV2()
        evidence = EvidencePacket(
            session_id="s_1",
            transcript_chunks=[
                TranscriptChunk(chunk_id="t1", session_id="s_1", speaker="PM", text="讨论产品规划"),
                TranscriptChunk(chunk_id="t2", session_id="s_1", speaker="Dev", text="技术方案评估"),
            ],
        )
        summary = engine._summarize_evidence(evidence)
        assert "PM" in summary
        assert "Dev" in summary

    @pytest.mark.asyncio
    async def test_run_group_single_agent(self):
        _ensure_session("s_1")
        engine = DebateEngineV2()
        agent = AgentManifest(id="pm", name="产品经理", role="product")
        group = AgentGroup(
            group_id=f"g_{uuid.uuid4().hex[:8]}",
            group_name="产品组",
            topic="MVP规划",
            agents=[agent],
        )
        evidence = EvidencePacket(session_id="s_1")
        result = await engine._run_group("s_1", evidence, group, None)

        assert len(result.steps) == 4  # 4 steps for 1 agent
        assert result.steps[0].step_number == 1
        assert result.steps[0].step_type == DebateStepType.STATEMENT
        assert result.steps[1].step_type == DebateStepType.CHALLENGE
        assert result.steps[2].step_type == DebateStepType.NEW_PERSPECTIVE
        assert result.steps[3].step_type == DebateStepType.CONSENSUS
        assert len(result.snapshots) == 1

    @pytest.mark.asyncio
    async def test_run_group_multiple_agents(self):
        _ensure_session("s_2")
        engine = DebateEngineV2()
        agents = [
            AgentManifest(id="pm", name="产品经理", role="product"),
            AgentManifest(id="arch", name="架构师", role="tech"),
        ]
        group = AgentGroup(
            group_id=f"g_{uuid.uuid4().hex[:8]}",
            group_name="技术产品组",
            topic="架构评审",
            agents=agents,
        )
        evidence = EvidencePacket(session_id="s_2")
        result = await engine._run_group("s_2", evidence, group, None)

        # 2 agents × 4 steps = 8 steps
        assert len(result.steps) == 8
        assert len(result.snapshots) == 1

    @pytest.mark.asyncio
    async def test_full_run(self):
        _ensure_session("s_full")
        engine = DebateEngineV2()
        agents = [
            AgentManifest(id="pm", name="产品经理", role="product"),
            AgentManifest(id="arch", name="架构师", role="tech"),
        ]
        groups = [
            AgentGroup(group_id=f"g_{uuid.uuid4().hex[:8]}", group_name="组1", topic="话题1", agents=agents),
        ]
        evidence = EvidencePacket(session_id="s_full")
        result = await engine.run("s_full", evidence, groups, None)

        assert result.session_id == "s_full"
        assert len(result.steps) == 8
        assert len(result.snapshots) >= 1
        assert "agreement_level" in result.final_consensus

    @pytest.mark.asyncio
    async def test_handle_interrupt(self):
        _ensure_session("s_int")
        engine = DebateEngineV2()
        interrupt = await engine.handle_interrupt(
            session_id="s_int",
            user_id="user_1",
            interrupt_type="question",
            content="为什么不做 A/B 测试？",
            target_agent_id="pm",
        )
        assert interrupt.session_id == "s_int"
        assert interrupt.interrupt_type == "question"
        assert interrupt.target_agent_id == "pm"

    def test_compute_group_consensus(self):
        engine = DebateEngineV2()
        step4 = [
            DebateStep(
                step_id="s1", group_id="g1", step_number=4, agent_id="pm",
                step_type=DebateStepType.CONSENSUS, content="可行",
                content_struct={"consensus_assessment": {"fatal_flaws": []}},
                confidence=0.8,
            ),
            DebateStep(
                step_id="s2", group_id="g1", step_number=4, agent_id="arch",
                step_type=DebateStepType.CONSENSUS, content="有风险",
                content_struct={"consensus_assessment": {"fatal_flaws": []}},
                confidence=0.7,
            ),
        ]
        snap = engine._compute_group_consensus("s_1", "g1", step4)
        assert snap is not None
        assert snap.agreement_level == AgreementLevel.PARTIAL_CONSENSUS
        assert "pm" in snap.dimension_scores
        assert "arch" in snap.dimension_scores


class TestDebateV2Database:
    def test_insert_and_get_debate_group(self):
        _ensure_session("s_1")
        gid = f"g_{uuid.uuid4().hex[:8]}"
        db.insert_debate_group(gid, "s_1", "测试组", "话题", ["pm", "arch"])
        groups = db.get_debate_groups("s_1")
        ids = [g["group_id"] for g in groups]
        assert gid in ids

    def test_update_group_status(self):
        _ensure_session("s_2")
        gid = f"g_{uuid.uuid4().hex[:8]}"
        db.insert_debate_group(gid, "s_2", "测试组", "话题", ["pm"])
        db.update_debate_group_status(gid, "completed")
        groups = db.get_debate_groups("s_2")
        g = next(x for x in groups if x["group_id"] == gid)
        assert g["status"] == "completed"

    def test_insert_and_get_debate_step(self):
        _ensure_session("s_3")
        gid = f"g_{uuid.uuid4().hex[:8]}"
        db.insert_debate_group(gid, "s_3", "组", "话题", ["pm"])
        sid = f"st_{uuid.uuid4().hex[:8]}"
        db.insert_debate_step(sid, gid, 1, "pm", "statement", "测试内容",
                              content_struct={"key": "value"}, confidence=0.9)
        steps = db.get_debate_steps(gid)
        assert len(steps) >= 1
        step = next(s for s in steps if s["step_id"] == sid)
        assert step["content_struct"]["key"] == "value"
        assert step["confidence"] == 0.9

    def test_debate_events(self):
        _ensure_session("s_ev")
        db.reset_sequence("s_ev")
        eid = db.insert_debate_event("s_ev", "round_start", None, "辩论开始")
        assert eid >= 1
        events = db.get_debate_events("s_ev")
        assert len(events) >= 1
        assert events[0]["event_type"] == "round_start"

    def test_user_interrupts(self):
        _ensure_session("s_4")
        iid = f"int_{uuid.uuid4().hex[:8]}"
        db.insert_user_interrupt(iid, "s_4", "user_1", "question", "pm", "为什么？", datetime.now(timezone.utc).isoformat())
        ints = db.get_user_interrupts("s_4")
        assert len(ints) >= 1
        assert ints[0]["interrupt_type"] == "question"

    def test_consensus_snapshots(self):
        _ensure_session("s_5")
        sid = f"snap_{uuid.uuid4().hex[:8]}"
        db.insert_consensus_snapshot(sid, "s_5", "g1", None, {"产品": 80}, "partial_consensus", "部分共识")
        snaps = db.get_consensus_snapshots("s_5")
        assert len(snaps) >= 1
        assert snaps[0]["dimension_scores"]["产品"] == 80
