"""Compact, domain-aware catalog for progressive tool disclosure.

The catalog is deliberately separate from the full LangChain/MCP tool schema.  A
model sees only ``domain + name + summary`` during routing; the complete schema is
bound only after a small candidate set has been selected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolMetadata:
    domain: str
    summary: str


# Self-owned tools use reviewed summaries instead of runtime LLM summarisation.
TOOL_METADATA: dict[str, ToolMetadata] = {
    "get_current_time": ToolMetadata("time", "查询指定时区的当前日期和时间"),
    "retrieve_knowledge": ToolMetadata("knowledge", "从知识库检索相关文档和历史经验"),
    "search_log": ToolMetadata("logs", "按关键词和时间范围检索本地运行日志"),
    "query_cpu_metrics": ToolMetadata("monitoring", "查询指定服务的 CPU 使用率和核数"),
    "query_memory_metrics": ToolMetadata("monitoring", "查询指定服务的内存使用情况"),
    "list_lhm_sensors": ToolMetadata("monitoring", "列出本机硬件温度传感器及实时值"),
    "get_lhm_temperature": ToolMetadata("monitoring", "查询匹配的本机硬件温度传感器"),
}

DOMAIN_LABELS = {
    "time": "时间",
    "knowledge": "知识检索",
    "logs": "日志",
    "monitoring": "系统监控",
    "general": "通用",
}

_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "time": ("时间", "日期", "几点", "星期", "timezone", "time", "date"),
    "knowledge": ("知识", "文档", "案例", "经验", "方案", "rag", "knowledge"),
    "logs": ("日志", "报错", "错误", "异常", "exception", "error", "log", "warn"),
    "monitoring": (
        "cpu",
        "内存",
        "memory",
        "温度",
        "传感器",
        "负载",
        "指标",
        "监控",
        "核数",
    ),
}


def tool_name(tool: Any) -> str:
    """Return a stable name for LangChain, MCP, or plain callable tools."""

    return str(getattr(tool, "name", getattr(tool, "__name__", ""))).strip()


def tool_metadata(tool: Any) -> ToolMetadata:
    """Return reviewed metadata, with a compact deterministic fallback."""

    name = tool_name(tool)
    known = TOOL_METADATA.get(name)
    if known is not None:
        return known

    description = str(getattr(tool, "description", "") or "").strip()
    # Unknown third-party tools get only their first sentence/line in the catalog.
    first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
    first_sentence = re.split(r"(?<=[。！？.!?])\s*", first_line, maxsplit=1)[0]
    summary = first_sentence[:80].strip() or f"调用 {name} 工具"
    return ToolMetadata(_infer_domain(f"{name} {summary}"), summary)


def format_tool_catalog(tools: Iterable[Any]) -> str:
    """Format only domain, name, and summary; never include parameter schemas."""

    grouped: dict[str, list[tuple[str, str]]] = {}
    for tool in tools:
        name = tool_name(tool)
        if not name:
            continue
        metadata = tool_metadata(tool)
        grouped.setdefault(metadata.domain, []).append((name, metadata.summary))

    if not grouped:
        return "（当前没有可用工具）"

    lines: list[str] = []
    for domain in sorted(grouped, key=lambda item: (item == "general", item)):
        label = DOMAIN_LABELS.get(domain, domain)
        lines.append(f"[{domain} / {label}]")
        for name, summary in sorted(grouped[domain]):
            lines.append(f"- {name}: {summary}")
    return "\n".join(lines)


def select_tools_by_name(tools: Iterable[Any], names: Iterable[str]) -> list[Any]:
    """Select tools in requested order while removing unknowns and duplicates."""

    available = {tool_name(tool): tool for tool in tools if tool_name(tool)}
    selected: list[Any] = []
    seen: set[str] = set()
    for name in names:
        if name in available and name not in seen:
            selected.append(available[name])
            seen.add(name)
    return selected


def fallback_tool_names(task: str, tools: Iterable[Any], max_tools: int = 3) -> list[str]:
    """Deterministic fallback used when the lightweight model router fails."""

    available = list(tools)
    lowered = task.lower()
    scored: list[tuple[int, str]] = []
    for tool in available:
        name = tool_name(tool)
        metadata = tool_metadata(tool)
        score = 0
        if name.lower() in lowered:
            score += 100
        for hint in _DOMAIN_HINTS.get(metadata.domain, ()):
            if hint in lowered:
                score += 5
        for token in re.findall(r"[a-zA-Z_]+|[\u4e00-\u9fff]{2,}", metadata.summary.lower()):
            if token in lowered:
                score += 1
        if score:
            scored.append((score, name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    names = [name for _, name in scored[:max_tools]]
    if names:
        return names

    # General questions still get one low-risk knowledge tool rather than every schema.
    available_names = {tool_name(tool) for tool in available}
    if "retrieve_knowledge" in available_names:
        return ["retrieve_knowledge"]
    return [tool_name(available[0])] if available else []


def _infer_domain(text: str) -> str:
    lowered = text.lower()
    for domain, hints in _DOMAIN_HINTS.items():
        if any(hint in lowered for hint in hints):
            return domain
    return "general"
