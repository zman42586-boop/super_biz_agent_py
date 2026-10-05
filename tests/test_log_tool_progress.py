from __future__ import annotations

from datetime import datetime

from app.tools import log_tool


def test_search_log_returns_stable_ids_and_sees_appended_lines(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(log_tool, "_LOGS_DIR", str(tmp_path))
    log_file = tmp_path / "app_test.log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"{timestamp} | INFO | matlab | MATLAB started",
        f"{timestamp} | WARN | matlab | MATLAB allocation slowed",
    ]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    first = log_tool.search_log.func(query="MATLAB", limit=2)
    first_ids = first["key_facts"]["log_ids"]
    assert len(first_ids) == 2

    log_file.write_text(
        "\n".join(lines + [f"{timestamp} | ERROR | matlab | MATLAB out of memory"]) + "\n",
        encoding="utf-8",
    )
    second = log_tool.search_log.func(query="MATLAB", limit=2)
    second_ids = second["key_facts"]["log_ids"]
    assert first_ids[1] == second_ids[0]
    assert second_ids[1] not in first_ids
    assert second["raw_result"]["logs"][-1]["line_number"] == 3
