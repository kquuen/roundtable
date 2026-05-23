"""Phase 3: Report Composer — structured markdown report generation."""

from __future__ import annotations

from roundtable.models import AgentReview, SupervisorReview, ReviewResult

# ── Bilingual section titles ──

_SECTION_TITLES: dict[str, dict[str, str]] = {
    "zh": {
        "report_title": "圆桌会议审查报告",
        "meeting_topic": "会议主题",
        "summary": "摘要",
        "facts": "会议事实",
        "no_facts": "（无通过审查的事实认定）",
        "inferences": "推断与判断",
        "recommendations": "专家建议",
        "extensions": "创意延展",
        "downgraded": "已降级项",
        "rejected": "已驳回（幻觉/证据不足）",
        "needs_confirm": "待用户确认",
        "open_questions": "待解决问题",
        "next_actions": "下一步行动建议",
        "stats_total": "审查统计",
        "stats_claims": "条 claim",
        "stats_passed": "通过",
        "stats_rejected": "驳回",
    },
    "en": {
        "report_title": "Roundtable Review Report",
        "meeting_topic": "Meeting Topic",
        "summary": "Summary",
        "facts": "Meeting Facts",
        "no_facts": "(No approved factual claims)",
        "inferences": "Inferences & Judgments",
        "recommendations": "Expert Recommendations",
        "extensions": "Creative Extensions",
        "downgraded": "Downgraded",
        "rejected": "Rejected (Hallucination / Insufficient Evidence)",
        "needs_confirm": "Needs User Confirmation",
        "open_questions": "Open Questions",
        "next_actions": "Recommended Next Actions",
        "stats_total": "Review Statistics",
        "stats_claims": "claims",
        "stats_passed": "approved",
        "stats_rejected": "rejected",
    },
}


class _Titles:
    """Safe title accessor — returns the key itself if missing from the lang dict."""

    def __init__(self, data: dict[str, str]):
        self._data = data

    def __getitem__(self, key: str) -> str:
        return self._data.get(key, key)


def compose_report(
    agent_reviews: list[AgentReview],
    supervisor_reviews: list[SupervisorReview],
    session_title: str = "",
    lang: str = "zh",
) -> str:
    """Compose the structured roundtable review report.

    Args:
        lang: "zh" for Chinese, "en" for English
    """
    t = _Titles(_SECTION_TITLES.get(lang, _SECTION_TITLES["zh"]))
    review_map = {r.claim_id: r for r in supervisor_reviews}

    # Categorize claims by review result
    facts: list[tuple[str, str]] = []
    inferences: list[tuple[str, str]] = []
    recommendations: list[tuple[str, str]] = []
    extensions: list[tuple[str, str]] = []
    rejected: list[tuple[str, str, str]] = []  # (agent_id, content, reason)
    downgraded: list[tuple[str, str, str]] = []
    needs_confirm: list[tuple[str, str]] = []

    for ar in agent_reviews:
        for c in ar.claims:
            r = review_map.get(c.claim_id)
            if not r:
                continue
            if r.review_result == ReviewResult.REJECTED:
                rejected.append((ar.agent_id, c.content, r.reason))
            elif r.review_result == ReviewResult.DOWNGRADED:
                downgraded.append((ar.agent_id, r.final_type or c.claim_type, c.content))
            elif r.review_result == ReviewResult.NEEDS_USER_CONFIRMATION:
                needs_confirm.append((ar.agent_id, c.content))
            else:  # APPROVED
                final_type = r.final_type or c.claim_type
                item = (ar.agent_id, c.content)
                if final_type == "fact":
                    facts.append(item)
                elif final_type == "inference":
                    inferences.append(item)
                elif final_type == "recommendation":
                    recommendations.append(item)
                elif final_type == "extension":
                    extensions.append(item)

    lines = [f"# {t['report_title']}", ""]
    if session_title:
        lines.append(f"> **{t['meeting_topic']}：{session_title}**")
        lines.append("")

    # 1. Summary
    lines.append(f"## {t['summary']}")
    for ar in agent_reviews:
        lines.append(f"- **{ar.agent_id}**：{ar.summary}")
    lines.append("")

    # 2. Facts
    lines.append(f"## {t['facts']}")
    if facts:
        for agent_id, content in facts:
            lines.append(f"- [{agent_id}] {content}")
    else:
        lines.append(t["no_facts"])
    lines.append("")

    # 3. Inferences
    if inferences:
        lines.append(f"## {t['inferences']}")
        for agent_id, content in inferences:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 4. Recommendations
    if recommendations:
        lines.append(f"## {t['recommendations']}")
        for agent_id, content in recommendations:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 5. Extensions
    if extensions:
        lines.append(f"## {t['extensions']}")
        for agent_id, content in extensions:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 6. Downgraded
    if downgraded:
        lines.append(f"## {t['downgraded']}")
        for agent_id, final_type, content in downgraded:
            lines.append(f"- [{agent_id}] ~{final_type}~：{content}")
        lines.append("")

    # 7. Rejected (hallucinations)
    if rejected:
        lines.append(f"## {t['rejected']}")
        for agent_id, content, reason in rejected:
            lines.append(f"- [{agent_id}] {content} — _{reason}_")
        lines.append("")

    # 8. Needs user confirmation
    if needs_confirm:
        lines.append(f"## {t['needs_confirm']}")
        for agent_id, content in needs_confirm:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 9. Open questions
    lines.append(f"## {t['open_questions']}")
    for ar in agent_reviews:
        for q in ar.open_questions:
            lines.append(f"- [{ar.agent_id}] {q}")
    lines.append("")

    # 10. Next actions
    lines.append(f"## {t['next_actions']}")
    for ar in agent_reviews:
        for act in ar.recommended_next_actions:
            lines.append(f"- [{ar.agent_id}] {act}")
    lines.append("")

    # Stats
    total = len(supervisor_reviews)
    rej = sum(1 for r in supervisor_reviews if r.review_result == ReviewResult.REJECTED)
    lines.append("---")
    lines.append(
        f"**{t['stats_total']}**：共 {total} {t['stats_claims']}，"
        f"{t['stats_passed']} {total - rej}，{t['stats_rejected']} {rej}。"
    )

    return "\n".join(lines)
