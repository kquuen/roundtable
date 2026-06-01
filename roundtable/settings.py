"""Application settings via pydantic-settings.

All configuration is env-driven with sensible defaults.
Priority: env vars > .env file > defaults.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──
    database_path: str = "data/roundtable.db"

    # ── Auth ──
    jwt_secret: str = ""  # Must be set via env; empty triggers validation at runtime
    jwt_expire_hours: int = 168  # 7 days
    jwt_algorithm: str = "HS256"
    admin_users: str = ""  # Comma-separated list of admin usernames

    # ── Provider fallback preferences ──
    debate_provider_fallbacks: str = (
        "deepseek/deepseek-chat,anthropic/claude-sonnet-4-20250514,openai/gpt-4o"
    )

    # ── Voice / System ──
    voice_max_concurrent: int = 50
    admin_token: str = ""

    # ── Feature flags ──
    debug: bool = False

    # ── CORS ──
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def admin_user_list(self) -> List[str]:
        return [u.strip() for u in self.admin_users.split(",") if u.strip()]

    @property
    def debate_provider_fallback_list(self) -> List[str]:
        return [p.strip() for p in self.debate_provider_fallbacks.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
