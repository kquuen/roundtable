"""System admin endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query

from roundtable.auth import require_admin, User
from roundtable import db

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


# ── Sentinel / Health ──

@router.get("/agent-health", summary="Agent health dashboard")
async def agent_health_dashboard(user: User = Depends(require_admin)):
    """Return health status for all agents."""
    health = db.list_agent_health()
    return {
        "agents": health,
        "summary": {
            "total": len(health),
            "healthy": sum(1 for h in health if h.get("status") == "healthy"),
            "degraded": sum(1 for h in health if h.get("status") == "degraded"),
            "unhealthy": sum(1 for h in health if h.get("status") == "unhealthy"),
            "circuits_open": sum(1 for h in health if h.get("circuit_state") == "open"),
        },
    }


@router.post("/agent-health/{agent_id}/reset")
async def reset_agent_health(agent_id: str, user: User = Depends(require_admin)):
    """Manually reset an agent's health and circuit breaker."""
    db.reset_agent_health(agent_id)
    return {"agent_id": agent_id, "status": "reset"}


@router.get("/alerts", summary="Sentinel alerts")
async def list_alerts(
    session_id: str = Query(default="", description="Filter by session"),
    severity: str = Query(default="", description="Filter by severity"),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_admin),
):
    """Return sentinel alerts with optional filtering."""
    alerts = db.get_sentinel_alerts(
        session_id=session_id or None,
        severity=severity or None,
        limit=limit,
    )
    return {
        "alerts": alerts,
        "count": len(alerts),
    }


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, user: User = Depends(require_admin)):
    """Acknowledge a sentinel alert."""
    ok = db.acknowledge_alert(alert_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(404, "Alert not found")
    return {"alert_id": alert_id, "acknowledged": True}
