"""
通用 Plan-Execute-Replan 状态定义
基于 LangGraph 官方教程实现
"""

from typing import List, TypedDict, Annotated


def _replace(existing: list, update: list) -> list:
    """全量替换 reducer：用新值直接替换旧值（非追加）。

    executor / microcompact 节点需要返回完整列表而非增量，
    这样 microcompact 才能替换最后一步的压缩结果。
    """
    return update


class PlanExecuteState(TypedDict):
    """Plan-Execute-Replan 状态"""

    # 用户输入（任务描述）
    input: str

    # 执行计划（步骤列表）
    plan: List[str]

    # 已执行的步骤历史（全量替换，由各节点自行维护完整列表）
    past_steps: Annotated[List[tuple], _replace]

    # 最终响应/报告
    response: str

    # Level 3 Collapse：折叠后的历史步骤摘要（由 replanner 写入）
    steps_summary: str
