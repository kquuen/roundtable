"""Service layer tests."""

from __future__ import annotations

from roundtable.services import RoundtableService
from roundtable.config import ConfigManager
from roundtable.providers import ProviderRouter


def test_run_pipeline_sync_available():
    service = RoundtableService()
    result = service.run_pipeline_sync(
        session_id="s_sync",
        segments=[{"speaker": "A", "text": "hello"}],
        mode="meeting",
        title="sync test",
        agent_count=2,
    )
    assert result.session_id == "s_sync"
    assert result.mode == "mock"
    assert "# 圆桌会议审查报告" in result.report


def test_run_pipeline_auto_provider_mode_llm_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    async def _fake_chat(self, system_prompt, user_message, max_tokens=2000, temperature=0.7):
        return (
            '{"summary":"ok","claims":[{"content":"x","claim_type":"fact","confidence":0.9,'
            '"evidence_text":"MVP"}],"open_questions":[],"recommended_next_actions":[]}'
        )
    monkeypatch.setattr("roundtable.providers.OpenAIProvider.chat", _fake_chat)

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
    monkeypatch.setattr("roundtable.config.ConfigManager._instance", ConfigManager(config_path=config_path))

    service = RoundtableService()
    result = service.run_pipeline_sync(
        session_id="s_auto_llm",
        segments=[{"speaker": "A", "text": "We should ship MVP first."}],
        mode="meeting",
        title="auto provider",
        agent_count=3,
        lang="en",
    )

    assert result.mode == "llm"

    ConfigManager.reset()
    ProviderRouter.reset()


def test_run_pipeline_auto_provider_mode_mock_when_no_mapping(monkeypatch):
    from unittest.mock import AsyncMock

    ConfigManager.reset()
    ProviderRouter.reset()
    monkeypatch.setattr("roundtable.config.ConfigManager._instance", ConfigManager())
    monkeypatch.setattr(
        "roundtable.orchestrator.run_orchestrator_async",
        AsyncMock(return_value=([], {"llm_attempted": False})),
    )

    service = RoundtableService()
    result = service.run_pipeline_sync(
        session_id="s_auto_mock",
        segments=[{"speaker": "A", "text": "hello"}],
        mode="meeting",
        title="auto mock",
        agent_count=1,
        lang="en",
    )
    assert result.mode == "mock"

    ConfigManager.reset()
    ProviderRouter.reset()
