"""Tests for MemoryStore SQLite backend."""

from __future__ import annotations

import os
import tempfile
import pytest

from roundtable.memory import MemoryStore
from roundtable.models import MemoryWrite


@pytest.fixture
def temp_memory_store():
    """Create a MemoryStore backed by a temporary SQLite DB."""
    from pathlib import Path
    from roundtable import db as db_module
    original_path = db_module.DB_PATH
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
            db_module.DB_PATH = Path(tmp_path)
            db_module.init_db()
            # Pre-create sessions for FK constraints in tests
            conn = db_module._get_conn()
            try:
                for sid in ("s_1", "s_2", "s_a", "s_b", "s_c", "s_d"):
                    conn.execute(
                        "INSERT OR IGNORE INTO sessions (session_id, mode, title, status, created_by) VALUES (?, ?, ?, ?, ?)",
                        (sid, "meeting", "Test", "recording", "test"),
                    )
                conn.commit()
            finally:
                conn.close()
            store = MemoryStore()
            yield store
    finally:
        db_module.DB_PATH = original_path
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class TestMemoryStoreCRUD:
    """MemoryStore CRUD operations."""

    def test_get_empty_session(self, temp_memory_store):
        entries = temp_memory_store.get("s_empty")
        assert entries == []

    def test_write_and_get(self, temp_memory_store):
        mem = MemoryWrite(
            memory_id="mem_001",
            session_id="s_1",
            memory_type="fact",
            content="Test content",
            evidence_ids=["e1", "e2"],
            source="supervisor_approved",
            requires_user_confirmation=False,
            confirmed=True,
        )
        temp_memory_store._save_session_memories("s_1", [mem])

        entries = temp_memory_store.get("s_1")
        assert len(entries) == 1
        assert entries[0]["memory_id"] == "mem_001"
        assert entries[0]["content"] == "Test content"
        assert entries[0]["evidence_ids"] == ["e1", "e2"]
        assert entries[0]["confirmed"] is True
        assert "created_at" in entries[0]

    def test_update_entry(self, temp_memory_store):
        mem = MemoryWrite(
            memory_id="mem_002",
            session_id="s_2",
            memory_type="fact",
            content="Before update",
            evidence_ids=[],
            source="supervisor_approved",
            requires_user_confirmation=False,
            confirmed=False,
        )
        temp_memory_store._save_session_memories("s_2", [mem])

        updated = temp_memory_store.update_entry("s_2", "mem_002", {"confirmed": True})
        assert updated is True

        entries = temp_memory_store.get("s_2")
        assert entries[0]["confirmed"] is True
        assert "updated_at" in entries[0]

    def test_update_not_found(self, temp_memory_store):
        updated = temp_memory_store.update_entry("s_nope", "mem_nope", {"confirmed": True})
        assert updated is False


class TestMemoryStoreSearch:
    """MemoryStore search operations."""

    def test_search_finds_match(self, temp_memory_store):
        mem1 = MemoryWrite(
            memory_id="mem_s1", session_id="s_a", memory_type="fact",
            content="Apple pie recipe", evidence_ids=[], source="test",
            requires_user_confirmation=False, confirmed=True,
        )
        mem2 = MemoryWrite(
            memory_id="mem_s2", session_id="s_b", memory_type="fact",
            content="Banana bread recipe", evidence_ids=[], source="test",
            requires_user_confirmation=False, confirmed=True,
        )
        temp_memory_store._save_session_memories("s_a", [mem1])
        temp_memory_store._save_session_memories("s_b", [mem2])

        results = temp_memory_store.search("Apple")
        assert len(results) == 1
        assert results[0]["memory_id"] == "mem_s1"

    def test_search_case_insensitive(self, temp_memory_store):
        mem = MemoryWrite(
            memory_id="mem_s3", session_id="s_c", memory_type="fact",
            content="UPPERCASE CONTENT", evidence_ids=[], source="test",
            requires_user_confirmation=False, confirmed=True,
        )
        temp_memory_store._save_session_memories("s_c", [mem])

        results = temp_memory_store.search("uppercase")
        assert len(results) == 1

    def test_search_limit(self, temp_memory_store):
        memories = []
        for i in range(25):
            memories.append(MemoryWrite(
                memory_id=f"mem_{i:03d}", session_id="s_d", memory_type="fact",
                content=f"Common keyword item {i}", evidence_ids=[], source="test",
                requires_user_confirmation=False, confirmed=True,
            ))
        temp_memory_store._save_session_memories("s_d", memories)

        results = temp_memory_store.search("Common keyword", limit=10)
        assert len(results) == 10

    def test_search_no_match(self, temp_memory_store):
        results = temp_memory_store.search("zzzzzzzzzz")
        assert results == []
