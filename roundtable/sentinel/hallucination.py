"""Hallucination detection for Agent outputs.

Detection dimensions:
1. Unsourced numbers: contains digits but no data_sources
2. Contradictory claims: same agent contradicts itself across steps
3. Boundary violation: evaluates forbidden_topics
4. Low confidence: confidence < 0.5 but not labeled "speculation"
5. Repetition: verbatim repetition across rounds
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from roundtable.models import DebateStep, EvidenceClaim, SentinelAlert
from roundtable import db

logger = logging.getLogger("roundtable.sentinel.hallucination")

# ── Detection Rules ──

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?(?:%|万|亿|千|百|十)?")
_CONFIDENCE_THRESHOLD = 0.5


class HallucinationDetector:
    """Detect hallucination patterns in agent outputs."""

    def __init__(self):
        self._agent_history: dict[str, list[str]] = {}

    # ── Public API ──

    def check_step(self, step: DebateStep, session_id: str) -> list[dict]:
        """Run all hallucination checks on a debate step.

        Returns list of detected flags.
        """
        flags: list[dict] = []

        f1 = self._check_unsourced_numbers(step)
        if f1:
            flags.append(f1)

        f2 = self._check_low_confidence(step)
        if f2:
            flags.append(f2)

        f3 = self._check_repetition(step)
        if f3:
            flags.append(f3)

        f4 = self._check_boundary_in_content(step)
        if f4:
            flags.append(f4)

        # Persist alerts
        for f in flags:
            self._persist_alert(f, session_id, step.agent_id, step.step_id)

        # Update agent health hallucination count
        if flags:
            db.upsert_agent_health(
                agent_id=step.agent_id,
                hallucination_delta=len(flags),
            )

        return flags

    def check_claim(
        self,
        claim: EvidenceClaim,
        forbidden_topics: list[str],
        session_id: str,
    ) -> list[dict]:
        """Run hallucination checks on a single claim (used by Supervisor)."""
        flags: list[dict] = []

        # Unsourced numbers in fact claims
        if claim.claim_type == "fact":
            f = self._check_unsourced_numbers_in_text(claim.content, claim.evidence_ids)
            if f:
                flags.append({**f, "claim_id": claim.claim_id})

        # Boundary violation
        if forbidden_topics:
            f = self._check_forbidden_topics(claim.content, forbidden_topics)
            if f:
                flags.append({**f, "claim_id": claim.claim_id})

        for f in flags:
            self._persist_alert(f, session_id, claim.agent_id, claim.claim_id)

        if flags:
            db.upsert_agent_health(
                agent_id=claim.agent_id,
                hallucination_delta=len(flags),
            )

        return flags

    def check_contradictions(
        self,
        steps: list[DebateStep],
        session_id: str,
    ) -> list[dict]:
        """Detect contradictions between steps from the same agent."""
        flags: list[dict] = []
        by_agent: dict[str, list[DebateStep]] = {}
        for s in steps:
            by_agent.setdefault(s.agent_id, []).append(s)

        for agent_id, agent_steps in by_agent.items():
            if len(agent_steps) < 2:
                continue
            # Check for direct negation patterns
            for i in range(len(agent_steps) - 1):
                s1 = agent_steps[i]
                s2 = agent_steps[i + 1]
                if self._is_contradictory(s1.content, s2.content):
                    flag = {
                        "type": "contradiction",
                        "severity": "high",
                        "description": f"Agent {agent_id} 在 Step {s1.step_number} 和 Step {s2.step_number} 中出现矛盾",
                        "step_ids": [s1.step_id, s2.step_id],
                    }
                    flags.append(flag)
                    self._persist_alert(flag, session_id, agent_id, s2.step_id)

        return flags

    # ── Individual Checks ──

    def _check_unsourced_numbers(self, step: DebateStep) -> dict | None:
        """Detect numbers without data sources."""
        sources = step.sources or []
        text = step.content
        numbers = _NUMBER_RE.findall(text)
        if numbers and not sources:
            return {
                "type": "unsourced_numbers",
                "severity": "medium",
                "description": f"发现未标注来源的数字: {', '.join(numbers[:3])}",
                "evidence": numbers[:3],
            }
        return None

    def _check_unsourced_numbers_in_text(self, text: str, evidence_ids: list[str]) -> dict | None:
        """Detect numbers without evidence in claim text."""
        numbers = _NUMBER_RE.findall(text)
        if numbers and not evidence_ids:
            return {
                "type": "unsourced_numbers",
                "severity": "medium",
                "description": f"Claim 中包含未标注来源的数字: {', '.join(numbers[:3])}",
                "evidence": numbers[:3],
            }
        return None

    def _check_low_confidence(self, step: DebateStep) -> dict | None:
        """Detect low confidence without 'speculation' label."""
        if step.confidence < _CONFIDENCE_THRESHOLD:
            text = step.content
            if "推测" not in text and "假设" not in text and "可能" not in text:
                return {
                    "type": "low_confidence_unlabeled",
                    "severity": "low",
                    "description": f"置信度仅 {step.confidence:.2f}，但未标注为推测",
                    "confidence": step.confidence,
                }
        return None

    def _check_repetition(self, step: DebateStep) -> dict | None:
        """Detect verbatim repetition across steps from same agent."""
        history = self._agent_history.setdefault(step.agent_id, [])
        for prev in history:
            if step.content and len(step.content) >= 15:
                # Simple containment check
                if step.content in prev or prev in step.content:
                    return {
                        "type": "repetition",
                        "severity": "low",
                        "description": "与之前输出存在大量重复内容",
                    }
        history.append(step.content)
        # Keep history bounded
        if len(history) > 10:
            history.pop(0)
        return None

    def _check_boundary_in_content(self, step: DebateStep) -> dict | None:
        """Check if step content evaluates forbidden topics."""
        # This is a lightweight check; full boundary check is in supervisor
        boundary_keywords = ["必须", "一定", "毫无疑问", "肯定", "绝对"]
        if any(kw in step.content for kw in boundary_keywords):
            return {
                "type": "assertive_language",
                "severity": "low",
                "description": "使用断言性语言，可能越界",
                "keywords_found": [kw for kw in boundary_keywords if kw in step.content],
            }
        return None

    def _check_forbidden_topics(self, content: str, forbidden_topics: list[str]) -> dict | None:
        """Check if content directly mentions forbidden topics."""
        matched = [t for t in forbidden_topics if t in content]
        if matched:
            return {
                "type": "forbidden_topic",
                "severity": "high",
                "description": f"涉及禁止话题: {', '.join(matched)}",
                "matched_topics": matched,
            }
        return None

    def _is_contradictory(self, text1: str, text2: str) -> bool:
        """Heuristic: detect direct negation between two texts."""
        negation_pairs = [
            ("可行", "不可行"), ("正确", "错误"), ("应该", "不应该"),
            ("必须", "不必"), ("好", "不好"), ("安全", "不安全"),
        ]
        for pos, neg in negation_pairs:
            if pos in text1 and neg in text2:
                return True
            if neg in text1 and pos in text2:
                return True
        return False

    # ── Persistence ──

    def _persist_alert(self, flag: dict, session_id: str, agent_id: str, claim_id: str | None) -> None:
        if not session_id:
            return
        db.insert_sentinel_alert(
            alert_id=f"alt_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            alert_type=flag.get("type", "hallucination"),
            severity=flag.get("severity", "medium"),
            agent_id=agent_id,
            claim_id=claim_id,
            message=flag.get("description", ""),
            metadata={k: v for k, v in flag.items() if k not in ("type", "severity", "description")},
        )
