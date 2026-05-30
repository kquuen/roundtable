"""P1: JSON file-based persistence layer for sessions, evidence, and reports.

Replaces the in-memory dict with persistent JSON storage under data/.
Sessions survive server restarts.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from roundtable.models import Session, SessionStatus, SessionMode, AgentReview, SupervisorReview

logger = logging.getLogger("roundtable.store")


def _validate_session_id(session_id: str) -> str:
    """Validate session_id to prevent path traversal attacks."""
    if not session_id:
        raise ValueError("session_id cannot be empty")
    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        raise ValueError(
            f"Invalid session_id '{session_id}': only alphanumeric, underscore, hyphen allowed"
        )
    return session_id


class SessionStore:
    """JSON file-based session + evidence store.

    Directory layout:
        data/sessions/{session_id}.json   — session metadata + evidence segments
        data/sessions/_index.json         — list of all session IDs
    """

    def __init__(self, base_dir: str | Path = "data/sessions"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.base_dir / "_index.json"
        self._sessions: dict[str, Session] = {}
        self._evidence: dict[str, list[dict]] = {}
        self._agent_reviews: dict[str, list[dict]] = {}
        self._supervisor_reviews: dict[str, list[dict]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._load_all()

    # ── Load / Save ──

    def _load_all(self) -> None:
        """Load all persisted sessions from disk."""
        if not self._index_path.exists():
            return

        try:
            idx = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        for sid in idx.get("sessions", []):
            try:
                self._load_one(sid)
            except Exception:
                logger.warning("Skipping corrupted session file: %s", sid, exc_info=True)
                continue

    def _load_one(self, session_id: str) -> None:
        """Load a single session file."""
        path = self._session_path(session_id)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))

        # Restore Session model
        meta = data.get("meta", {})
        session = Session(
            session_id=session_id,
            mode=SessionMode(meta.get("mode", "meeting")),
            title=meta.get("title", ""),
            status=SessionStatus(meta.get("status", "recording")),
            started_at=meta.get("started_at"),
            ended_at=meta.get("ended_at"),
            created_by=meta.get("created_by", "local_user"),
        )
        self._sessions[session_id] = session

        # Restore evidence segments
        evidence = data.get("evidence", [])
        if evidence:
            self._evidence[session_id] = evidence

        # Restore reviews
        ar_data = data.get("agent_reviews", [])
        sr_data = data.get("supervisor_reviews", [])
        if ar_data:
            self._agent_reviews[session_id] = ar_data
        if sr_data:
            self._supervisor_reviews[session_id] = sr_data

    def _save_one(self, session_id: str) -> None:
        """Persist a single session to disk (thread-safe per session)."""
        session = self._sessions.get(session_id)
        if not session:
            return

        data = {
            "meta": {
                "session_id": session.session_id,
                "mode": session.mode.value if hasattr(session.mode, 'value') else str(session.mode),
                "title": session.title,
                "status": session.status.value if hasattr(session.status, 'value') else str(session.status),
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": session.ended_at.isoformat() if session.ended_at else None,
                "created_by": session.created_by,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "evidence": self._evidence.get(session_id, []),
            "agent_reviews": self._agent_reviews.get(session_id, []),
            "supervisor_reviews": self._supervisor_reviews.get(session_id, []),
        }

        with self._locks_lock:
            lock = self._locks.setdefault(session_id, threading.Lock())
        with lock:
            path = self._session_path(session_id)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _save_index(self) -> None:
        """Persist the session index (thread-safe)."""
        idx = {"sessions": list(self._sessions.keys())}
        with self._index_lock:
            self._index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    def _session_path(self, session_id: str) -> Path:
        _validate_session_id(session_id)
        return self.base_dir / f"{session_id}.json"

    # ── Session CRUD ──

    def create(self, title: str = "", mode: str = "meeting") -> Session:
        """Create a new session and persist it."""
        sid = f"s_{uuid.uuid4().hex[:8]}"
        session = Session(
            session_id=sid,
            mode=SessionMode(mode),
            title=title,
            status=SessionStatus.RECORDING,
            started_at=datetime.now(timezone.utc),
        )
        self._sessions[sid] = session
        self._save_one(sid)
        self._save_index()
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def update_status(self, session_id: str, status: SessionStatus) -> None:
        """Update session status and persist."""
        session = self._sessions.get(session_id)
        if session:
            session.status = status
            if status == SessionStatus.COMPLETED:
                session.ended_at = datetime.now(timezone.utc)
            self._save_one(session_id)

    def list_all(self) -> list[Session]:
        return list(self._sessions.values())

    # ── Evidence ──

    def store_evidence(self, session_id: str, segments: list[dict]) -> None:
        """Store evidence segments for a session."""
        self._evidence[session_id] = segments
        self._save_one(session_id)

    def get_evidence(self, session_id: str) -> list[dict]:
        """Retrieve stored evidence segments."""
        return self._evidence.get(session_id, [])

    def store_reviews(
        self,
        session_id: str,
        agent_reviews: list[AgentReview],
        supervisor_reviews: list[SupervisorReview],
    ) -> None:
        """Persist reviews for later retrieval by /pending and /review/confirm."""
        self._agent_reviews[session_id] = [ar.model_dump() for ar in agent_reviews]
        self._supervisor_reviews[session_id] = [sr.model_dump() for sr in supervisor_reviews]
        self._save_one(session_id)

    def get_reviews(self, session_id: str) -> tuple[list[dict], list[dict]]:
        """Retrieve stored reviews. Returns (agent_reviews, supervisor_reviews)."""
        return (
            self._agent_reviews.get(session_id, []),
            self._supervisor_reviews.get(session_id, []),
        )

    def session_count(self) -> int:
        return len(self._sessions)


class ReportStore:
    """JSON file-based report archive.

    Directory layout:
        reports/{session_id}_{timestamp}.md
        reports/_index.json
    """

    def __init__(self, base_dir: str | Path = "reports"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.base_dir / "_index.json"

    def save(self, session_id: str, title: str, report: str) -> Path:
        """Save a report and return its path."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{session_id}_{ts}.md"
        path = self.base_dir / filename
        path.write_text(report, encoding="utf-8")

        # Update index
        self._add_to_index(session_id, filename, title, ts)
        return path

    def list_for_session(self, session_id: str) -> list[dict]:
        """List all reports for a session."""
        if not self._index_path.exists():
            return []
        try:
            idx = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return idx.get(session_id, [])

    def get(self, filename: str) -> str | None:
        """Read a specific report by filename."""
        if ".." in filename or "/" in filename or "\\" in filename:
            return None
        path = self.base_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _add_to_index(self, session_id: str, filename: str, title: str, ts: str) -> None:
        idx: dict = {}
        if self._index_path.exists():
            try:
                idx = json.loads(self._index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                idx = {}

        if session_id not in idx:
            idx[session_id] = []

        idx[session_id].append({
            "filename": filename,
            "title": title,
            "created_at": ts,
        })

        self._index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
