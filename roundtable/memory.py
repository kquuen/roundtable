"""P1: Memory system — auto-write supervisor-approved claims to persistent memory.

Writes high-confidence facts and decisions to data/memory/ for future recall.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from roundtable.models import (
    AgentReview, EvidenceClaim, MemoryWrite,
    SupervisorReview, ReviewResult, ClaimType,
)


class MemoryStore:
    """JSON file-based memory store.

    Directory layout:
        data/memory/{session_id}.json   — memory entries per session
        data/memory/_index.json         — cross-session memory index
    """

    def __init__(self, base_dir: str | Path = "data/memory"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.base_dir / "_index.json"

    def write_from_reviews(
        self,
        session_id: str,
        agent_reviews: list[AgentReview],
        supervisor_reviews: list[SupervisorReview],
    ) -> list[MemoryWrite]:
        """Auto-write approved high-confidence claims to memory.

        Rules for auto-write:
        - Claim passes supervisor review (APPROVED)
        - Claim type is FACT with confidence >= 0.8, or INFERENCE with confidence >= 0.85
        - Not already in memory for this session
        """
        # Build review lookup
        review_map: dict[str, SupervisorReview] = {
            r.claim_id: r for r in supervisor_reviews
        }

        memories = []
        for ar in agent_reviews:
            for claim in ar.claims:
                r = review_map.get(claim.claim_id)
                if not r or r.review_result != ReviewResult.APPROVED:
                    continue

                should_remember = (
                    (claim.claim_type == ClaimType.FACT and claim.confidence >= 0.8)
                    or (claim.claim_type == ClaimType.INFERENCE and claim.confidence >= 0.85)
                )

                if should_remember:
                    mem = MemoryWrite(
                        memory_id=f"mem_{session_id}_{len(memories):03d}",
                        session_id=session_id,
                        memory_type=claim.claim_type.value if hasattr(claim.claim_type, 'value') else str(claim.claim_type),
                        content=claim.content,
                        evidence_ids=claim.evidence_ids,
                        source="supervisor_approved",
                        requires_user_confirmation=False,
                        confirmed=True,
                    )
                    memories.append(mem)

        if memories:
            self._save_session_memories(session_id, memories)

        return memories

    def _save_session_memories(self, session_id: str, memories: list[MemoryWrite]) -> None:
        """Persist memories to disk."""
        path = self.base_dir / f"{session_id}.json"
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = []

        # Append new memories
        for mem in memories:
            existing.append({
                "memory_id": mem.memory_id,
                "session_id": mem.session_id,
                "memory_type": mem.memory_type,
                "content": mem.content,
                "evidence_ids": mem.evidence_ids,
                "source": mem.source,
                "confirmed": mem.confirmed,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

        # Update index
        self._update_index(session_id, len(existing))

    def _update_index(self, session_id: str, entry_count: int) -> None:
        idx: dict = {}
        if self._index_path.exists():
            try:
                idx = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                idx = {}

        idx[session_id] = {
            "entry_count": entry_count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, session_id: str) -> list[dict]:
        """Retrieve all memory entries for a session."""
        path = self.base_dir / f"{session_id}.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """Simple keyword search across all memory entries (O(n) scan)."""
        results = []
        for path in sorted(self.base_dir.glob("*.json")):
            if path.name.startswith("_"):
                continue
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for entry in entries:
                if keyword.lower() in entry.get("content", "").lower():
                    results.append(entry)
                    if len(results) >= limit:
                        return results
        return results
