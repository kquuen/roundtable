"""Tests for roundtable.db users CRUD operations."""

import pytest
from roundtable.db import init_db, create_user, get_user_by_username, get_user_by_id, update_user_custom_keys, list_all_users, _from_json


class TestUsersCRUD:
    def test_create_and_get_user_by_username(self, tmp_path, monkeypatch):
        monkeypatch.setattr("roundtable.db.DB_PATH", tmp_path / "test.db")
        init_db()
        create_user(
            user_id="u_test001",
            username="testuser",
            email="test@example.com",
            hashed_password="hashed_pwd",
            custom_keys={"deepseek": "sk-xxx"},
            monthly_quota=100000,
            monthly_used=100,
        )
        row = get_user_by_username("testuser")
        assert row is not None
        assert row["username"] == "testuser"
        assert row["email"] == "test@example.com"
        assert row["hashed_password"] == "hashed_pwd"
        assert _from_json(row["custom_keys"]) == {"deepseek": "sk-xxx"}
        assert row["monthly_quota"] == 100000
        assert row["monthly_used"] == 100

    def test_get_user_by_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("roundtable.db.DB_PATH", tmp_path / "test.db")
        init_db()
        create_user("u_test002", "byid", "byid@example.com", "hpwd")
        row = get_user_by_id("u_test002")
        assert row is not None
        assert row["username"] == "byid"

    def test_get_user_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("roundtable.db.DB_PATH", tmp_path / "test.db")
        init_db()
        assert get_user_by_username("nonexistent") is None
        assert get_user_by_id("u_nonexistent") is None

    def test_update_user_custom_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("roundtable.db.DB_PATH", tmp_path / "test.db")
        init_db()
        create_user("u_test003", "keyuser", "k@example.com", "hpwd", custom_keys={})
        update_user_custom_keys("u_test003", {"openai": "sk-open"})
        row = get_user_by_id("u_test003")
        assert row is not None
        assert "sk-open" in row["custom_keys"]

    def test_list_all_users(self, tmp_path, monkeypatch):
        monkeypatch.setattr("roundtable.db.DB_PATH", tmp_path / "test.db")
        init_db()
        create_user("u_a", "alice", "a@example.com", "hpwd")
        create_user("u_b", "bob", "b@example.com", "hpwd")
        users = list_all_users()
        assert len(users) == 2
        usernames = {u["username"] for u in users}
        assert usernames == {"alice", "bob"}

    def test_duplicate_username_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("roundtable.db.DB_PATH", tmp_path / "test.db")
        init_db()
        create_user("u_dup", "dupuser", "dup@example.com", "hpwd")
        with pytest.raises(Exception):
            create_user("u_dup2", "dupuser", "dup2@example.com", "hpwd")
