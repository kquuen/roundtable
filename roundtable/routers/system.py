"""System admin endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header

from roundtable.auth import require_admin, User

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/backup")
async def system_backup(
    x_admin_token: str = Header(default=""),
    user: User = Depends(require_admin),
):
    from roundtable.admin import backup_database
    return backup_database(x_admin_token)


@router.get("/backups")
async def list_backups_endpoint(
    x_admin_token: str = Header(default=""),
    user: User = Depends(require_admin),
):
    from roundtable.admin import list_backups
    return {"backups": list_backups(x_admin_token)}


@router.post("/restore")
async def system_restore(
    req: dict,
    x_admin_token: str = Header(default=""),
    user: User = Depends(require_admin),
):
    from roundtable.admin import restore_database
    filename = req.get("filename", "")
    return restore_database(x_admin_token, filename)
