"""System admin endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/backup")
async def system_backup(x_admin_token: str = Header(default="")):
    from roundtable.admin import backup_database
    return backup_database(x_admin_token)


@router.get("/backups")
async def list_backups_endpoint(x_admin_token: str = Header(default="")):
    from roundtable.admin import list_backups
    return {"backups": list_backups(x_admin_token)}


@router.post("/restore")
async def system_restore(req: dict, x_admin_token: str = Header(default="")):
    from roundtable.admin import restore_database
    filename = req.get("filename", "")
    return restore_database(x_admin_token, filename)
