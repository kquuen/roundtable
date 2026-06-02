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

        CREATE TABLE IF NOT EXISTS debate_groups (
            group_id    TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            group_name  TEXT NOT NULL DEFAULT '',
            topic       TEXT NOT NULL DEFAULT '',
            agent_ids_json TEXT NOT NULL DEFAULT '[]',
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS debate_steps (
            step_id     TEXT PRIMARY KEY,
            group_id    TEXT NOT NULL,
            step_number INTEGER NOT NULL,
            agent_id    TEXT NOT NULL,
            step_type   TEXT NOT NULL DEFAULT 'statement',
            content     TEXT NOT NULL DEFAULT '',
            content_json TEXT NOT NULL DEFAULT '{}',
            confidence  REAL NOT NULL DEFAULT 0.5,
            hallucination_flags_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES debate_groups(group_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS debate_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            agent_id    TEXT,
            content     TEXT NOT NULL DEFAULT '',
            sequence_num INTEGER NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_interrupts (
            interrupt_id TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            interrupt_type TEXT NOT NULL DEFAULT 'question',
            target_agent_id TEXT,
            content     TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS consensus_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            group_id    TEXT,
            step_id     TEXT,
            dimension_scores_json TEXT NOT NULL DEFAULT '{}',
            agreement_level TEXT NOT NULL DEFAULT 'unknown',
            consensus_text TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_debate_groups_session ON debate_groups(session_id);
        CREATE INDEX IF NOT EXISTS idx_debate_steps_group ON debate_steps(group_id);
        CREATE INDEX IF NOT EXISTS idx_debate_events_session ON debate_events(session_id);
        CREATE INDEX IF NOT EXISTS idx_debate_events_seq ON debate_events(session_id, sequence_num);
        CREATE INDEX IF NOT EXISTS idx_interrupts_session ON user_interrupts(session_id);
        CREATE INDEX IF NOT EXISTS idx_consensus_session ON consensus_snapshots(session_id);

        CREATE TABLE IF NOT EXISTS agent_health (
            agent_id    TEXT PRIMARY KEY,
            status      TEXT NOT NULL DEFAULT 'healthy',
            failure_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            circuit_state TEXT NOT NULL DEFAULT 'closed',
            total_hallucinations INTEGER NOT NULL DEFAULT 0,
            avg_confidence REAL NOT NULL DEFAULT 0.0,
            last_failure_at TEXT,
            last_success_at TEXT,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sentinel_alerts (
            alert_id    TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            alert_type  TEXT NOT NULL,
            severity    TEXT NOT NULL DEFAULT 'low',
            agent_id    TEXT,
            claim_id    TEXT,
            message     TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            acknowledged INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_alerts_session ON sentinel_alerts(session_id);
        CREATE INDEX IF NOT EXISTS idx_alerts_type ON sentinel_alerts(alert_type);
        CREATE INDEX IF NOT EXISTS idx_alerts_severity ON sentinel_alerts(severity);
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


# ── Debate Groups CRUD ──

def insert_debate_group(group_id: str, session_id: str, group_name: str, topic: str,
                        agent_ids: list[str], status: str = "pending") -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO debate_groups (group_id, session_id, group_name, topic, agent_ids_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (group_id, session_id, group_name, topic, _to_json(agent_ids), status,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_debate_groups(session_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM debate_groups WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["agent_ids"] = _from_json(d.pop("agent_ids_json", "[]"), [])
            result.append(d)
        return result
    finally:
        conn.close()


def update_debate_group_status(group_id: str, status: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE debate_groups SET status = ? WHERE group_id = ?",
            (status, group_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Debate Steps CRUD ──

def insert_debate_step(step_id: str, group_id: str, step_number: int, agent_id: str,
                       step_type: str, content: str, content_struct: dict | None = None,
                       confidence: float = 0.5, hallucination_flags: list | None = None,
                       sources: list | None = None) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO debate_steps (step_id, group_id, step_number, agent_id, step_type,
                 content, content_json, confidence, hallucination_flags_json, sources_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (step_id, group_id, step_number, agent_id, step_type, content,
             _to_json(content_struct or {}), confidence,
             _to_json(hallucination_flags or []), _to_json(sources or []),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_debate_steps(group_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM debate_steps WHERE group_id = ? ORDER BY step_number, created_at""",
            (group_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["content_struct"] = _from_json(d.pop("content_json", "{}"), {})
            d["hallucination_flags"] = _from_json(d.pop("hallucination_flags_json", "[]"), [])
            d["sources"] = _from_json(d.pop("sources_json", "[]"), [])
            result.append(d)
        return result
    finally:
        conn.close()


# ── Debate Events CRUD ──

_next_sequence: dict[str, int] = {}

def insert_debate_event(session_id: str, event_type: str, agent_id: str | None,
                        content: str, metadata: dict | None = None) -> int:
    conn = _get_conn()
    try:
        seq = _next_sequence.get(session_id, 0) + 1
        _next_sequence[session_id] = seq
        cur = conn.execute(
            """INSERT INTO debate_events (session_id, event_type, agent_id, content, sequence_num, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, event_type, agent_id, content, seq, _to_json(metadata or {}),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid or seq
    finally:
        conn.close()


def get_debate_events(session_id: str, limit: int = 500) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM debate_events WHERE session_id = ? ORDER BY sequence_num LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["metadata"] = _from_json(d.pop("metadata_json", "{}"), {})
            result.append(d)
        return result
    finally:
        conn.close()


def reset_sequence(session_id: str) -> None:
    _next_sequence.pop(session_id, None)


# ── User Interrupts CRUD ──

def insert_user_interrupt(interrupt_id: str, session_id: str, user_id: str,
                          interrupt_type: str, target_agent_id: str | None,
                          content: str, timestamp: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO user_interrupts (interrupt_id, session_id, user_id, interrupt_type,
                 target_agent_id, content, timestamp, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (interrupt_id, session_id, user_id, interrupt_type, target_agent_id,
             content, timestamp, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_interrupts(session_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM user_interrupts WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Consensus Snapshots CRUD ──

def insert_consensus_snapshot(snapshot_id: str, session_id: str, group_id: str | None,
                              step_id: str | None, dimension_scores: dict,
                              agreement_level: str, consensus_text: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO consensus_snapshots (snapshot_id, session_id, group_id, step_id,
                 dimension_scores_json, agreement_level, consensus_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, session_id, group_id, step_id,
             _to_json(dimension_scores), agreement_level, consensus_text,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_consensus_snapshots(session_id: str) -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM consensus_snapshots WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["dimension_scores"] = _from_json(d.pop("dimension_scores_json", "{}"), {})
            result.append(d)
        return result
    finally:
        conn.close()


# ── Users CRUD ──

# ── Agent Health CRUD ──

def upsert_agent_health(agent_id: str, status: str = "", failure_delta: int = 0,
                        success_delta: int = 0, circuit_state: str = "",
                        hallucination_delta: int = 0, confidence: float = 0.0) -> None:
    conn = _get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        # Try update first
        updates = ["updated_at = ?"]
        params = [now]
        if status:
            updates.append("status = ?")
            params.append(status)
        if failure_delta:
            updates.append("failure_count = failure_count + ?")
            params.append(failure_delta)
            updates.append("last_failure_at = ?")
            params.append(now)
        if success_delta:
            updates.append("success_count = success_count + ?")
            params.append(success_delta)
            updates.append("last_success_at = ?")
            params.append(now)
        if circuit_state:
            updates.append("circuit_state = ?")
            params.append(circuit_state)
        if hallucination_delta:
            updates.append("total_hallucinations = total_hallucinations + ?")
            params.append(hallucination_delta)
        if confidence > 0:
            # Weighted moving average for confidence
            updates.append("avg_confidence = (avg_confidence * (success_count + failure_count - 1) + ?) / MAX(1, success_count + failure_count)")
            params.append(confidence)
        params.append(agent_id)

        cur = conn.execute(
            f"UPDATE agent_health SET {', '.join(updates)} WHERE agent_id = ?",
            params,
        )
        if cur.rowcount == 0:
            # Insert new
            conn.execute(
                """INSERT INTO agent_health (agent_id, status, failure_count, success_count,
                     circuit_state, total_hallucinations, avg_confidence, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (agent_id, status or "healthy", failure_delta, success_delta,
                 circuit_state or "closed", hallucination_delta, confidence, now),
            )
        conn.commit()
    finally:
        conn.close()


def get_agent_health(agent_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM agent_health WHERE agent_id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_agent_health() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM agent_health ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def reset_agent_health(agent_id: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE agent_health SET status='healthy', failure_count=0, success_count=0,
                 circuit_state='closed', total_hallucinations=0, avg_confidence=0.0,
                 last_failure_at=NULL, updated_at=? WHERE agent_id = ?""",
            (datetime.now(timezone.utc).isoformat(), agent_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Sentinel Alerts CRUD ──

def insert_sentinel_alert(alert_id: str, session_id: str, alert_type: str,
                          severity: str, agent_id: str | None, claim_id: str | None,
                          message: str, metadata: dict | None = None) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO sentinel_alerts (alert_id, session_id, alert_type, severity,
                 agent_id, claim_id, message, metadata_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert_id, session_id, alert_type, severity, agent_id, claim_id,
             message, _to_json(metadata or {}), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_sentinel_alerts(session_id: str | None = None, severity: str | None = None,
                        limit: int = 100) -> list[dict]:
    conn = _get_conn()
    try:
        conditions = []
        params = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM sentinel_alerts {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["metadata"] = _from_json(d.pop("metadata_json", "{}"), {})
            d["acknowledged"] = bool(d["acknowledged"])
            result.append(d)
        return result
    finally:
        conn.close()


def acknowledge_alert(alert_id: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "UPDATE sentinel_alerts SET acknowledged = 1 WHERE alert_id = ?",
            (alert_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ── Users CRUD ──

def list_all_users() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

