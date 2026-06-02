-- Migration 003: Phase 2 — Structured Debate V2
-- Adds: debate_groups, debate_steps, debate_events, user_interrupts, consensus_snapshots

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
