from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.agent.aiops.microcompact import microcompact
from app.tools.result import make_tool_result, normalize_tool_result

microcompact_module = importlib.import_module("app.agent.aiops.microcompact")


@pytest.mark.asyncio
async def test_structured_result_offloads_raw_content_and_preserves_facts(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        microcompact_module,
        "_ARTIFACTS_ROOT",
        str(tmp_path / "artifacts"),
    )
    result = make_tool_result(
        summary="CPU 使用率 92%，共 16 核",
        key_facts={"cpu_usage": 92, "cpu_cores": 16},
        raw_result={"samples": ["sample-data" * 100]},
    )

    update = await microcompact(
        {"past_steps": [("检查 CPU", result)]},
        {"configurable": {"thread_id": "run-1"}},
    )

    compact = update["past_steps"][-1][1]
    assert compact["summary"] == "CPU 使用率 92%，共 16 核"
    assert compact["key_facts"] == {"cpu_usage": 92, "cpu_cores": 16}
    assert "raw_result" not in compact
    assert compact["truncated"] is True
    artifact = Path(compact["raw_ref"])
    assert artifact.exists()
    assert "sample-data" in artifact.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_short_structured_result_is_not_compacted() -> None:
    result = make_tool_result(
        summary="CPU 正常",
        key_facts={"cpu_usage": 20},
    )

    assert await microcompact({"past_steps": [("检查 CPU", result)]}) == {}


def test_generic_dictionary_tool_result_uses_scalar_facts_without_keywords() -> None:
    normalized = normalize_tool_result(
        "system_metrics",
        {
            "cpu_usage": 92,
            "cpu_cores": 16,
            "samples": [1, 2, 3],
            "message": "CPU 负载偏高",
        },
    )

    assert normalized is not None
    assert normalized["summary"] == "CPU 负载偏高"
    assert normalized["key_facts"]["cpu_usage"] == 92
    assert "samples" not in normalized["key_facts"]
    assert normalized["raw_result"]["samples"] == [1, 2, 3]
