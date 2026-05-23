"""Tests for session_id validation — path traversal prevention."""

import pytest

from roundtable.store import _validate_session_id


class TestValidateSessionId:
    """Verify that _validate_session_id rejects malicious inputs."""

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            _validate_session_id("../../etc/passwd")

    def test_special_chars_rejected(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            _validate_session_id("s; rm -rf /")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_session_id("")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            _validate_session_id("s_001\\..\\secret")

    def test_dot_dot_rejected(self):
        with pytest.raises(ValueError, match="Invalid session_id"):
            _validate_session_id("../etc")

    def test_valid_alphanumeric(self):
        assert _validate_session_id("abc123") == "abc123"

    def test_valid_with_underscore_hyphen(self):
        assert _validate_session_id("s_001-test") == "s_001-test"

    def test_valid_hex_id(self):
        assert _validate_session_id("a1b2c3d4") == "a1b2c3d4"
