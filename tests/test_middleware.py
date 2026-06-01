"""Tests for global middleware and exception handlers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from roundtable.app import app


client = TestClient(app)


class TestRequestID:
    """Request ID middleware tests."""

    def test_request_id_header_returned(self):
        """Every response should include an X-Request-ID header."""
        r = client.get("/health")
        assert r.status_code == 200
        assert "X-Request-ID" in r.headers
        assert len(r.headers["X-Request-ID"]) > 0

    def test_custom_request_id_preserved(self):
        """If client sends X-Request-ID, it should be echoed back."""
        custom_id = "my-test-id-123"
        r = client.get("/health", headers={"X-Request-ID": custom_id})
        assert r.headers["X-Request-ID"] == custom_id


class TestExceptionSanitization:
    """Exception handler tests — verify no internal details leak."""

    def test_404_has_request_id(self):
        r = client.get("/nonexistent-endpoint-xyz")
        assert r.status_code == 404
        body = r.json()
        assert "error" in body
        assert "code" in body
        assert "request_id" in body
        assert "traceback" not in str(body).lower()

    def test_validation_error_sanitized(self):
        r = client.post("/auth/register", json={})
        assert r.status_code == 422
        body = r.json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "request_id" in body
        # Should NOT contain raw Pydantic error details with internal field names
        assert "loc" not in body
        assert "type" not in body or body.get("type") != "missing"

    def test_401_has_request_id(self):
        r = client.post("/skills/reload")
        assert r.status_code == 401
        body = r.json()
        assert "request_id" in body
        assert body["code"] == "HTTP_401"

    def test_403_admin_endpoint(self):
        """Authenticated non-admin should get 403 with sanitized response."""
        import uuid as _uuid
        username = f"user_{_uuid.uuid4().hex[:8]}"
        r = client.post(
            "/auth/register",
            json={"username": username, "email": f"{username}@example.com", "password": "password123"},
        )
        assert r.status_code == 201
        token = r.json()["access_token"]

        r = client.post(
            "/skills/reload",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403
        body = r.json()
        assert "request_id" in body
        assert body["code"] == "HTTP_403"
        assert "traceback" not in str(body).lower()
