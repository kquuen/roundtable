"""FastAPI APIRouter modules for Roundtable.

Each module owns a domain of endpoints. Imported and mounted in app.py.
"""

from roundtable.routers.auth import router as auth_router
from roundtable.routers.auth import user_router
from roundtable.routers.sessions import router as sessions_router
from roundtable.routers.roundtable import router as roundtable_router
from roundtable.routers.memory import router as memory_router
from roundtable.routers.skills import router as skills_router
from roundtable.routers.review import router as review_router
from roundtable.routers.voice import router as voice_router
from roundtable.routers.debate_rt import router as debate_rt_router
from roundtable.routers.system import router as system_router

__all__ = [
    "auth_router",
    "user_router",
    "sessions_router",
    "roundtable_router",
    "memory_router",
    "skills_router",
    "review_router",
    "voice_router",
    "debate_rt_router",
    "system_router",
]
