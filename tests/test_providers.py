"""Provider 层测试：多协议 LLM Provider 架构。

覆盖：
- MockProvider 基本行为
- OpenAIProvider 创建与 chat（mock client）
- AnthropicProvider 创建与 chat（mock client）
- ProviderRouter 路由、缓存、fallback
- ProviderAdapter / get_provider 向后兼容
- 错误处理（无效 model_ref、缺失 API key）
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from roundtable.providers import (
    BaseLLMProvider,
    MockProvider,
    OpenAIProvider,
    AnthropicProvider,
    ProviderRouter,
    ProviderAdapter,
    get_provider,
    build_agent_prompt,
    parse_agent_response,
    build_debate_prompt,
)
from roundtable.models import EvidencePacket, SkillManifest, ClaimType
from roundtable.config import ConfigManager


# ══════════════════════════════════════════════════════════════
# MockProvider
# ══════════════════════════════════════════════════════════════

class TestMockProvider:
    @pytest.mark.asyncio
    async def test_chat_returns_mock_json(self):
        p = MockProvider()
        result = await p.chat("sys", "hello world")
        parsed = json.loads(result)
        assert "Mock analysis of" in parsed["summary"]
        assert parsed["claims"] == []

    @pytest.mark.asyncio
    async def test_chat_ignores_max_tokens_and_temperature(self):
        p = MockProvider()
        result = await p.chat("sys", "test", max_tokens=1, temperature=0.0)
        parsed = json.loads(result)
        assert "test" in parsed["summary"]


# ══════════════════════════════════════════════════════════════
# OpenAIProvider
# ══════════════════════════════════════════════════════════════

class TestOpenAIProvider:
    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="API key required"):
            OpenAIProvider("deepseek", "deepseek-chat", api_key="")

    def test_init_creates_client(self):
        p = OpenAIProvider("deepseek", "deepseek-chat", api_key="sk-test")
        assert p.provider_id == "deepseek"
        assert p.model_id == "deepseek-chat"
        assert p._client is not None

    @pytest.mark.asyncio
    async def test_chat_success(self):
        p = OpenAIProvider("deepseek", "deepseek-chat", api_key="sk-test")

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Hello from DeepSeek"
        mock_response.usage = MagicMock(total_tokens=42)
        p._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await p.chat("sys", "hello")
        assert result == "Hello from DeepSeek"
        p._client.chat.completions.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_retries_then_fails(self):
        p = OpenAIProvider("deepseek", "deepseek-chat", api_key="sk-test")
        p._client.chat.completions.create = AsyncMock(
            side_effect=Exception("API down")
        )

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            await p.chat("sys", "hello")
        assert p._client.chat.completions.create.await_count == 3

    @pytest.mark.asyncio
    async def test_chat_returns_empty_string_when_no_content(self):
        p = OpenAIProvider("deepseek", "deepseek-chat", api_key="sk-test")
        mock_response = MagicMock()
        mock_response.choices[0].message.content = None
        mock_response.usage = None
        p._client.chat.completions.create = AsyncMock(return_value=mock_response)

        result = await p.chat("sys", "hello")
        assert result == ""


# ══════════════════════════════════════════════════════════════
# AnthropicProvider
# ══════════════════════════════════════════════════════════════

class TestAnthropicProvider:
    def test_init_requires_anthropic_sdk(self):
        with patch("roundtable.providers.AsyncAnthropic", None):
            with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
                AnthropicProvider("anthropic", "claude-sonnet", api_key="sk-test")

    def test_init_requires_api_key(self):
        with patch("roundtable.providers.AsyncAnthropic"):
            with pytest.raises(ValueError, match="API key required"):
                AnthropicProvider("anthropic", "claude-sonnet", api_key="")

    @pytest.mark.asyncio
    async def test_chat_success(self):
        with patch("roundtable.providers.AsyncAnthropic") as MockAnthropic:
            p = AnthropicProvider("anthropic", "claude-sonnet", api_key="sk-test")

            mock_block = MagicMock()
            mock_block.type = "text"
            mock_block.text = "Hello from Claude"

            mock_response = MagicMock()
            mock_response.content = [mock_block]
            mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
            p._client.messages.create = AsyncMock(return_value=mock_response)

            result = await p.chat("sys", "hello")
            assert result == "Hello from Claude"
            p._client.messages.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_filters_non_text_blocks(self):
        with patch("roundtable.providers.AsyncAnthropic"):
            p = AnthropicProvider("anthropic", "claude-sonnet", api_key="sk-test")

            text_block = MagicMock(type="text", text="hello")
            tool_block = MagicMock(type="tool_use", text="tool")
            mock_response = MagicMock(content=[text_block, tool_block])
            mock_response.usage = None
            p._client.messages.create = AsyncMock(return_value=mock_response)

            result = await p.chat("sys", "hi")
            assert result == "hello"

    @pytest.mark.asyncio
    async def test_chat_retries_then_fails(self):
        with patch("roundtable.providers.AsyncAnthropic"):
            p = AnthropicProvider("anthropic", "claude-sonnet", api_key="sk-test")
            p._client.messages.create = AsyncMock(side_effect=Exception("API down"))

            with pytest.raises(RuntimeError, match="failed after 3 attempts"):
                await p.chat("sys", "hello")
            assert p._client.messages.create.await_count == 3


# ══════════════════════════════════════════════════════════════
# ProviderRouter
# ══════════════════════════════════════════════════════════════

class TestProviderRouter:
    def setup_method(self):
        ProviderRouter.reset()
        ConfigManager.reset()

    def teardown_method(self):
        ProviderRouter.reset()
        ConfigManager.reset()

    def test_singleton(self):
        r1 = ProviderRouter.get_instance()
        r2 = ProviderRouter.get_instance()
        assert r1 is r2

    def test_get_mock(self):
        router = ProviderRouter.get_instance()
        p = router.get("mock")
        assert isinstance(p, MockProvider)

    def test_get_empty_returns_mock(self):
        router = ProviderRouter.get_instance()
        p = router.get("")
        assert isinstance(p, MockProvider)

    def test_get_invalid_format_raises(self):
        router = ProviderRouter.get_instance()
        with pytest.raises(ValueError, match="Invalid model reference"):
            router.get("invalid-no-slash")

    def test_get_caches_same_instance(self):
        router = ProviderRouter.get_instance()
        p1 = router.get("mock")
        p2 = router.get("mock")
        assert p1 is p2

    def test_get_fallback_when_not_in_config(self):
        """当 model_ref 不在 config 中时，fallback 到 mock。"""
        router = ProviderRouter.get_instance()
        p = router.get("unknown/model123")
        assert isinstance(p, MockProvider)
        assert p.provider_id == "unknown"

    def test_get_opensai_provider_from_config(self):
        """从 config 解析 openai 协议 provider。"""
        from pathlib import Path
        import tempfile

        yaml_content = """
providers:
  test_openai:
    protocol: openai
    base_url: https://api.test.com/v1
    api_key: sk-testkey
    timeout: 30
    models:
      - id: gpt-4
agent_models:
  pm: test_openai/gpt-4
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(yaml_content, encoding="utf-8")
            ConfigManager.reset()
            cm = ConfigManager(config_path=config_path)
            ConfigManager._instance = cm  # ensure get() returns this instance

            ProviderRouter.reset()
            router = ProviderRouter.get_instance()
            p = router.get("test_openai/gpt-4")
            assert isinstance(p, OpenAIProvider)
            assert p.provider_id == "test_openai"
            assert p.model_id == "gpt-4"
            assert p._api_key == "sk-testkey"

    def test_get_anthropic_provider_from_config(self):
        """从 config 解析 anthropic 协议 provider。"""
        from pathlib import Path
        import tempfile

        yaml_content = """
providers:
  test_anthropic:
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: sk-ant-test
    timeout: 45
    models:
      - id: claude-sonnet
agent_models:
  architect: test_anthropic/claude-sonnet
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(yaml_content, encoding="utf-8")
            ConfigManager.reset()
            cm = ConfigManager(config_path=config_path)
            ConfigManager._instance = cm

            ProviderRouter.reset()
            with patch("roundtable.providers.AsyncAnthropic"):
                router = ProviderRouter.get_instance()
                p = router.get("test_anthropic/claude-sonnet")
                assert isinstance(p, AnthropicProvider)
                assert p.provider_id == "test_anthropic"
                assert p.model_id == "claude-sonnet"

    def test_get_default_returns_first_agent_model(self):
        from pathlib import Path
        import tempfile

        yaml_content = """
providers:
  deepseek:
    protocol: openai
    base_url: https://api.deepseek.com/v1
    api_key: sk-ds
    timeout: 60
    models:
      - id: deepseek-chat
agent_models:
  pm: deepseek/deepseek-chat
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(yaml_content, encoding="utf-8")
            ConfigManager.reset()
            cm = ConfigManager(config_path=config_path)
            ConfigManager._instance = cm

            ProviderRouter.reset()
            router = ProviderRouter.get_instance()
            p = router.get_default()
            assert isinstance(p, OpenAIProvider)
            assert p.model_id == "deepseek-chat"

    def test_get_default_returns_mock_when_no_agents(self):
        ConfigManager.reset()
        from pathlib import Path
        import tempfile

        yaml_content = "\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(yaml_content, encoding="utf-8")
            cm = ConfigManager(config_path=config_path)
            ConfigManager._instance = cm

            ProviderRouter.reset()
            router = ProviderRouter.get_instance()
            p = router.get_default()
            assert isinstance(p, MockProvider)

    def test_reset_clears_cache(self):
        router = ProviderRouter.get_instance()
        router.get("mock")
        assert "mock" in router._cache
        ProviderRouter.reset()
        router2 = ProviderRouter.get_instance()
        assert "mock" not in router2._cache

    def test_config_reload_invalidates_router_cache(self):
        from pathlib import Path
        import tempfile

        yaml_v1 = """
providers:
  deepseek:
    protocol: openai
    base_url: https://api.deepseek.com/v1
    api_key: sk-old
    timeout: 60
    models:
      - id: deepseek-chat
agent_models:
  pm: deepseek/deepseek-chat
"""
        yaml_v2 = yaml_v1.replace("sk-old", "sk-new")

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "providers.yaml"
            config_path.write_text(yaml_v1, encoding="utf-8")

            ConfigManager.reset()
            ProviderRouter.reset()
            cm = ConfigManager(config_path=config_path)
            ConfigManager._instance = cm

            router = ProviderRouter.get_instance()
            p1 = router.get("deepseek/deepseek-chat")
            assert isinstance(p1, OpenAIProvider)
            assert p1._api_key == "sk-old"

            config_path.write_text(yaml_v2, encoding="utf-8")
            cm.reload()

            p2 = router.get("deepseek/deepseek-chat")
            assert isinstance(p2, OpenAIProvider)
            assert p2._api_key == "sk-new"
            assert p2 is not p1


# ══════════════════════════════════════════════════════════════
# Backward Compatibility
# ══════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    def test_provider_adapter_is_openai_provider(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=False):
            pa = ProviderAdapter()
            assert isinstance(pa, OpenAIProvider)
            assert pa.model_id == "deepseek-chat"

    def test_get_provider_returns_provider_adapter(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}, clear=False):
            p = get_provider()
            assert isinstance(p, ProviderAdapter)
            assert p.model_id == "deepseek-chat"

    def test_provider_adapter_accepts_custom_params(self):
        pa = ProviderAdapter(provider="custom", api_key="sk-custom", model="gpt-4")
        assert pa.provider_id == "custom"
        assert pa.model_id == "gpt-4"
        assert pa._api_key == "sk-custom"


# ══════════════════════════════════════════════════════════════
# Prompt Builders (unchanged, regression coverage)
# ══════════════════════════════════════════════════════════════

class TestPromptBuilders:
    def test_build_agent_prompt(self):
        skill = SkillManifest(
            skill_id="pm",
            name="产品经理",
            role="产品分析专家",
            allowed_domains=["产品策略"],
            forbidden=["断言技术架构"],
            allowed_claim_types=["inference", "recommendation"],
        )
        evidence = EvidencePacket(
            session_id="s_001",
            mode="meeting",
            transcript_chunks=[],
        )
        system, user = build_agent_prompt(skill, evidence)
        assert "产品经理" in system
        assert "产品策略" in system
        assert "断言技术架构" in system
        assert "s_001" in user

    def test_parse_agent_response_valid_json(self):
        raw = '{"summary": "ok", "claims": [], "open_questions": [], "recommended_next_actions": []}'
        result, err = parse_agent_response(raw, "pm", None)
        assert err is None
        assert result["summary"] == "ok"

    def test_parse_agent_response_with_markdown_fence(self):
        raw = '```json\n{"summary": "ok", "claims": []}\n```'
        result, err = parse_agent_response(raw, "pm", None)
        assert err is None
        assert result["summary"] == "ok"

    def test_parse_agent_response_missing_fields_fills_defaults(self):
        raw = '{"summary": "only summary"}'
        result, err = parse_agent_response(raw, "pm", None)
        assert err is None
        assert result["claims"] == []
        assert result["open_questions"] == []
        assert result["recommended_next_actions"] == []

    def test_parse_agent_response_invalid_returns_error(self):
        raw = "not json at all"
        result, err = parse_agent_response(raw, "pm", None)
        assert result is None
        assert err is not None
        assert "无法从响应中提取 JSON" in err or "JSON 解析失败" in err

    def test_build_debate_prompt(self):
        skill = SkillManifest(
            skill_id="pm",
            name="产品经理",
            role="产品分析专家",
            allowed_domains=["产品策略"],
            forbidden=["断言技术架构"],
            allowed_claim_types=["inference"],
        )
        evidence = EvidencePacket(
            session_id="s_001",
            mode="meeting",
            transcript_chunks=[],
        )
        system, user = build_debate_prompt(skill, evidence, [])
        assert "辩论" in system
        assert "第二轮" in system


# ══════════════════════════════════════════════════════════════
# BaseLLMProvider ABC
# ══════════════════════════════════════════════════════════════

class TestBaseLLMProvider:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseLLMProvider("test", "model")
