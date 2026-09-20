"""Two-stage tool selection for progressive schema disclosure."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.core.llm_factory import llm_factory
from app.tools.catalog import (
    fallback_tool_names,
    format_tool_catalog,
    select_tools_by_name,
    tool_metadata,
    tool_name,
)


class ToolSelection(BaseModel):
    """Small routing result; it contains no tool parameter schemas."""

    domains: list[str] = Field(default_factory=list, description="完成当前任务需要的工具领域")
    tool_names: list[str] = Field(
        default_factory=list,
        description="完成当前任务所需的最少工具名称，最多三个",
    )


_SELECTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是工具路由器。根据当前任务，从紧凑工具目录中选择完成任务所需的最少工具。

规则：
- 目录只包含领域、工具名和简短摘要，不包含参数 Schema
- 只能选择目录中真实存在的工具名
- 最多选择 3 个；能用 1 个完成就不要选择 2 个
- 任务明确点名工具时优先选择该工具
- 不需要工具时返回空列表

紧凑工具目录：
{tool_catalog}""",
        ),
        ("user", "当前任务：{task}"),
    ]
)


async def select_tools_for_task(
    task: str,
    tools: Iterable[Any],
    *,
    max_tools: int = 3,
) -> list[Any]:
    """Select a bounded tool set before exposing any full schemas to the executor."""

    available = list(tools)
    if not available:
        return []

    catalog = format_tool_catalog(available)
    routing_failed = False
    try:
        router_model = llm_factory.create_chat_model(
            temperature=0,
            streaming=False,
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=256,
        )
        chain = _SELECTION_PROMPT | router_model.with_structured_output(
            ToolSelection,
            method="function_calling",
        )
        decision = await chain.ainvoke({"task": task, "tool_catalog": catalog})
        requested = (
            decision.tool_names
            if isinstance(decision, ToolSelection)
            else list(decision.get("tool_names", []))
        )
        selected = select_tools_by_name(available, requested[:max_tools])
        # An empty decision is valid when the current step needs reasoning only.
        if not requested:
            logger.info("按需挂载工具: 当前任务不需要工具")
            return []
    except Exception as exc:
        logger.warning(f"工具路由失败，使用确定性降级选择: {exc}")
        selected = []
        routing_failed = True

    if routing_failed or not selected:
        fallback_names = fallback_tool_names(task, available, max_tools=max_tools)
        selected = select_tools_by_name(available, fallback_names)

    logger.info(
        "按需挂载工具: "
        + ", ".join(f"{tool_name(tool)}[{tool_metadata(tool).domain}]" for tool in selected)
    )
    return selected
