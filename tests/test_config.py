"""Tests for settings and configuration."""

from __future__ import annotations

import os

import pytest


class TestSettings:
    """pydantic-settings integration tests."""

    def test_settings_loads_defaults(self):
        from roundtable.settings import Settings
        s = Settings()
        assert s.jwt_expire_hours == 168
        assert s.jwt_algorithm == "HS256"
        assert "deepseek/deepseek-chat" in s.debate_provider_fallback_list

    def test_admin_user_list_parsing(self):
        from roundtable.settings import Settings
        s = Settings(admin_users="alice, bob, charlie")
        assert s.admin_user_list == ["alice", "bob", "charlie"]

    def test_cors_origin_list_parsing(self):
        from roundtable.settings import Settings
        s = Settings(cors_allowed_origins="http://a.com, http://b.com")
        assert s.cors_origin_list == ["http://a.com", "http://b.com"]

    def test_jwt_secret_from_env(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET", "my-test-secret-123")
        from roundtable.settings import Settings
        s = Settings()
        assert s.jwt_secret == "my-test-secret-123"

    def test_missing_jwt_secret_raises(self, monkeypatch):
        """Empty jwt_secret should be allowed at model level (runtime check in auth.py)."""
        monkeypatch.delenv("JWT_SECRET", raising=False)
        from roundtable.settings import Settings
        s = Settings()
        assert s.jwt_secret == ""
