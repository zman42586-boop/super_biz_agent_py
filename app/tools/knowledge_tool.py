"""知识检索工具 - 从向量数据库中检索相关信息"""

from typing import Literal

from langchain_core.documents import Document
from langchain_core.tools import tool
from loguru import logger

from app.config import config
from app.services.retrieval_context import (
    format_diagnostic_context,
    format_planner_context,
)
from app.services.vector_store_manager import vector_store_manager


@tool(response_format="content_and_artifact")
def retrieve_knowledge(
    query: str,
    mode: Literal["diagnostic", "plan"] = "diagnostic",
) -> tuple[str, list[Document]]:
    """从知识库中检索相关信息来回答问题

    当用户的问题涉及专业知识、文档内容或需要参考资料时，使用此工具。

    Args:
        query: 用户的问题或查询
        mode: diagnostic 返回完整 Parent 证据；plan 返回受预算限制的 Child 摘要

    Returns:
        Tuple[str, List[Document]]: (格式化的上下文文本, 原始文档列表)
    """
    try:
        logger.info(f"知识检索工具被调用: query='{query}'")

        # 从向量存储中检索相关文档
        outcome = vector_store_manager.search_with_diagnostics(
            query, k=config.rag_top_k
        )
        docs = outcome.documents

        if not docs:
            logger.warning("未检索到相关文档")

        # Planner 只需要紧凑的命中 Child；诊断与最终回答保留完整 Parent。
        context = (
            format_planner_context(query, outcome)
            if mode == "plan"
            else format_diagnostic_context(query, outcome)
        )

        logger.info(f"检索到 {len(docs)} 个相关文档")
        return context, docs

    except Exception as e:
        logger.error(f"知识检索工具调用失败: {e}")
        return (
            "## 知识库证据\n\n检索执行失败，当前没有可验证证据。\n\n"
            "## 推断约束\n\n必须输出“原因未确定”，并说明检索失败及需要补充的日志、dump或系统指标。",
            [],
        )
