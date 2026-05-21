"""Phase 2: Skills + Agents + Orchestrator tests."""

import pytest
from roundtable.skills import load_skill, list_skills, register_skill, BUILTIN_SKILLS
from roundtable.models import SkillManifest, EvidencePacket
from roundtable.agents import (
    ProductManager, Architect, ProjectManager, BusinessAnalyst, SupervisorAgent,
)
from roundtable.orchestrator import run_orchestrator


class TestSkillRegistry:
    def test_list_skills_returns_5(self):
        assert len(list_skills()) == 5

    def test_load_valid_skill(self):
        skill = load_skill("architect")
        assert skill.name == "架构师"
        assert "architecture" in skill.allowed_domains

    def test_load_invalid_skill_raises(self):
        with pytest.raises(KeyError):
            load_skill("nonexistent")

    def test_register_new_skill(self):
        m = SkillManifest(skill_id="test_skill", name="Test")
        register_skill(m)
        assert "test_skill" in list_skills()


class TestProductManager:
    def test_analyze_returns_review(self):
        agent = ProductManager()
        chunk_data = [
            {"chunk_id": "t_0", "session_id": "s_1", "speaker": "张", "text": "我们决定先做文本导入。"},
        ]
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(**c) for c in chunk_data]
        evidence = EvidencePacket(session_id="s_1", transcript_chunks=chunks)
        review = agent.analyze(evidence)
        assert review.agent_id == "product_manager"
        assert len(review.claims) >= 1


class TestArchitect:
    def test_analyze_returns_review(self):
        agent = Architect()
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_2", speaker="李", text="后端协议先定死。")]
        evidence = EvidencePacket(session_id="s_2", transcript_chunks=chunks)
        review = agent.analyze(evidence)
        assert review.agent_id == "architect"
        assert len(review.claims) >= 1


class TestOrchestrator:
    def test_run_3_agents(self):
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_3", speaker="张", text="MVP先做文本导入。")]
        evidence = EvidencePacket(session_id="s_3", transcript_chunks=chunks)
        reviews = run_orchestrator(evidence, agent_count=3)
        assert len(reviews) == 3
        for r in reviews:
            assert r.agent_id in ("product_manager", "architect", "project_manager")

    def test_run_all_5_agents(self):
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_4", speaker="王", text="前端交互像游戏组队。")]
        evidence = EvidencePacket(session_id="s_4", transcript_chunks=chunks)
        reviews = run_orchestrator(evidence, agent_count=5)
        assert len(reviews) == 5

    def test_max_agents_capped_at_5(self):
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_5", speaker="A", text="test")]
        evidence = EvidencePacket(session_id="s_5", transcript_chunks=chunks)
        reviews = run_orchestrator(evidence, agent_count=10)
        assert len(reviews) == 5  # capped
