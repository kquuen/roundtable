"""P3: Memory system — SQLite-backed persistent memory.

Auto-write supervisor-approved claims to the SQLite memories table for future recall.
Replaces legacy JSON file storage (data/memory/*.json) with transactional SQL.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from roundtable.db import (
    init_db,
    insert_memory,
    get_memories_by_session,
    search_memories,
    update_memory_entry,
)
from roundtable.models import (
    AgentReview,
    MemoryWrite,
    SupervisorReview,
    ReviewResult,
    ClaimType,
)

logger = logging.getLogger("roundtable.memory")


class MemoryStore:
    """SQLite-backed memory store.

    Replaces JSON file storage with transactional SQLite operations.
    Interface remains unchanged for backward compatibility.
    """

    def __init__(self, base_dir: str | Path = "data/memory"):
        self.base_dir = Path(base_dir)
        init_db()
        self._migrate_from_json_if_needed()

    def _migrate_from_json_if_needed(self) -> None:
        """One-time migration from legacy JSON files to SQLite."""
        if not self.base_dir.exists():
            return

        json_files = list(self.base_dir.glob("*.json"))
        if not json_files:
            return

        backup_dir = self.base_dir.parent / f"memory.backup.{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copytree(self.base_dir, backup_dir)
            logger.info("Memory JSON backup created at %s", backup_dir)
        except OSError as e:
            logger.warning("Failed to backup memory JSON files: %s", e)

        migrated = 0
        for path in json_files:
            if path.name.startswith("_"):
                continue
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for entry in entries:
                try:
                    insert_memory(
                        session_id=entry.get("session_id", ""),
                        memory_id=entry.get("memory_id", ""),
                        memory_type=entry.get("memory_type", "unknown"),
                        content=entry.get("content", ""),
                        evidence_ids=entry.get("evidence_ids", []),
                        source=entry.get("source", "supervisor_approved"),
                        requires_user_confirmation=entry.get("requires_user_confirmation", False),
                        confirmed=entry.get("confirmed", False),
                        created_at=entry.get("created_at"),
                    )
                    migrated += 1
                except Exception:
                    logger.exception("Failed to migrate memory entry %s", entry.get("memory_id"))

        # Rename directory to prevent re-migration
        try:
            self.base_dir.rename(self.base_dir.with_suffix(".migrated"))
            logger.info("Migrated %d memory entries from JSON to SQLite", migrated)
        except OSError as e:
            logger.warning("Failed to rename memory dir after migration: %s", e)

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
        """
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
        """Persist memories to SQLite."""
        for mem in memories:
            try:
                insert_memory(
                    session_id=mem.session_id,
                    memory_id=mem.memory_id,
                    memory_type=mem.memory_type,
                    content=mem.content,
                    evidence_ids=mem.evidence_ids,
                    source=mem.source,
                    requires_user_confirmation=mem.requires_user_confirmation,
                    confirmed=mem.confirmed,
                )
            except Exception:
                logger.exception("Failed to insert memory %s", mem.memory_id)

    def get(self, session_id: str) -> list[dict]:
        """Retrieve all memory entries for a session."""
        return get_memories_by_session(session_id)

    def update_entry(self, session_id: str, memory_id: str, updates: dict) -> bool:
        """Update a single memory entry's fields (e.g. confirmed status).

        Returns True if the entry was found and updated, False otherwise.
        """
        result = update_memory_entry(session_id, memory_id, updates)
        if result:
            logger.info("Memory entry %s updated: %s", memory_id, updates)
        return result

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """Keyword search across all memory entries (SQL LIKE)."""
        return search_memories(keyword, limit)
