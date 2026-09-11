from types import SimpleNamespace

from langchain_core.documents import Document

from app.services.retrieval_confidence import ConfidenceResult
from app.services.retrieval_context import (
    format_diagnostic_context,
    format_planner_context,
)
from app.utils.context_compaction import format_execution_history
from app.utils.token_meter import count_tokens
from tests.eval_retrieval import calculate_metrics


def test_planner_context_uses_matched_child_instead_of_full_parent() -> None:
    parent = "父章节内容。" * 1_000
    child = "固定步长 1e-6 与 Scope 缓存会导致内存持续增长。"
    doc = Document(
        page_content=parent,
        metadata={
            "h1": "Simulink 长时间仿真",
            "h2": "Scope 内存耗尽",
            "_file_name": "case_scope_memory.md",
            "_trust_level": "medium",
            "_parent_content": parent,
            "_matched_child": child,
        },
    )
    outcome = SimpleNamespace(
        documents=[doc],
        confidence=ConfidenceResult(
            0.6, "high", 0.5, 0.5, 0.5, "case_scope_memory.md"
        ),
        attempts=1,
        rewritten=False,
        reranker_used=False,
    )

    full_context = format_diagnostic_context("Scope 内存问题", outcome)
    planner_context = format_planner_context("Scope 内存问题", outcome)

    assert child in planner_context
    assert parent not in planner_context
    assert count_tokens(planner_context) < count_tokens(full_context) * 0.2


def test_final_response_history_reuses_collapsed_steps() -> None:
    past_steps = [
        ("检查 crash dump", "A" * 2_000),
        ("查询系统日志", "B" * 2_000),
        ("检索知识库", "C" * 2_000),
    ]
    raw_history = "\n\n".join(
        f"### 步骤: {step}\n**结果:**\n{result}"
        for step, result in past_steps
    )
    compact_history = format_execution_history(past_steps)

    assert "A" * 121 not in compact_history
    assert "B" * 501 not in compact_history
    assert "C" * 501 not in compact_history
    assert count_tokens(compact_history) < count_tokens(raw_history) * 0.25


def test_context_budgeting_does_not_change_retrieval_metrics() -> None:
    """上下文格式化只能缩短提示词，不能改变已召回文档或其排序。"""
    docs = [
        Document(page_content="完整 Parent A", metadata={"_file_name": "scope_memory.md", "_matched_child": "Scope 内存"}),
        Document(page_content="完整 Parent B", metadata={"_file_name": "mex_crash.md", "_matched_child": "MEX 崩溃"}),
        Document(page_content="完整 Parent C", metadata={"_file_name": "other.md", "_matched_child": "其他"}),
    ]
    outcome = SimpleNamespace(
        documents=docs,
        confidence=ConfidenceResult(0.6, "high", 0.5, 0.5, 0.5, "scope_memory.md"),
        attempts=1,
        rewritten=False,
        reranker_used=False,
    )
    retrieved = [str(doc.metadata["_file_name"]) for doc in outcome.documents]
    before = calculate_metrics(["scope_memory", "mex_crash"], retrieved, top_k=3)

    _ = format_planner_context("MATLAB 崩溃", outcome)
    _ = format_diagnostic_context("MATLAB 崩溃", outcome)

    after = calculate_metrics(
        ["scope_memory", "mex_crash"],
        [str(doc.metadata["_file_name"]) for doc in outcome.documents],
        top_k=3,
    )
    assert after == before
    assert after["recall_at_k"] == 1.0
    assert after["precision_at_k"] == 2 / 3
    assert after["mrr"] == 1.0
