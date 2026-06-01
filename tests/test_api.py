"""Phase 5: FastAPI integration tests."""

import pytest
from fastapi.testclient import TestClient
from roundtable.app import app

client = TestClient(app)


class TestRoot:
    def test_root(self):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "roundtable"

    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_llm_enabled_false_when_provider_key_missing(self, monkeypatch, tmp_path):
        from roundtable.config import ConfigManager

        yaml_content = """
providers:
  deepseek:
    protocol: openai
    base_url: https://api.deepseek.com/v1
    api_key: ""
    timeout: 60
    models:
      - id: deepseek-chat
agent_models:
  product_manager: deepseek/deepseek-chat
"""
        config_path = tmp_path / "providers.yaml"
        config_path.write_text(yaml_content, encoding="utf-8")

        monkeypatch.setattr("roundtable.config.ConfigManager._instance", ConfigManager(config_path=config_path))

        r1 = client.get("/")
        r2 = client.get("/health")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["llm_enabled"] is False
        assert r2.json()["llm_enabled"] is False


class TestSession:
    def test_create_session(self):
        r = client.post("/session/create", json={"title": "Test Meeting", "mode": "meeting"})
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "Test Meeting"
        assert data["mode"] == "meeting"

    def test_get_session_404(self):
        r = client.get("/session/nonexistent")
        assert r.status_code == 404

    def test_get_session_found(self):
        r = client.post("/session/create", json={"title": "Found Me"})
        sid = r.json()["session_id"]
        r2 = client.get(f"/session/{sid}")
        assert r2.status_code == 200
        assert r2.json()["title"] == "Found Me"


class TestEvidence:
    def test_upload_evidence(self):
        r = client.post("/session/create", json={"title": "E"})
        sid = r.json()["session_id"]
        r2 = client.post("/evidence/upload", json={
            "session_id": sid,
            "segments": [{"speaker": "A", "text": "Hello"}],
        })
        assert r2.status_code == 200
        assert r2.json()["chunk_count"] == 1

    def test_upload_invalid_session(self):
        r = client.post("/evidence/upload", json={
            "session_id": "bad",
            "segments": [],
        })
        assert r.status_code == 404

    def test_upload_text_evidence(self):
        r = client.post("/session/create", json={"title": "TXT Evidence"})
        sid = r.json()["session_id"]
        r2 = client.post("/evidence/text", json={
            "session_id": sid,
            "text": "张三：先上线 MVP\n没有说话人也要保留",
        })
        assert r2.status_code == 200
        data = r2.json()
        assert data["chunk_count"] == 2
        assert data["segments"] == [
            {"speaker": "张三", "text": "先上线 MVP"},
            {"speaker": "Speaker", "text": "没有说话人也要保留"},
        ]


class TestRoundtable:
    def test_run(self):
        r = client.post("/session/create", json={"title": "Roundtable Test"})
        sid = r.json()["session_id"]
        r2 = client.post("/roundtable/run", json={"session_id": sid, "agent_count": 3})
        assert r2.status_code == 200
        assert "report" in r2.json()
        assert "# 圆桌会议审查报告" in r2.json()["report"]

    def test_run_lang_en(self):
        """English lang should produce English section titles."""
        r = client.post("/session/create", json={"title": "Test EN", "mode": "meeting"})
        sid = r.json()["session_id"]
        client.post("/evidence/upload", json={
            "session_id": sid,
            "segments": [{"speaker": "PM", "text": "We need to ship the MVP."}],
        })
        r2 = client.post("/roundtable/run", json={
            "session_id": sid, "agent_count": 2, "use_mock": True, "lang": "en",
        })
        assert r2.status_code == 200
        report = r2.json()["report"]
        assert "# Roundtable Review Report" in report
        assert "## Summary" in report
        assert "摘要" not in report

    def test_run_lang_zh_default(self):
        """Default lang should still produce Chinese section titles."""
        r = client.post("/session/create", json={"title": "Test ZH", "mode": "meeting"})
        sid = r.json()["session_id"]
        client.post("/evidence/upload", json={
            "session_id": sid,
            "segments": [{"speaker": "PM", "text": "讨论产品规划"}],
        })
        r2 = client.post("/roundtable/run", json={
            "session_id": sid, "agent_count": 2, "use_mock": True,
        })
        assert r2.status_code == 200
        report = r2.json()["report"]
        assert "# 圆桌会议审查报告" in report
        assert "## 摘要" in report

    def test_quick_roundtable_endpoint_works(self):
        """Quick endpoint should not depend on removed global provider."""
        r = client.post("/roundtable/quick", json={"question": "我应该先做MVP吗？"})
        assert r.status_code == 200
        body = r.json()
        assert "session_id" in body
        assert "report" in body


class TestTeam:
    def test_recommend(self):
        r = client.post("/team/recommend", json={
            "session_id": "s_test",
            "segments": [{"speaker": "A", "text": "后端架构和协议讨论"}],
        })
        assert r.status_code == 200
        data = r.json()
        assert "session_type" in data
        assert len(data["recommended_teams"]) >= 1

    def test_recommend_chinese_encoding(self):
        """Chinese characters should render as-is, not \\uXXXX escapes."""
        r = client.post("/team/recommend", json={
            "session_id": "s_enc",
            "segments": [{"speaker": "PM", "text": "产品需求和用户价值讨论"}],
        })
        assert r.status_code == 200
        body = r.text
        # Chinese characters should appear directly, not as unicode escapes
        assert "\\u" not in body
        # Verify at least one known Chinese team name is present
        assert any(name in body for name in ["产品深挖队", "广域机会发现队", "技术审查队"])


class TestCORS:
    def test_cors_preflight(self):
        """OPTIONS preflight should return CORS headers."""
        r = client.options("/", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        })
        assert r.status_code == 200
        headers_lower = {k.lower() for k in r.headers.keys()}
        assert "access-control-allow-origin" in headers_lower

    def test_cors_post_with_origin(self):
        """POST with Origin header should include CORS headers."""
        r = client.post("/session/create",
            json={"title": "test", "mode": "meeting"},
            headers={"Origin": "http://localhost:3000"},
        )
        assert r.status_code == 201
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
