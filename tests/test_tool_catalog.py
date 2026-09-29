from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.agent.tool_selector import select_tools_for_task
from app.core.llm_factory import llm_factory
from app.tools.catalog import (
    fallback_tool_names,
    format_tool_catalog,
    select_tools_by_name,
    tool_metadata,
)


@dataclass
class FakeTool:
    name: str
    description: str = ""


def _tools() -> list[FakeTool]:
    return [
        FakeTool("get_current_time", "very long parameters: timezone, locale"),
        FakeTool("retrieve_knowledge", "very long retrieval schema"),
        FakeTool("search_log", "very long log schema"),
        FakeTool("query_cpu_metrics", "very long CPU schema"),
        FakeTool("query_memory_metrics", "very long memory schema"),
        FakeTool("get_lhm_temperature", "very long temperature schema"),
    ]


def test_catalog_contains_only_reviewed_name_summary_and_domain() -> None:
    catalog = format_tool_catalog(_tools())

    assert "[monitoring / 系统监控]" in catalog
    assert "query_cpu_metrics: [risk=read] 查询指定服务的 CPU 使用率和核数" in catalog
    assert "search_log: [risk=read] 按关键词和时间范围检索本地运行日志" in catalog
    assert "very long" not in catalog
    assert "parameters" not in catalog


def test_every_known_tool_has_a_domain() -> None:
    domains = {tool.name: tool_metadata(tool).domain for tool in _tools()}

    assert domains == {
        "get_current_time": "time",
        "retrieve_knowledge": "knowledge",
        "search_log": "logs",
        "query_cpu_metrics": "monitoring",
        "query_memory_metrics": "monitoring",
        "get_lhm_temperature": "monitoring",
    }


def test_every_known_tool_is_currently_read_only() -> None:
    assert {tool_metadata(tool).risk_level for tool in _tools()} == {"read"}


def test_unknown_tool_fails_closed_as_dangerous() -> None:
    metadata = tool_metadata(FakeTool("restart_production", "重启生产服务。"))

    assert metadata.risk_level == "dangerous"


def test_selection_ignores_unknown_names_and_preserves_order() -> None:
    selected = select_tools_by_name(
        _tools(),
        ["search_log", "not_registered", "query_cpu_metrics", "search_log"],
    )

    assert [tool.name for tool in selected] == ["search_log", "query_cpu_metrics"]


def test_deterministic_fallback_routes_by_domain() -> None:
    selected = fallback_tool_names("检查 CPU 负载和内存指标", _tools(), max_tools=3)

    assert "query_cpu_metrics" in selected
    assert "query_memory_metrics" in selected
    assert len(selected) <= 3


@pytest.mark.asyncio
async def test_router_failure_falls_back_without_mounting_all_tools(monkeypatch) -> None:
    def fail_model(*args, **kwargs):
        raise RuntimeError("router unavailable")

    monkeypatch.setattr(llm_factory, "create_chat_model", fail_model)
    selected = await select_tools_for_task("搜索最近的 ERROR 日志", _tools())

    assert [tool.name for tool in selected] == ["search_log"]


@pytest.mark.asyncio
async def test_unregistered_dangerous_tool_is_not_mounted(monkeypatch) -> None:
    def fail_model(*args, **kwargs):
        raise RuntimeError("router must not run without an allowed tool")

    monkeypatch.setattr(llm_factory, "create_chat_model", fail_model)
    selected = await select_tools_for_task(
        "重启生产服务",
        [FakeTool("restart_production", "重启生产服务。")],
    )

    assert selected == []
