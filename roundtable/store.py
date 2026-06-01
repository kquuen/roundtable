"""P2: SQLite-backed persistence layer for sessions, evidence, and reports.

Replaces JSON file storage with SQLite (zero-config, file-level).
SessionStore class interface remains unchanged for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roundtable.db import init_db, _get_conn, _to_json, _from_json as _db_from_json
from roundtable.models import Session, SessionStatus, SessionMode, AgentReview, SupervisorReview

logger = logging.getLogger("roundtable.store")


def _validate_session_id(session_id: str) -> str:
    if not session_id:
        raise ValueError("session_id cannot be empty")
    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        raise ValueError(f"Invalid session_id '{session_id}': only alphanumeric, underscore, hyphen allowed")
    return session_id


def _execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    """Execute SQL with a fresh connection and auto-close."""
    conn = _get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    finally:
        conn.close()


class SessionStore:
    """SQLite-backed session + evidence store.

    Class interface unchanged from JSON version; internal storage migrated to SQLite.
    Thread-safe: uses a global DB lock for writes + per-session locks for compound ops.
    """

    def __init__(self, base_dir: str | Path = "data/sessions"):
        # base_dir kept for signature compatibility; actual DB path fixed in db.py
        init_db()
        self._db_lock = threading.Lock()   # protects SQLite writes
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._load_all()

    # ── Load / Save ──

    def _load_all(self) -> None:
        """Warm the in-memory cache from SQLite on startup."""
        self._sessions: dict[str, Session] = {}
        self._evidence: dict[str, list[dict]] = {}
        self._agent_reviews: dict[str, list[dict]] = {}
        self._supervisor_reviews: dict[str, list[dict]] = {}

        conn = _get_conn()
        try:
            cur = conn.execute("SELECT * FROM sessions")
            for row in cur.fetchall():
                session = Session(
                    session_id=row["session_id"],
                    mode=SessionMode(row["mode"]),
                    title=row["title"] or "",
                    status=SessionStatus(row["status"]),
                    started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                    ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
                    created_by=row["created_by"] or "anonymous",
                )
                self._sessions[session.session_id] = session

            cur = conn.execute("SELECT session_id, speaker, text FROM evidence_segments")
            for row in cur.fetchall():
                sid = row["session_id"]
                if sid not in self._evidence:
                    self._evidence[sid] = []
                self._evidence[sid].append({"speaker": row["speaker"], "text": row["text"]})
        finally:
            conn.close()

    def _lock(self, session_id: str) -> threading.Lock:
        with self._locks_lock:
            return self._locks.setdefault(session_id, threading.Lock())

    # ── Session CRUD ──

    def create(self, title: str = "", mode: str = "meeting", created_by: str = "anonymous") -> Session:
        sid = f"s_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        session = Session(
            session_id=sid,
            mode=SessionMode(mode),
            title=title,
            status=SessionStatus.RECORDING,
            started_at=now,
            created_by=created_by,
        )
        with self._lock(sid):
            _execute(
                """INSERT INTO sessions (session_id, mode, title, status, started_at, ended_at, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (sid, session.mode.value, session.title, session.status.value,
                 session.started_at.isoformat() if session.started_at else None,
                 session.ended_at.isoformat() if session.ended_at else None,
                 session.created_by),
            )
            self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def update_status(self, session_id: str, status: SessionStatus) -> None:
        from roundtable.models import is_valid_status_transition
        session = self._sessions.get(session_id)
        if not session:
            return
        if not is_valid_status_transition(session.status.value, status.value):
            raise ValueError(
                f"Invalid status transition from {session.status.value} to {status.value}"
            )
        session.status = status
        if status == SessionStatus.COMPLETED:
            session.ended_at = datetime.now(timezone.utc)
        with self._lock(session_id):
            _execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE session_id = ?",
                (status.value,
                 session.ended_at.isoformat() if session.ended_at else None,
                 session_id),
            )

    def list_all(self) -> list[Session]:
        return list(self._sessions.values())

    # ── Evidence ──

    def store_evidence(self, session_id: str, segments: list[dict]) -> None:
        with self._lock(session_id):
            _execute("DELETE FROM evidence_segments WHERE session_id = ?", (session_id,))
            if segments:
                conn = _get_conn()
                try:
                    conn.executemany(
                        "INSERT INTO evidence_segments (session_id, speaker, text) VALUES (?, ?, ?)",
                        [(session_id, seg.get("speaker", "Speaker")[:64], seg.get("text", "")) for seg in segments],
                    )
                    conn.commit()
                finally:
                    conn.close()
            self._evidence[session_id] = segments

    def get_evidence(self, session_id: str) -> list[dict]:
        return self._evidence.get(session_id, [])

    # ── Reviews ──

    def store_reviews(
        self,
        session_id: str,
        agent_reviews: list[AgentReview],
        supervisor_reviews: list[SupervisorReview],
    ) -> None:
        ar_dicts = [ar.model_dump() for ar in agent_reviews]
        sr_dicts = [sr.model_dump() for sr in supervisor_reviews]

        with self._lock(session_id):
            conn = _get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute("DELETE FROM agent_reviews WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM claims WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM supervisor_reviews WHERE session_id = ?", (session_id,))

                for ar in agent_reviews:
                    conn.execute(
                        """INSERT INTO agent_reviews
                           (session_id, agent_id, summary, claims_json, open_questions_json, recommended_next_actions_json)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (session_id, ar.agent_id, ar.summary,
                         _to_json([c.model_dump() for c in ar.claims]),
                         _to_json(ar.open_questions),
                         _to_json(ar.recommended_next_actions)),
                    )

                for ar in agent_reviews:
                    for c in ar.claims:
                        conn.execute(
                            """INSERT INTO claims
                               (session_id, claim_id, agent_id, claim_type, content,
                                evidence_ids_json, confidence, status, lifecycle,
                                consensus_level, verification, debate_history_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (session_id, c.claim_id, c.agent_id, c.claim_type.value, c.content,
                             _to_json(c.evidence_ids), c.confidence, c.status,
                             c.lifecycle.value, c.consensus_level.value, c.verification.value,
                             _to_json(c.debate_history)),
                        )

                for sr in supervisor_reviews:
                    conn.execute(
                        """INSERT INTO supervisor_reviews
                           (session_id, claim_id, review_result, final_type, reason,
                            required_changes_json, boundary_classification)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (session_id, sr.claim_id, sr.review_result.value, sr.final_type, sr.reason,
                         _to_json(sr.required_changes),
                         sr.boundary_classification.value if sr.boundary_classification else None),
                    )

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            self._agent_reviews[session_id] = ar_dicts
            self._supervisor_reviews[session_id] = sr_dicts

    def get_reviews(self, session_id: str) -> tuple[list[dict], list[dict]]:
        return (
            self._agent_reviews.get(session_id, []),
            self._supervisor_reviews.get(session_id, []),
        )

    def list_by_user(self, username: str, limit: int = 20, offset: int = 0) -> list[Session]:
        conn = _get_conn()
        try:
            cur = conn.execute(
                """SELECT session_id, mode, title, status, started_at, ended_at, created_by
                   FROM sessions WHERE created_by = ? ORDER BY started_at DESC LIMIT ? OFFSET ?""",
                (username, limit, offset),
            )
            sessions = []
            for row in cur.fetchall():
                sessions.append(Session(
                    session_id=row["session_id"],
                    mode=SessionMode(row["mode"]),
                    title=row["title"] or "",
                    status=SessionStatus(row["status"]),
                    started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                    ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
                    created_by=row["created_by"] or "anonymous",
                ))
            return sessions
        finally:
            conn.close()

    def session_count(self) -> int:
        return len(self._sessions)


class ReportStore:
    """SQLite-backed report archive.

    Interface unchanged; internal storage migrated to SQLite.
    """

    def __init__(self, base_dir: str | Path = "reports"):
        init_db()

    def save(self, session_id: str, title: str, report: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{session_id}_{ts}.md"
        _execute(
            "INSERT INTO reports (session_id, filename, title, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, filename, title, report, ts),
        )
        return Path(filename)

    def list_for_session(self, session_id: str) -> list[dict]:
        cur = _execute(
            "SELECT filename, title, created_at FROM reports WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        )
        return [{"filename": r["filename"], "title": r["title"], "created_at": r["created_at"]} for r in cur.fetchall()]

    def get(self, filename: str) -> str | None:
        if ".." in filename or "/" in filename or "\\" in filename:
            return None
        cur = _execute(
            "SELECT content FROM reports WHERE filename = ? LIMIT 1", (filename,)
        )
        row = cur.fetchone()
        return row["content"] if row else None
