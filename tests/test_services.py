"""Service layer tests."""

from __future__ import annotations

from roundtable.services import RoundtableService


def test_run_pipeline_sync_available():
    service = RoundtableService()
    result = service.run_pipeline_sync(
        session_id="s_sync",
        segments=[{"speaker": "A", "text": "hello"}],
        mode="meeting",
        title="sync test",
        agent_count=2,
    )
    assert result.session_id == "s_sync"
    assert result.mode == "mock"
    assert "# 圆桌会议审查报告" in result.report
