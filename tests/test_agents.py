"""Phase 2: Skills + Agents + Orchestrator tests."""

import os
from pathlib import Path

import pytest
from roundtable.skills import load_skill, list_skills, register_skill, BUILTIN_SKILLS
from roundtable.models import SkillManifest, EvidencePacket
from roundtable.agents import (
    ProductManager, Architect, ProjectManager, BusinessAnalyst, SupervisorAgent,
)
from roundtable.orchestrator import run_orchestrator
from roundtable.config import ConfigManager
from roundtable.providers import ProviderRouter, OpenAIProvider


def setup_module(module):
    """Ensure no real API keys leak into agent auto-resolution during tests."""
    os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)


def teardown_module(module):
    ConfigManager.reset()
    ProviderRouter.reset()


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

    def test_finds_english_signals(self):
        """Mock agent should detect English decision keywords."""
        from roundtable.models import TranscriptChunk
        from roundtable.evidence import build_evidence_packet
        segments = [{"speaker": "PM", "text": "We decide to focus on MVP first and defer ASR to phase 2."}]
        evidence = build_evidence_packet("test", "meeting", segments)
        agent = ProductManager()
        review = agent.analyze(evidence)
        assert len(review.claims) >= 1
        assert "0 product signals" not in review.summary.lower()

    def test_fallback_extracts_topics(self):
        """Even without keywords, should extract at least one signal from input."""
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_1", speaker="A", text="Random discussion about weather.")]
        evidence = EvidencePacket(session_id="s_1", transcript_chunks=chunks)
        agent = ProductManager()
        review = agent.analyze(evidence)
        assert len(review.claims) >= 1

    def test_recommendation_evidence_ids_use_chunk_ids(self):
        from roundtable.models import TranscriptChunk
        chunks = [
            TranscriptChunk(
                chunk_id="t_0", session_id="s_1", speaker="张", text="我们决定先做文本导入。",
            ),
        ]
        evidence = EvidencePacket(session_id="s_1", transcript_chunks=chunks)
        agent = ProductManager()
        review = agent.analyze(evidence)
        recommendation_claims = [c for c in review.claims if c.claim_type.value == "recommendation"]
        assert recommendation_claims
        assert recommendation_claims[0].evidence_ids == ["t_0"]


class TestArchitect:
    def test_analyze_returns_review(self):
        agent = Architect()
        from roundtable.models import TranscriptChunk
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s_2", speaker="李", text="后端协议先定死。")]
        evidence = EvidencePacket(session_id="s_2", transcript_chunks=chunks)
        review = agent.analyze(evidence)
        assert review.agent_id == "architect"
        assert len(review.claims) >= 1

    def test_finds_english_tech_keywords(self):
        """Mock architect should detect English tech keywords."""
        from roundtable.evidence import build_evidence_packet
        segments = [{"speaker": "Dev", "text": "The backend protocol and database design must be locked down first."}]
        evidence = build_evidence_packet("test", "meeting", segments)
        agent = Architect()
        review = agent.analyze(evidence)
        assert len(review.claims) >= 1
        assert any("protocol" in c.content.lower() or "database" in c.content.lower() for c in review.claims)


class TestProjectManager:
    def test_analyzes_input(self):
        """ProjectManager mock should detect timeline signals from input."""
        from roundtable.evidence import build_evidence_packet
        segments = [{"speaker": "PM", "text": "We plan to deliver MVP in 2 weeks, sprint-based."}]
        evidence = build_evidence_packet("test", "meeting", segments)
        agent = ProjectManager()
        review = agent.analyze(evidence)
        assert len(review.claims) >= 1
        assert any("sprint" in c.content.lower() or "week" in c.content.lower()
                    for c in review.claims if c.claim_type.value == "fact")


class TestBusinessAnalyst:
    def test_analyzes_input(self):
        """BusinessAnalyst mock should detect business keywords from input."""
        from roundtable.evidence import build_evidence_packet
        segments = [{"speaker": "PM", "text": "Our target users are product managers who need structured analysis."}]
        evidence = build_evidence_packet("test", "meeting", segments)
        agent = BusinessAnalyst()
        review = agent.analyze(evidence)
        assert len(review.claims) >= 1
        assert "0" not in review.summary or "signal" not in review.summary.lower()


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

    def test_agents_auto_resolve_provider_when_provider_omitted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

        yaml_content = """
providers:
  deepseek:
    protocol: openai
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    timeout: 30
    models:
      - id: deepseek-chat
agent_models:
  product_manager: deepseek/deepseek-chat
  architect: deepseek/deepseek-chat
  project_manager: deepseek/deepseek-chat
"""
        config_path = tmp_path / "providers.yaml"
        config_path.write_text(yaml_content, encoding="utf-8")

        ConfigManager.reset()
        ProviderRouter.reset()
        ConfigManager._instance = ConfigManager(config_path=config_path)

        from roundtable.orchestrator import create_agents

        agents = create_agents(agent_count=3)
        assert len(agents) == 3
        assert all(agent.provider is not None for agent in agents)
        assert all(isinstance(agent.provider, OpenAIProvider) for agent in agents)

        ConfigManager.reset()
        ProviderRouter.reset()
