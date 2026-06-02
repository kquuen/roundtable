"""FastAPI application entry point for Roundtable.

Usage:
    $env:DEEPSEEK_API_KEY="sk-..."   # PowerShell
    uvicorn roundtable.app:app --reload
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

try:
    __version__ = version("roundtable")
except PackageNotFoundError:
    __version__ = "0.2.0"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from roundtable.responses import Utf8JSONResponse
from roundtable.config import ConfigManager
from roundtable.dependencies import get_store
from roundtable.skills import load_from_directory
from roundtable.logging_config import setup_logging
from roundtable.middleware import (
    request_id_middleware,
    validation_exception_handler,
    http_exception_handler,
    global_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from roundtable.routers import (
    auth_router,
    user_router,
    sessions_router,
    roundtable_router,
    memory_router,
    skills_router,
    review_router,
    voice_router,
    debate_rt_router,
    system_router,
    agents_router,
    payment_router,
)
from roundtable.routers.voice import (
    MAX_VOICE_CONCURRENT,
    _voice_semaphore,
    _voice_active_count,
)

setup_logging()
logger = logging.getLogger("roundtable.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = ConfigManager.get()
    if cfg.loaded:
        logger.info(
            "Config loaded: %d providers, %d agent models",
            len(cfg.list_providers()),
            len(cfg.list_agent_models()),
        )
    else:
        logger.warning("Config not loaded — running in mock mode")
    logger.info("Loaded %d persisted sessions", get_store().session_count())

    yaml_loaded = load_from_directory()
    if yaml_loaded:
        logger.info("Loaded %d YAML skills from skills/", yaml_loaded)

    # Sync agent registry V2 to DB
    try:
        from roundtable.routers.agents import _sync_registry_to_db
        synced = _sync_registry_to_db()
        logger.info("Synced %d agents from registry.json to DB", synced)
    except Exception as e:
        logger.warning("Agent registry sync failed: %s", e)

    # Pre-seed admin account if configured
    try:
        from roundtable.auth import ensure_admin_user
        admin_token = ensure_admin_user()
        if admin_token:
            logger.info(
                "Admin account ready. Pre-generated token (first 20 chars): %s...",
                admin_token[:20],
            )
    except Exception as e:
        logger.warning("Admin pre-seed failed: %s", e)

    yield


app = FastAPI(
    title="圆桌会议 Roundtable API",
    version=__version__,
    description="AI 专家圆桌工作台后端 API — LLM 驱动 + 持久化",
    lifespan=lifespan,
    default_response_class=Utf8JSONResponse,
)

# CORS — allow all origins in dev, restrict via env var in production
_allowed_origins = [
    o.strip()
    for o in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")
    if o.strip()
]

if "*" in _allowed_origins:
    logger.warning(
        "CORS: allow_origins='*' is incompatible with allow_credentials=True. "
        "Browsers will reject credentialed requests from cross-origin pages. "
        "Set CORS_ALLOWED_ORIGINS to specific origins in production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(request_id_middleware)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# ── Domain routers ──
app.include_router(auth_router)
app.include_router(user_router)
app.include_router(sessions_router)
app.include_router(roundtable_router)
app.include_router(memory_router)
app.include_router(skills_router)
app.include_router(review_router)
app.include_router(voice_router)
app.include_router(debate_rt_router)
app.include_router(system_router)
app.include_router(agents_router)
app.include_router(payment_router)

# ── 前端 SPA fallback（必须在所有 API 路由之后） ──
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    @app.get("/", response_class=HTMLResponse)
    async def serve_frontend():
        return (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    app.mount("/css", StaticFiles(directory=str(_FRONTEND_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(_FRONTEND_DIR / "js")), name="js")
