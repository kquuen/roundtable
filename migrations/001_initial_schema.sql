-- Migration 001: Initial schema (pre-V2)
-- Applied before Phase 1

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
