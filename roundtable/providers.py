"""Universal LLM Provider Layer — multi-protocol, config-driven.

Supports:
  - OpenAI-compatible (DeepSeek, OpenAI, SiliconFlow, OneAPI, local vLLM, etc.)
  - Anthropic Claude Messages API
  - Mock mode for offline development and testing

Routing:  skill_id → ConfigManager → ProviderRouter → Provider instance
Usage:    ProviderRouter.get_instance().get("deepseek/deepseek-chat")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from typing import Optional

from openai import AsyncOpenAI

try:
    from anthropic import AsyncAnthropic
except ImportError:  # pragma: no cover
    AsyncAnthropic = None  # type: ignore[misc,assignment]

from roundtable.models import EvidencePacket, SkillManifest

logger = logging.getLogger("roundtable.providers")


# ══════════════════════════════════════════════════════════════
# Abstract Base
# ══════════════════════════════════════════════════════════════

class BaseLLMProvider(ABC):
    """Unified interface for all LLM providers.

    Implementations handle protocol-specific details (OpenAI, Anthropic, etc.)
    while exposing a single ``chat()`` method to callers.
    """

    def __init__(self, provider_id: str, model_id: str, api_key: str | None = None):
        self.provider_id = provider_id
        self.model_id = model_id
        self._api_key = api_key
        self.budget = None  # TokenBudget injected by service layer

    @abstractmethod
    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        """Send a chat completion request and return the raw text response."""

    def _mock_response(self, user_message: str) -> str:
        """Return a mock JSON response for testing."""
        return json.dumps(
            {
                "summary": f"Mock analysis of: {user_message[:80]}...",
                "claims": [],
                "open_questions": [],
                "recommended_next_actions": [],
            },
            ensure_ascii=False,
        )


# ══════════════════════════════════════════════════════════════
# OpenAI-compatible Provider
# ══════════════════════════════════════════════════════════════

class OpenAIProvider(BaseLLMProvider):
    """Provider for any OpenAI-compatible API endpoint.

    Covers: DeepSeek, OpenAI, SiliconFlow, OneAPI, local vLLM, etc.
    """

    def __init__(
        self,
        provider_id: str,
        model_id: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ):
        super().__init__(provider_id, model_id, api_key)
        if not api_key:
            raise ValueError(
                f"API key required for provider '{provider_id}'. "
                f"Check config/providers.yaml or set the corresponding env var."
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        last_error: str | None = None
        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model_id,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                content = response.choices[0].message.content

                # TokenBudget tracking
                if self.budget is not None and response.usage is not None:
                    self.budget.consume(response.usage.total_tokens, "chat")

                return content or ""

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "[%s/%s] API call attempt %d/3 failed: %s",
                    self.provider_id,
                    self.model_id,
                    attempt + 1,
                    e,
                )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue

        raise RuntimeError(
            f"{self.provider_id}/{self.model_id} API call failed after 3 attempts. "
            f"Last error: {last_error}"
        )


# ══════════════════════════════════════════════════════════════
# Anthropic Claude Provider
# ══════════════════════════════════════════════════════════════

class AnthropicProvider(BaseLLMProvider):
    """Provider for Anthropic Claude Messages API."""

    def __init__(
        self,
        provider_id: str,
        model_id: str,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 60.0,
    ):
        super().__init__(provider_id, model_id, api_key)
        if AsyncAnthropic is None:
            raise RuntimeError(
                "anthropic SDK not installed. Run: pip install anthropic>=0.30.0"
            )
        if not api_key:
            raise ValueError(
                f"API key required for provider '{provider_id}'. "
                f"Check config/providers.yaml or set ANTHROPIC_API_KEY."
            )
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        last_error: str | None = None
        for attempt in range(3):
            try:
                response = await self._client.messages.create(
                    model=self.model_id,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                # Extract text from content blocks
                text_parts = []
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        text_parts.append(block.text)
                result = "\n".join(text_parts)

                # TokenBudget tracking (input_tokens + output_tokens)
                if self.budget is not None:
                    total = (
                        getattr(response.usage, "input_tokens", 0)
                        + getattr(response.usage, "output_tokens", 0)
                    )
                    if total:
                        self.budget.consume(total, "chat")

                return result

            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "[%s/%s] API call attempt %d/3 failed: %s",
                    self.provider_id,
                    self.model_id,
                    attempt + 1,
                    e,
                )
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
                continue

        raise RuntimeError(
            f"{self.provider_id}/{self.model_id} API call failed after 3 attempts. "
            f"Last error: {last_error}"
        )


# ══════════════════════════════════════════════════════════════
# Mock Provider
# ══════════════════════════════════════════════════════════════

class MockProvider(BaseLLMProvider):
    """Mock provider for offline development and testing."""

    def __init__(self, provider_id: str = "mock", model_id: str = "mock"):
        super().__init__(provider_id, model_id, api_key=None)

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        return self._mock_response(user_message)


# ══════════════════════════════════════════════════════════════
# Provider Router — Factory + Cache
# ══════════════════════════════════════════════════════════════

class ProviderRouter:
    """Routes provider/model references to actual provider instances.

    Usage::

        router = ProviderRouter.get_instance()
        provider = router.get("deepseek/deepseek-chat")
        response = await provider.chat(...)
    """

    _instance: ProviderRouter | None = None

    def __init__(self):
        self._cache: dict[str, BaseLLMProvider] = {}

    @classmethod
    def get_instance(cls) -> ProviderRouter:
        if cls._instance is None:
            cls._instance = ProviderRouter()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (mainly for testing)."""
        cls._instance = None

    def clear_cache(self) -> None:
        """Clear cached provider instances while keeping the singleton."""
        self._cache.clear()

    def get(self, model_ref: str) -> BaseLLMProvider:
        """Resolve a model reference like ``deepseek/deepseek-chat`` to a provider instance.

        Uses caching: same model_ref returns the same instance.
        """
        if model_ref in self._cache:
            return self._cache[model_ref]

        provider = self._create(model_ref)
        self._cache[model_ref] = provider
        return provider

    def _create(self, model_ref: str) -> BaseLLMProvider:
        """Create a new provider instance for the given model reference."""
        if model_ref == "mock" or not model_ref:
            return MockProvider()

        if "/" not in model_ref:
            raise ValueError(
                f"Invalid model reference: '{model_ref}'. "
                f"Expected format: 'provider/model' (e.g. 'deepseek/deepseek-chat')"
            )

        provider_id, model_id = model_ref.split("/", 1)

        # Lazy import to avoid circular dependency at module load time
        from roundtable.config import ConfigManager

        config = ConfigManager.get()
        resolved = config.get_model_config(model_ref)

        if resolved is None:
            logger.warning(
                "Model reference '%s' not found in config. Falling back to mock.",
                model_ref,
            )
            return MockProvider(provider_id=provider_id, model_id=model_id)

        pconf, _ = resolved
        protocol = pconf.protocol

        if protocol == "openai":
            return OpenAIProvider(
                provider_id=provider_id,
                model_id=model_id,
                api_key=pconf.api_key,
                base_url=pconf.base_url,
                timeout=float(pconf.timeout),
            )

        if protocol == "anthropic":
            return AnthropicProvider(
                provider_id=provider_id,
                model_id=model_id,
                api_key=pconf.api_key,
                base_url=pconf.base_url,
                timeout=float(pconf.timeout),
            )

        logger.warning(
            "Unknown protocol '%s' for provider '%s'. Using mock.",
            protocol,
            provider_id,
        )
        return MockProvider(provider_id=provider_id, model_id=model_id)

    def get_default(self) -> BaseLLMProvider:
        """Return a default provider (first available from config, or mock)."""
        from roundtable.config import ConfigManager

        config = ConfigManager.get()
        agents = config.list_agent_models()
        if agents:
            first_model = next(iter(agents.values()))
            return self.get(first_model)
        return MockProvider()


# ══════════════════════════════════════════════════════════════
# Backward compatibility
# ══════════════════════════════════════════════════════════════

class ProviderAdapter(OpenAIProvider):
    """Backward-compatible alias for existing code.

    Deprecated: use ``ProviderRouter.get("deepseek/deepseek-chat")`` instead.
    """

    def __init__(
        self,
        provider: str = "deepseek",
        api_key: str | None = None,
        model: str | None = None,
    ):
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        model = model or "deepseek-chat"
        super().__init__(
            provider_id=provider,
            model_id=model,
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
        )


def get_provider(
    provider: str = "deepseek",
    api_key: str | None = None,
    model: str | None = None,
) -> ProviderAdapter:
    """Backward-compatible factory."""
    return ProviderAdapter(provider=provider, api_key=api_key, model=model)


# ══════════════════════════════════════════════════════════════
# Prompt builders (protocol-agnostic)
# ══════════════════════════════════════════════════════════════


def build_agent_prompt(
    skill: SkillManifest,
    evidence: EvidencePacket,
) -> tuple[str, str]:
    """Build system + user prompts for an agent based on its SkillManifest."""
    forbidden_lines = (
        "\n".join(f"- {f}" for f in skill.forbidden)
        if skill.forbidden
        else "（无）"
    )
    domains = ", ".join(skill.allowed_domains) if skill.allowed_domains else "通用分析"
    claim_types = ", ".join(skill.allowed_claim_types)

    system_prompt = f"""你是一位{skill.name}，角色定位是 {skill.role or skill.name}。

你的专业领域：{domains}
你可以生成的声明类型：{claim_types}

你必须严格遵守以下规则：
{forbidden_lines}

重要输出格式要求：
你必须返回一个严格的 JSON 对象，格式如下：
{{
  "summary": "你对本次分析的一句话中文总结",
  "claims": [
    {{
      "content": "具体的分析发现（中文）",
      "claim_type": "fact|inference|recommendation|extension",
      "confidence": 0.0-1.0,
      "evidence_text": "支持此声明的原文片段（必须从会议记录中原文摘录，不超过50字）"
    }}
  ],
  "open_questions": ["待澄清的问题1", "问题2"],
  "recommended_next_actions": ["建议行动1", "行动2"]
}}

注意：
- fact 类声明必须有 evidence_text 支持，且原文必须出现在会议记录中
- confidence 根据证据充分程度赋值：原文直接佐证 >0.8，间接推断 0.5-0.8，纯推测 <0.5
- 每个声明只输出一个最精确的 claim_type
- 输出纯 JSON，不要包含 markdown 代码块标记"""

    transcript_text = _format_transcript(evidence)
    mode_label = (
        "会议模式（严格证据绑定）"
        if evidence.mode == "meeting"
        else "个人圆桌模式（允许合理发散）"
    )

    user_message = f"""以下是一场会议的文字记录，请你以{skill.name}的身份进行分析。

会议模式：{mode_label}
会话ID：{evidence.session_id}

=== 会议记录 ===
{transcript_text}

=== 已知信息 ===
已知决策：{evidence.known_decisions or "（无）"}
已知行动项：{evidence.known_action_items or "（无）"}

请根据以上会议内容，以{skill.name}的专业视角进行分析，输出 JSON 格式的分析结果。只输出 JSON，不要包含任何其他文字。"""

    return system_prompt, user_message


def _format_transcript(evidence: EvidencePacket) -> str:
    lines = []
    for c in evidence.transcript_chunks:
        speaker = c.speaker or "未知"
        lines.append(f"[{c.chunk_id}] {speaker}：{c.text}")
    return "\n".join(lines) if lines else "（会议记录为空）"


def parse_agent_response(
    raw_response: str,
    agent_id: str,
    evidence: EvidencePacket,
) -> tuple[dict | None, str | None]:
    """Parse LLM JSON response, with robust fallback."""
    cleaned = raw_response.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None, f"无法从响应中提取 JSON 对象。原始响应: {raw_response[:200]}"
        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            return None, f"JSON 解析失败: {e}。原始响应: {raw_response[:200]}"

    if not isinstance(result, dict):
        return None, "响应不是 JSON 对象"

    if "summary" not in result:
        result["summary"] = f"{agent_id} 分析完成"
    if "claims" not in result:
        result["claims"] = []
    if "open_questions" not in result:
        result["open_questions"] = []
    if "recommended_next_actions" not in result:
        result["recommended_next_actions"] = []

    return result, None


def build_debate_prompt(
    skill: SkillManifest,
    evidence: EvidencePacket,
    peer_reviews: list,
) -> tuple[str, str]:
    """Build debate Round 2 system + user prompt."""
    from roundtable.models import AgentReview as AR

    system = f"""你是{skill.name}（{skill.role}），正在参加一场专家辩论。

这是一场结构化的两轮辩论。第一轮各专家已独立发表观点，现在是第二轮：你需要审视其他专家的结论。

**你的职责：**
1. 仔细阅读其他专家的观点
2. 对于你**同意的观点**：说明为什么同意，补充你的论据
3. 对于你**不同意的观点**：明确指出矛盾，给出理由和证据
4. 对于你**想延伸的观点**：在原观点基础上扩展

**领域边界：**
- 你可以讨论：{', '.join(skill.allowed_domains) if skill.allowed_domains else '所有领域'}
- 你不应越界：{'、'.join(skill.forbidden) if skill.forbidden else '无特别限制'}
- 你可以产出的声明类型：{', '.join(skill.allowed_claim_types) if skill.allowed_claim_types else 'inference, recommendation'}

**输出格式（JSON）：**
{{
  "summary": "一句话总结你对本轮辩论的立场",
  "claims": [
    {{
      "content": "你的论点内容",
      "claim_type": "inference | fact | recommendation",
      "confidence": 0.0-1.0,
      "position": "agree | disagree | extend",
      "target_claim_id": "被回应的 claim_id（可选）",
      "evidence_text": "引用原文的关键语句"
    }}
  ],
  "open_questions": [],
  "recommended_next_actions": []
}}
"""

    chunks_text = "\n".join(
        f"[{c.speaker}] {c.text}" for c in evidence.transcript_chunks
    )

    peer_texts = []
    for pr in peer_reviews:
        if isinstance(pr, AR):
            peer_texts.append(f"\n### {pr.agent_id} 的首轮观点\n{pr.summary}")
            for c in pr.claims:
                ct = (
                    c.claim_type.value
                    if hasattr(c.claim_type, "value")
                    else c.claim_type
                )
                peer_texts.append(f"- [{c.claim_id}] ({ct}) {c.content}")
        else:
            peer_texts.append(str(pr))

    user = f"""=== 会议原文 ===
{chunks_text}

=== 其他专家的首轮观点 ===
{''.join(peer_texts)}

请输出你的第二轮辩论观点（JSON 格式）："""

    return system, user
