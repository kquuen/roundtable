"""CLI smoke tests for roundtable.main."""

from __future__ import annotations

from roundtable import main as main_module


def test_main_mock_runs(monkeypatch, tmp_path):
    output_path = tmp_path / "cli_report.md"
    monkeypatch.setattr(
        "sys.argv",
        ["roundtable.main", "--mock", "--output", str(output_path)],
    )

    exit_code = main_module.main()

    assert exit_code == 0
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "# 圆桌会议审查报告" in content
