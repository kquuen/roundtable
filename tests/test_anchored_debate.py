"""Anchored debate engine compatibility tests."""

from __future__ import annotations

import pytest

from roundtable.debate import AnchoredDebateEngine
from roundtable.models import InterviewContext, DecisionTemplate, DebateMode


class _ChatOnlyProvider:
    provider_id = "test"

    async def chat(self, system_prompt: str, user_message: str, max_tokens: int = 2000, temperature: float = 0.7):
        return "对用户愿景代言人的立场：[支持]\n核心论据：这是测试响应。"


@pytest.mark.asyncio
async def test_anchored_debate_uses_chat_interface():
    interview = InterviewContext(
        session_id="rt_test",
        original_question="我应该先做MVP吗？",
        template=DecisionTemplate.GENERAL,
        questions=[],
        answers={},
        enriched_context="背景：资源有限",
        user_bias_signal=None,
    )
    engine = AnchoredDebateEngine(provider=_ChatOnlyProvider())

    report = await engine.run(interview, mode=DebateMode.QUICK)
    assert report.session_id == "rt_test"
    assert isinstance(report.conclusions, list)
