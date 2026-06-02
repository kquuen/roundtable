"""Real-time debate endpoints: interview, quick roundtable, SSE streaming."""

from __future__ import annotations

import asyncio
import json as _j
import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from roundtable.auth import User, require_user
from roundtable.config import ConfigManager
from roundtable.dependencies import require_session_owner
from roundtable.billing import require_quota, consume_quota
from roundtable.responses import Utf8JSONResponse
from roundtable.services.sse import (
    start_sse_pipeline,
    validate_stream_key,
    acquire_sse_lock,
    pop_sse_queue,
    cancel_sse_pipeline,
)

logger = logging.getLogger("roundtable.routers.debate_rt")
router = APIRouter(prefix="/roundtable", tags=["debate_rt"])


class InterviewStartRequest(BaseModel):
    question: str
    template: str = "general"


class InterviewAnswerRequest(BaseModel):
    session_id: str
    answers: dict


class QuickRequest(BaseModel):
    question: str
    template: str = "general"
    context: str = ""
    mode: str = "default"


def _get_debate_provider(user=None):
    """Resolve a working LLM provider for debate engines."""
    from roundtable.providers import ProviderRouter

    router = ProviderRouter.get_instance()
    for ref in ("deepseek/deepseek-chat", "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o"):
        try:
            if user:
                return router.get_for_user(ref, user)
            return router.get(ref)
        except Exception as exc:
            logger.debug("Debate provider %s unavailable: %s", ref, exc)
            continue
    logger.warning("All debate providers failed")
    return None


@router.post("/interview", response_class=Utf8JSONResponse)
async def start_interview(req: InterviewStartRequest, user: User = Depends(require_quota)):
    """追问阶段：用户提交问题后，系统返回2-3个追问。"""
    session_id = f"rt_{_uuid.uuid4().hex}"
    try:
        from roundtable.models import DecisionTemplate
        template = DecisionTemplate(req.template)
    except ValueError:
        from roundtable.models import DecisionTemplate
        template = DecisionTemplate.GENERAL

    from roundtable.debate import get_interview_questions, sanitize_user_bias
    questions = get_interview_questions(template)
    sanitized, bias = sanitize_user_bias(req.question)

    return {
        "session_id": session_id,
        "original_question": req.question,
        "sanitized_question": sanitized,
        "template": template.value,
        "questions": questions,
        "_bias_detected": bias is not None,
    }


@router.post("/quick", response_class=Utf8JSONResponse)
async def quick_roundtable(req: QuickRequest, user: User = Depends(require_quota)):
    """零门槛启动辩论（同步，等待完成后返回报告）。"""
    session_id = f"rt_{_uuid.uuid4().hex}"
    from roundtable.debate import sanitize_user_bias, AnchoredDebateEngine
    from roundtable.report import compose_anchored_report
    from roundtable.models import InterviewContext

    sanitized, bias_signal = sanitize_user_bias(req.question)

    interview = InterviewContext(
        session_id=session_id,
        original_question=req.question,
        template=req.template,
        questions=[],
        answers={},
        enriched_context=(req.context or "") + f"\n原始问题：{sanitized}",
        user_bias_signal=bias_signal,
    )

    engine = AnchoredDebateEngine(provider=_get_debate_provider(user=user))
    report = await engine.run(interview, mode=req.mode)
    md = compose_anchored_report(report)

    return {
        "session_id": session_id,
        "report": md,
        "conclusions": report.conclusions,
        "key_dispute": report.key_dispute,
        "blind_spot": report.blind_spot,
        "next_action": report.next_action,
        "specialist_stances": report.specialist_stances,
        "information_gaps": [g.model_dump() for g in report.information_gaps],
    }


@router.post("/quick/stream-start", response_class=Utf8JSONResponse)
async def quick_roundtable_stream_start(req: QuickRequest, user: User = Depends(require_user)):
    """启动流式辩论：返回 session_id，前端随即连接 SSE 端点。"""
    session_id = f"rt_{_uuid.uuid4().hex}"
    from roundtable.debate import sanitize_user_bias, AnchoredDebateEngine
    from roundtable.report import compose_anchored_report
    from roundtable.models import InterviewContext

    sanitized, bias_signal = sanitize_user_bias(req.question)

    interview = InterviewContext(
        session_id=session_id,
        original_question=req.question,
        template=req.template,
        questions=[],
        answers={},
        enriched_context=(req.context or "") + f"\n原始问题：{sanitized}",
        user_bias_signal=bias_signal,
    )

    async def _run_fn(queue):
        engine = AnchoredDebateEngine(provider=_get_debate_provider(user=user))
        report = await engine.run(interview, mode=req.mode, event_queue=queue)
        md = compose_anchored_report(report)
        await queue.put({
            "type": "final_report",
            "content": md,
            "data": {
                "conclusions": report.conclusions,
                "key_dispute": report.key_dispute,
                "blind_spot": report.blind_spot,
                "next_action": report.next_action,
                "specialist_stances": report.specialist_stances,
            },
        })
        return {}

    return await start_sse_pipeline(session_id, _run_fn, None)


@router.get("/stream/{session_id}")
async def stream_debate_events(
    session_id: str,
    key: str = "",
    request: Request = None,
):
    """SSE端点：推送辩论过程的实时事件流。"""
    async with acquire_sse_lock():
        if not validate_stream_key(session_id, key):
            raise HTTPException(401, "Invalid or missing stream key")
        queue = pop_sse_queue(session_id)
        if queue is None:
            raise HTTPException(404, f"No active debate stream for session {session_id}")

    async def event_generator():
        try:
            while True:
                # Detect client disconnect and cancel pipeline
                if request is not None and await request.is_disconnected():
                    logger.info("[%s] Client disconnected, cancelling pipeline", session_id)
                    await cancel_sse_pipeline(session_id)
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield "data: {\"type\": \"heartbeat\"}\n\n"
                    continue

                data_str = _j.dumps(event, ensure_ascii=False)
                yield f"data: {data_str}\n\n"

                if event.get("type") in ("done", "error"):
                    break
        finally:
            pass  # queue already popped from dict

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/templates", response_class=Utf8JSONResponse)
async def list_templates():
    """列出所有决策模板及其推荐Agent组合。"""
    from roundtable.debate import _DEFAULT_AGENTS
    return {
        "templates": [
            {"id": "direction", "name": "方向选择", "desc": "该不该做这个方向"},
            {"id": "feature",   "name": "功能取舍", "desc": "哪个功能先做"},
            {"id": "pricing",   "name": "定价策略", "desc": "如何定价"},
            {"id": "pivot",     "name": "转型决策", "desc": "要不要转型"},
            {"id": "partner",   "name": "合作判断", "desc": "这个合作值不值得谈"},
            {"id": "general",   "name": "通用决策", "desc": "其他类型的决策"},
        ],
        "default_agents": _DEFAULT_AGENTS,
    }
