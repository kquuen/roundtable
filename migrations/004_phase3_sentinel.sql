-- Migration 004: Phase 3 — Sentinel (circuit breaker + hallucination detection)
-- Adds: agent_health, sentinel_alerts

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
