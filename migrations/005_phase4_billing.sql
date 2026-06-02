-- Migration 005: Phase 4 — Billing, subscriptions, export, payments
-- Adds: user plan columns, orders, usage_logs

-- User subscription columns (added via init_db migration)
-- ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free';
-- ALTER TABLE users ADD COLUMN trial_expires_at TEXT;
-- ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'active';
-- ALTER TABLE users ADD COLUMN quota_reset_at TEXT;

CREATE TABLE IF NOT EXISTS orders (
    order_id    TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    plan        TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'CNY',
    provider    TEXT NOT NULL DEFAULT '',
    provider_order_id TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    paid_at     TEXT,
    activated_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_logs (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    session_id  TEXT,
    action      TEXT NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    cost_cents  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_usage_logs_user ON usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_session ON usage_logs(session_id);
