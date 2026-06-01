"""Review, feedback, and confirmation endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from roundtable.auth import User, require_user
from roundtable.dependencies import get_store, get_memory, require_session_owner
from roundtable.models import (
    SessionStatus,
    ReviewResult,
    AgentReview,
    SupervisorReview,
)
from roundtable.feedback import (
    UserVerdict,
    UserCorrection,
    process_user_correction,
    process_user_answer,
    update_memory_confirmation,
    get_pending_items,
    apply_bulk_verdicts,
)

logger = logging.getLogger("roundtable.routers.review")

router = APIRouter(tags=["review"])


class ConfirmReviewRequest(BaseModel):
    session_id: str
    verdicts: list[dict]


class FeedbackRequest(BaseModel):
    session_id: str
    corrections: list[dict] = []
    answers: list[dict] = []


class MemoryConfirmRequest(BaseModel):
    session_id: str
    memory_id: str
    confirmed: bool


@router.get("/session/{session_id}/pending")
async def get_pending(session_id: str, user: User = Depends(require_user)):
    """获取当前 session 中需要用户裁决的所有待办项。"""
    s = require_session_owner(session_id, user)

    ar_dicts, sr_dicts = get_store().get_reviews(session_id)
    if not ar_dicts or not sr_dicts:
        return {"session_id": session_id, "pending": [], "status": s.status.value}

    agent_reviews = [AgentReview(**d) for d in ar_dicts]
    supervisor_reviews = [SupervisorReview(**d) for d in sr_dicts]
    pending = get_pending_items(supervisor_reviews, agent_reviews)

    return {
        "session_id": session_id,
        "status": s.status.value,
        "pending_count": len(pending),
        "pending": pending,
    }


@router.post("/review/confirm")
async def confirm_review(req: ConfirmReviewRequest, user: User = Depends(require_user)):
    """用户对 NEEDS_CONFIRMATION 的 claim 提交裁决。"""
    s = require_session_owner(req.session_id, user)

    ar_dicts, sr_dicts = get_store().get_reviews(req.session_id)
    if not ar_dicts or not sr_dicts:
        raise HTTPException(400, "No reviews found — run /roundtable/run first")

    agent_reviews = [AgentReview(**d) for d in ar_dicts]
    supervisor_reviews = [SupervisorReview(**d) for d in sr_dicts]

    verdicts = []
    errors = []
    for v_dict in req.verdicts:
        try:
            verdicts.append(UserVerdict.from_dict(v_dict))
        except ValueError as e:
            errors.append({"input": v_dict, "error": str(e)})

    if errors:
        raise HTTPException(400, f"Invalid verdicts: {errors}")

    result = apply_bulk_verdicts(verdicts, supervisor_reviews, agent_reviews)
    get_store().store_reviews(req.session_id, agent_reviews, supervisor_reviews)

    remaining_pending = sum(
        1 for sr in supervisor_reviews
        if sr.review_result == ReviewResult.NEEDS_USER_CONFIRMATION
    )
    if remaining_pending == 0:
        get_store().update_status(req.session_id, SessionStatus.COMPLETED)

    from roundtable.report import compose_report
    report = compose_report(agent_reviews, supervisor_reviews, session_title=s.title)

    return {
        "session_id": req.session_id,
        "verdicts_applied": result["applied"],
        "verdicts_failed": result["failed"],
        "remaining_pending": remaining_pending,
        "status": "completed" if remaining_pending == 0 else "reviewing",
        "report": report,
        "details": result["details"],
    }


@router.post("/session/{session_id}/feedback")
async def submit_feedback(session_id: str, req: FeedbackRequest, user: User = Depends(require_user)):
    """提交用户反馈：纠正系统推断 + 回答问题。"""
    require_session_owner(session_id, user)

    correction_results = []
    for c_dict in req.corrections:
        try:
            corr = UserCorrection.from_dict(c_dict)
            r = process_user_correction(corr)
            correction_results.append(r)
        except Exception:
            logger.exception("Feedback correction failed: %s", c_dict)
            correction_results.append({"error": "Processing failed", "input": c_dict})

    answer_results = []
    for a_dict in req.answers:
        r = process_user_answer(
            session_id,
            a_dict.get("question", ""),
            a_dict.get("answer", ""),
        )
        answer_results.append(r)

    return {
        "session_id": session_id,
        "corrections_recorded": len([r for r in correction_results if r.get("recorded")]),
        "answers_recorded": len([r for r in answer_results if r.get("recorded")]),
        "corrections": correction_results,
        "answers": answer_results,
    }


@router.post("/memory/confirm")
async def confirm_memory(req: MemoryConfirmRequest, user: User = Depends(require_user)):
    """用户确认或驳回一条自动写入的记忆条目。"""
    result = update_memory_confirmation(
        get_memory(), req.session_id, req.memory_id, req.confirmed,
    )
    if not result.get("updated"):
        raise HTTPException(404, "Memory entry not found or could not be updated")
    return result
