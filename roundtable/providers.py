"""Phase 5: LLM Provider Adapter.

Supports DeepSeek API (OpenAI-compatible) and mock mode for testing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Optional

from openai import AsyncOpenAI

from roundtable.models import EvidencePacket, SkillManifest

logger = logging.getLogger("roundtable.providers")


class ProviderAdapter:
    """LLM provider adapter with DeepSeek backend and mock fallback."""

    # DeepSeek OpenAI-compatible endpoint
    DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self,
        provider: str = "deepseek",
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.provider = provider
        self.model = model or self.DEFAULT_MODEL
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.budget = None  # TokenBudget injected by service layer (Bug 7)

        if provider == "deepseek":
            if not self._api_key:
                raise ValueError(
                    "DEEPSEEK_API_KEY not set. "
                    "Export it: $env:DEEPSEEK_API_KEY='sk-...' (PowerShell) "
                    "or export DEEPSEEK_API_KEY='sk-...' (bash)."
                )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self.DEEPSEEK_BASE_URL,
            )

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        """Send a chat completion request with retry logic.

        Returns the raw text response from the LLM.
        """
        if self.provider == "mock":
            return self._mock_response(user_message)

        last_error: str | None = None
        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                content = response.choices[0].message.content

                # Consume real token count from API response (Bug 7)
                if self.budget is not None and response.usage is not None:
                    self.budget.consume(response.usage.total_tokens, "chat")

                return content or ""

            except Exception as e:
                last_error = str(e)
                logger.warning("API call attempt %d/3 failed: %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff
                continue

        raise RuntimeError(
            f"DeepSeek API call failed after 3 attempts. Last error: {last_error}"
        )

    def _mock_response(self, user_message: str) -> str:
        """Return a mock JSON response for testing."""
        return json.dumps({
            "summary": f"Mock analysis of: {user_message[:80]}...",
            "claims": [],
            "open_questions": [],
            "recommended_next_actions": [],
        }, ensure_ascii=False)


def build_agent_prompt(
    skill: SkillManifest,
    evidence: EvidencePacket,
) -> tuple[str, str]:
    """Build system + user prompts for an agent based on its SkillManifest.

    Returns:
        (system_prompt, user_message) tuple
    """
    # ── System prompt ──
    forbidden_lines = "\n".join(f"- {f}" for f in skill.forbidden) if skill.forbidden else "（无）"
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

    # ── User message ──
    transcript_text = _format_transcript(evidence)
    mode_label = "会议模式（严格证据绑定）" if evidence.mode == "meeting" else "个人圆桌模式（允许合理发散）"

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
    """Format transcript chunks into a readable text block."""
    lines = []
    for c in evidence.transcript_chunks:
        speaker = c.speaker or "未知"
        chunk_id = c.chunk_id
        text = c.text
        lines.append(f"[{chunk_id}] {speaker}：{text}")
    return "\n".join(lines) if lines else "（会议记录为空）"


def parse_agent_response(
    raw_response: str,
    agent_id: str,
    evidence: EvidencePacket,
) -> tuple[dict | None, str | None]:
    """Parse LLM JSON response, with robust fallback.

    Returns:
        (parsed_dict, error_message). One of them is always None.
    """
    # Clean common LLM formatting quirks
    cleaned = raw_response.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)

    # Try to extract the first JSON object
    try:
        # First try direct parse
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object with regex
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if not match:
            return None, f"无法从响应中提取 JSON 对象。原始响应: {raw_response[:200]}"
        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError as e:
            return None, f"JSON 解析失败: {e}。原始响应: {raw_response[:200]}"

    # Validate required fields
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
    """构建辩论 Round 2 的 system + user prompt。

    Round 2 的核心差异：Agent 看到其他专家的 Round 1 结论，
    需要产出质疑（disagree）、同意（agree）、或延伸（extend）。
    """
    from roundtable.models import AgentReview as AR

    # System prompt
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

    # User message: transcript + peer reviews
    chunks_text = "\n".join(
        f"[{c.speaker}] {c.text}" for c in evidence.transcript_chunks
    )

    peer_texts = []
    for pr in peer_reviews:
        if isinstance(pr, AR):
            peer_texts.append(f"\n### {pr.agent_id} 的首轮观点\n{pr.summary}")
            for c in pr.claims:
                peer_texts.append(f"- [{c.claim_id}] ({c.claim_type.value if hasattr(c.claim_type, 'value') else c.claim_type}) {c.content}")
        else:
            peer_texts.append(str(pr))

    user = f"""=== 会议原文 ===
{chunks_text}

=== 其他专家的首轮观点 ===
{''.join(peer_texts)}

请输出你的第二轮辩论观点（JSON 格式）："""

    return system, user


def get_provider(
    provider: str = "deepseek",
    api_key: str | None = None,
    model: str | None = None,
) -> ProviderAdapter:
    """Factory: return a ProviderAdapter for the given provider name."""
    return ProviderAdapter(provider=provider, api_key=api_key, model=model)
