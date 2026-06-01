"""Admin endpoints — backup and restore SQLite database."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from fastapi import HTTPException, Header

from roundtable.db import DB_PATH


def _require_admin(x_admin_token: str | None) -> None:
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(403, "Admin endpoints are disabled (ADMIN_TOKEN not set)")
    if x_admin_token != expected:
        raise HTTPException(403, "Invalid admin token")


def backup_database(x_admin_token: str | None) -> dict:
    _require_admin(x_admin_token)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"roundtable_{ts}.db"
    shutil.copy2(str(DB_PATH), str(backup_path))
    return {
        "filename": backup_path.name,
        "path": str(backup_path),
        "size_bytes": backup_path.stat().st_size,
    }


def list_backups(x_admin_token: str | None) -> list[dict]:
    _require_admin(x_admin_token)
    backup_dir = DB_PATH.parent / "backups"
    if not backup_dir.exists():
        return []
    files = sorted(backup_dir.glob("roundtable_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for f in files
    ]


def restore_database(x_admin_token: str | None, file_path: str) -> dict:
    _require_admin(x_admin_token)
    backup_dir = DB_PATH.parent / "backups"
    target = backup_dir / file_path
    if ".." in file_path or "/" in file_path or "\\" in file_path:
        raise HTTPException(400, "Invalid file path")
    if not target.exists():
        raise HTTPException(404, "Backup file not found")
    # Validate SQLite magic header
    with open(target, "rb") as f:
        header = f.read(16)
    if not header.startswith(b"SQLite format 3"):
        raise HTTPException(400, "Invalid SQLite database file")
    # Backup current DB before overwriting
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pre_restore_backup = backup_dir / f"roundtable_pre_restore_{ts}.db"
    shutil.copy2(str(DB_PATH), str(pre_restore_backup))
    # Replace current DB
    shutil.copy2(str(target), str(DB_PATH))
    return {
        "status": "restored",
        "from": file_path,
        "pre_restore_backup": pre_restore_backup.name,
    }
