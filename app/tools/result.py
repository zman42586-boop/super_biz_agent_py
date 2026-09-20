"""统一的工具结果协议。

工具可以直接返回该结构；对于尚未迁移、但返回字典的工具，ToolGateway
也可以用 :func:`normalize_tool_result` 生成兼容结构。大字段放在 raw_result，
Microcompact 只需负责卸载 raw_result，不需要理解具体业务。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TOOL_RESULT_SCHEMA = "tool_result.v1"


def make_tool_result(
    *,
    summary: str,
    status: str = "success",
    key_facts: Mapping[str, Any] | None = None,
    raw_result: Any | None = None,
    raw_ref: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    """构造统一 ToolResult；字段保持简单，便于模型和持久化层消费。"""
    result: dict[str, Any] = {
        "schema": TOOL_RESULT_SCHEMA,
        "status": status,
        "summary": str(summary),
        "key_facts": dict(key_facts or {}),
    }
    if raw_result is not None:
        result["raw_result"] = raw_result
    if raw_ref:
        result["raw_ref"] = raw_ref
    if truncated:
        result["truncated"] = True
    return result


def is_tool_result(value: Any) -> bool:
    """判断对象是否符合本项目的 ToolResult 最小协议。"""
    return (
        isinstance(value, Mapping)
        and value.get("schema") == TOOL_RESULT_SCHEMA
        and "status" in value
        and "summary" in value
        and isinstance(value.get("key_facts"), Mapping)
    )


def _small_fact(value: Any, *, depth: int = 0) -> Any | None:
    """提取无需领域关键词的轻量事实，跳过列表和大型嵌套对象。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth >= 2 or not isinstance(value, Mapping):
        return None

    nested: dict[str, Any] = {}
    for key, child in value.items():
        compact = _small_fact(child, depth=depth + 1)
        if compact is not None:
            nested[str(key)] = compact
    return nested or None


def normalize_tool_result(tool_name: str, value: Any) -> dict[str, Any] | None:
    """把字典型旧工具结果适配为统一协议。

    不使用 CPU、日志等领域关键词：只保留顶层标量和小型嵌套字典作为
    key_facts，完整返回值仍放在 raw_result 中。纯文本旧工具返回 ``None``，
    继续走原来的文本兼容路径。
    """
    if is_tool_result(value):
        return dict(value)
    if not isinstance(value, Mapping):
        return None

    safe_value = {str(key): child for key, child in value.items()}
    status = "error" if safe_value.get("error") or safe_value.get("ok") is False else "success"
    summary_value = (
        safe_value.get("summary")
        or safe_value.get("message")
        or safe_value.get("error")
        or f"{tool_name} 执行完成"
    )
    key_facts: dict[str, Any] = {}
    structural_fields = {
        "schema",
        "status",
        "summary",
        "message",
        "error",
        "key_facts",
        "raw_result",
        "raw_ref",
        "truncated",
    }
    for key, child in safe_value.items():
        if key in structural_fields:
            continue
        compact = _small_fact(child)
        if compact is not None:
            key_facts[key] = compact

    return make_tool_result(
        status=status,
        summary=str(summary_value),
        key_facts=key_facts,
        raw_result=safe_value,
    )
