"""Skills, providers, and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from roundtable.config import ConfigManager
from roundtable.dependencies import get_store, llm_enabled
from roundtable.skills import load_from_directory, reload_skills, list_skills

router = APIRouter(tags=["skills"])


@router.post("/skills/reload")
async def reload_skills_endpoint():
    """Hot-reload skill definitions from skills/ directory. Admin only."""
    result = reload_skills()
    return {
        "status": "reloaded",
        "skills_loaded": result["loaded"],
        "total_skills": result["total"],
        "skill_ids": result["skill_ids"],
    }


@router.get("/skills")
async def list_skills_endpoint():
    """List all registered skill IDs."""
    return {"skill_ids": list_skills(), "total": len(list_skills())}


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "sessions": get_store().session_count(),
        "llm_enabled": llm_enabled(),
    }


@router.get("/providers")
async def list_providers():
    """Return configured LLM providers and agent model mappings.

    Useful for frontends to show which models are available.
    """
    cfg = ConfigManager.get()
    providers = []
    for pid in cfg.list_providers():
        p = cfg.get_provider(pid)
        if p:
            providers.append({
                "id": p.id,
                "name": p.name,
                "protocol": p.protocol,
                "models": [m.get("id") for m in p.models],
            })
    return {
        "providers": providers,
        "agent_models": cfg.list_agent_models(),
        "voice": cfg.get_voice_config(),
        "loaded": cfg.loaded,
    }
