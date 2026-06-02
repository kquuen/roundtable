-- Migration 002: Phase 1 — Agent Registry V2
-- Adds: users billing columns (pre-existing), agents table

-- Users billing columns (added via init_db migration)
-- ALTER TABLE users ADD COLUMN custom_keys TEXT NOT NULL DEFAULT '{}';
-- ALTER TABLE users ADD COLUMN monthly_quota INTEGER NOT NULL DEFAULT 50000;
-- ALTER TABLE users ADD COLUMN monthly_used INTEGER NOT NULL DEFAULT 0;

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
