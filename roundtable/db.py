"""SQLite persistence layer for Roundtable.

Provides synchronous DB operations via sqlite3 (stdlib).
aiosqlite can be swapped in later for fully-async paths.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("roundtable.db")

DB_PATH = Path("data/roundtable.db")


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _add_column_if_missing(conn: sqlite3.Connection, table: str, col: str, defn: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {r["name"] for r in cur.fetchall()}
    if col not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
        conn.commit()


def init_db() -> None:
    """Create all tables if they don't exist."""
    conn = _get_conn()
    try:
        _create_tables(conn)
        # 迁移已有表：添加新列
        _add_column_if_missing(conn, "users", "custom_keys", "TEXT NOT NULL DEFAULT '{}'")
        _add_column_if_missing(conn, "users", "monthly_quota", "INTEGER NOT NULL DEFAULT 50000")
        _add_column_if_missing(conn, "users", "monthly_used", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "memories", "created_at", "TEXT")
        _add_column_if_missing(conn, "memories", "updated_at", "TEXT")
    finally:
        conn.close()


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id   TEXT PRIMARY KEY,
            username  TEXT NOT NULL UNIQUE,
            email     TEXT NOT NULL,
            hashed_password TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            mode        TEXT NOT NULL,
            title       TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'recording',
            started_at  TEXT,
            ended_at    TEXT,
            created_by  TEXT NOT NULL DEFAULT 'anonymous'
        );

        CREATE TABLE IF NOT EXISTS evidence_segments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            speaker     TEXT NOT NULL DEFAULT 'Speaker',
            text        TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_reviews (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            agent_id    TEXT NOT NULL,
            summary     TEXT,
            claims_json TEXT NOT NULL DEFAULT '[]',
            open_questions_json TEXT NOT NULL DEFAULT '[]',
            recommended_next_actions_json TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS claims (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            claim_id    TEXT NOT NULL,
            agent_id    TEXT NOT NULL,
            claim_type  TEXT NOT NULL DEFAULT 'inference',
            content     TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            confidence  REAL NOT NULL DEFAULT 0.5,
            status      TEXT NOT NULL DEFAULT 'pending_review',
            lifecycle   TEXT NOT NULL DEFAULT 'draft',
            consensus_level TEXT NOT NULL DEFAULT 'unknown',
            verification TEXT NOT NULL DEFAULT 'unchecked',
            debate_history_json TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS supervisor_reviews (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            claim_id    TEXT NOT NULL,
            review_result TEXT NOT NULL,
            final_type  TEXT,
            reason      TEXT NOT NULL DEFAULT '',
            required_changes_json TEXT NOT NULL DEFAULT '[]',
            boundary_classification TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            filename    TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            memory_id   TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            content     TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            source      TEXT NOT NULL DEFAULT 'supervisor_approved',
            requires_user_confirmation INTEGER NOT NULL DEFAULT 0,
            confirmed   INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT,
            updated_at  TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence_segments(session_id);
        CREATE INDEX IF NOT EXISTS idx_agent_reviews_session ON agent_reviews(session_id);
        CREATE INDEX IF NOT EXISTS idx_claims_session ON claims(session_id);
        CREATE INDEX IF NOT EXISTS idx_supervisor_session ON supervisor_reviews(session_id);
        CREATE INDEX IF NOT EXISTS idx_reports_session ON reports(session_id);
        CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id);

        CREATE TABLE IF NOT EXISTS agents (
            agent_id    TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            emoji       TEXT NOT NULL DEFAULT '🤖',
            role        TEXT NOT NULL DEFAULT '',
            domains_json TEXT NOT NULL DEFAULT '[]',
            keywords_json TEXT NOT NULL DEFAULT '[]',
            methodology TEXT NOT NULL DEFAULT '',
            score_dimension TEXT NOT NULL DEFAULT '',
            can_challenge_json TEXT NOT NULL DEFAULT '[]',
            must_yield_to_json TEXT NOT NULL DEFAULT '[]',
            max_words   INTEGER NOT NULL DEFAULT 800,
            min_words   INTEGER NOT NULL DEFAULT 150,
            forbidden_topics_json TEXT NOT NULL DEFAULT '[]',
            required_output_fields_json TEXT NOT NULL DEFAULT '[]',
            is_active   INTEGER NOT NULL DEFAULT 1,
            profile_md  TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_agents_active ON agents(is_active);
        """
    )
    conn.commit()


# ── Helper ──

def _to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _from_json(text: str | None, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("JSON decode failed, returning default: %r", text[:200] if text else None)
        return default


# ── Users CRUD ──

def create_user(user_id: str, username: str, email: str, hashed_password: str,
                custom_keys: dict | None = None, monthly_quota: int = 50000,
                monthly_used: int = 0) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO users (user_id, username, email, hashed_password, created_at,
                 custom_keys, monthly_quota, monthly_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, email, hashed_password,
             datetime.now(timezone.utc).isoformat(),
             _to_json(custom_keys or {}), monthly_quota, monthly_used),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_user_custom_keys(user_id: str, custom_keys: dict) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET custom_keys = ? WHERE user_id = ?",
            (_to_json(custom_keys), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_user_usage(user_id: str, monthly_used: int) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET monthly_used = ? WHERE user_id = ?",
            (monthly_used, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Memories CRUD ──

def insert_memory(
    session_id: str,
    memory_id: str,
    memory_type: str,
    content: str,
    evidence_ids: list[str] | None = None,
    source: str = "supervisor_approved",
    requires_user_confirmation: bool = False,
    confirmed: bool = False,
    created_at: str | None = None,
) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO memories
               (session_id, memory_id, memory_type, content, evidence_ids_json,
                source, requires_user_confirmation, confirmed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, memory_id, memory_type, content,
             _to_json(evidence_ids or []), source,
             1 if requires_user_confirmation else 0,
             1 if confirmed else 0,
             created_at or datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_memories_by_session(session_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT memory_id, session_id, memory_type, content,
                      evidence_ids_json, source, confirmed, created_at, updated_at
               FROM memories WHERE session_id = ? ORDER BY created_at DESC""",
            (session_id,),
        ).fetchall()
        return [
            {
                "memory_id": r["memory_id"],
                "session_id": r["session_id"],
                "memory_type": r["memory_type"],
                "content": r["content"],
                "evidence_ids": _from_json(r["evidence_ids_json"], []),
                "source": r["source"],
                "confirmed": bool(r["confirmed"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def search_memories(keyword: str, limit: int = 20) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT memory_id, session_id, memory_type, content,
                      evidence_ids_json, source, confirmed, created_at, updated_at
               FROM memories
               WHERE content LIKE ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (f"%{keyword}%", limit),
        ).fetchall()
        return [
            {
                "memory_id": r["memory_id"],
                "session_id": r["session_id"],
                "memory_type": r["memory_type"],
                "content": r["content"],
                "evidence_ids": _from_json(r["evidence_ids_json"], []),
                "source": r["source"],
                "confirmed": bool(r["confirmed"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def update_memory_entry(session_id: str, memory_id: str, updates: dict) -> bool:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM memories WHERE session_id = ? AND memory_id = ?",
            (session_id, memory_id),
        ).fetchone()
        if not row:
            return False

        allowed = {"memory_type", "content", "source", "confirmed"}
        fields = []
        values = []
        for k, v in updates.items():
            if k in allowed:
                fields.append(f"{k} = ?")
                if k == "confirmed":
                    values.append(1 if v else 0)
                else:
                    values.append(v)
        if not fields:
            return False

        fields.append("updated_at = ?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(session_id)
        values.append(memory_id)

        conn.execute(
            f"UPDATE memories SET {', '.join(fields)} WHERE session_id = ? AND memory_id = ?",
            values,
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ── Users CRUD ──

# ── Agents CRUD ──

def upsert_agent(agent: dict) -> None:
    """Insert or update an agent record (sync from registry.json)."""
    conn = _get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO agents (
                agent_id, name, emoji, role, domains_json, keywords_json,
                methodology, score_dimension, can_challenge_json, must_yield_to_json,
                max_words, min_words, forbidden_topics_json, required_output_fields_json,
                is_active, profile_md, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name=excluded.name,
                emoji=excluded.emoji,
                role=excluded.role,
                domains_json=excluded.domains_json,
                keywords_json=excluded.keywords_json,
                methodology=excluded.methodology,
                score_dimension=excluded.score_dimension,
                can_challenge_json=excluded.can_challenge_json,
                must_yield_to_json=excluded.must_yield_to_json,
                max_words=excluded.max_words,
                min_words=excluded.min_words,
                forbidden_topics_json=excluded.forbidden_topics_json,
                required_output_fields_json=excluded.required_output_fields_json,
                is_active=excluded.is_active,
                profile_md=excluded.profile_md,
                updated_at=excluded.updated_at
            """,
            (
                agent["id"],
                agent["name"],
                agent.get("emoji", "🤖"),
                agent.get("role", ""),
                _to_json(agent.get("domains", [])),
                _to_json(agent.get("keywords", [])),
                agent.get("methodology", ""),
                agent.get("score_dimension", ""),
                _to_json(agent.get("can_challenge", [])),
                _to_json(agent.get("must_yield_to", [])),
                agent.get("max_words", 800),
                agent.get("min_words", 150),
                _to_json(agent.get("forbidden_topics", [])),
                _to_json(agent.get("required_output_fields", [])),
                1 if agent.get("is_active", True) else 0,
                agent.get("profile_md", ""),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_agents(active_only: bool = True) -> list[dict]:
    conn = _get_conn()
    try:
        sql = "SELECT * FROM agents"
        params = ()
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"
        rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["domains"] = _from_json(d.pop("domains_json", "[]"), [])
            d["keywords"] = _from_json(d.pop("keywords_json", "[]"), [])
            d["can_challenge"] = _from_json(d.pop("can_challenge_json", "[]"), [])
            d["must_yield_to"] = _from_json(d.pop("must_yield_to_json", "[]"), [])
            d["forbidden_topics"] = _from_json(d.pop("forbidden_topics_json", "[]"), [])
            d["required_output_fields"] = _from_json(d.pop("required_output_fields_json", "[]"), [])
            d["is_active"] = bool(d["is_active"])
            result.append(d)
        return result
    finally:
        conn.close()


def get_agent(agent_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["domains"] = _from_json(d.pop("domains_json", "[]"), [])
        d["keywords"] = _from_json(d.pop("keywords_json", "[]"), [])
        d["can_challenge"] = _from_json(d.pop("can_challenge_json", "[]"), [])
        d["must_yield_to"] = _from_json(d.pop("must_yield_to_json", "[]"), [])
        d["forbidden_topics"] = _from_json(d.pop("forbidden_topics_json", "[]"), [])
        d["required_output_fields"] = _from_json(d.pop("required_output_fields_json", "[]"), [])
        d["is_active"] = bool(d["is_active"])
        return d
    finally:
        conn.close()


def list_all_users() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

