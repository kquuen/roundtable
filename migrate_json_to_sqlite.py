#!/usr/bin/env python3
"""Migrate legacy JSON session data to SQLite.

Usage:
    python migrate_json_to_sqlite.py

Backs up data/sessions/ to data/sessions.bak/ before migration.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from roundtable.db import init_db, _get_conn
from roundtable.models import SessionMode, SessionStatus


def main():
    sessions_dir = Path("data/sessions")
    if not sessions_dir.exists():
        print("No legacy sessions directory found — nothing to migrate.")
        return

    # Backup
    backup_dir = Path("data/sessions.bak")
    if backup_dir.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(f"data/sessions.bak.{ts}")
    shutil.copytree(sessions_dir, backup_dir)
    print(f"Backed up legacy sessions to {backup_dir}")

    init_db()
    conn = _get_conn()
    migrated = 0

    for path in sessions_dir.glob("*.json"):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  SKIP {path.name}: corrupt JSON ({e})")
            continue

        meta = data.get("meta", {})
        sid = meta.get("session_id", path.stem)

        # Insert session
        conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, mode, title, status, started_at, ended_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                sid,
                meta.get("mode", "meeting"),
                meta.get("title", ""),
                meta.get("status", "recording"),
                meta.get("started_at"),
                meta.get("ended_at"),
                meta.get("created_by", "local_user"),
            ),
        )

        # Insert evidence
        for seg in data.get("evidence", []):
            conn.execute(
                "INSERT INTO evidence_segments (session_id, speaker, text) VALUES (?, ?, ?)",
                (sid, seg.get("speaker", "Speaker"), seg.get("text", "")),
            )

        # Insert agent_reviews (denormalised as JSON)
        for ar in data.get("agent_reviews", []):
            conn.execute(
                """INSERT INTO agent_reviews
                   (session_id, agent_id, summary, claims_json, open_questions_json, recommended_next_actions_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    ar.get("agent_id", ""),
                    ar.get("summary", ""),
                    json.dumps(ar.get("claims", []), ensure_ascii=False),
                    json.dumps(ar.get("open_questions", []), ensure_ascii=False),
                    json.dumps(ar.get("recommended_next_actions", []), ensure_ascii=False),
                ),
            )

        # Insert supervisor_reviews
        for sr in data.get("supervisor_reviews", []):
            conn.execute(
                """INSERT INTO supervisor_reviews
                   (session_id, claim_id, review_result, final_type, reason, required_changes_json, boundary_classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    sr.get("claim_id", ""),
                    sr.get("review_result", "approved"),
                    sr.get("final_type"),
                    sr.get("reason", ""),
                    json.dumps(sr.get("required_changes", []), ensure_ascii=False),
                    sr.get("boundary_classification"),
                ),
            )

        migrated += 1

    conn.commit()
    conn.close()
    print(f"Migrated {migrated} sessions to SQLite.")
    print("You can now delete the legacy JSON files after verification:")
    print(f"  rm -rf {sessions_dir}")


if __name__ == "__main__":
    main()
