"""RAG 文档级检索评测：Recall@K、Precision@K、MRR。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document
from app.evaluation.retrieval import legacy_file_metrics



def _document_name(doc: Document) -> str:
    return str(doc.metadata.get("_file_name") or doc.metadata.get("_source") or "")


def _deduplicate(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".md")
        if key and key not in seen:
            seen.add(key)
            result.append(name)
    return result


def build_eval_query(scenario: dict[str, Any]) -> str:
    """从告警正文与运行时证据构造检索查询。"""
    alert = scenario.get("alert", {})
    evidence = alert.get("evidence", {})
    parts = [
        alert.get("alert_name", ""),
        f"指标 {alert.get('metric', '')}",
        evidence.get("crash_type", ""),
        str(evidence.get("crash_log", ""))[:800],
    ]

    snapshot = evidence.get("system_snapshot", {})
    if snapshot:
        cpu_percent = float(snapshot.get("cpu_percent", 0))
        memory_percent = float(snapshot.get("memory_percent", 0))
        resource_pressure = cpu_percent >= 85 or memory_percent >= 85
        if cpu_percent >= 85 or alert.get("metric") == "cpu_percent":
            parts.append(f"CPU 使用率 {snapshot['cpu_percent']}%")
        if memory_percent >= 85 or alert.get("metric") == "memory_percent":
            parts.append(f"内存使用率 {snapshot['memory_percent']}%")
        if resource_pressure:
            for process in snapshot.get("top_processes", []):
                parts.append(
                    f"进程 {process.get('name', '')} CPU {process.get('cpu', '')}% "
                    f"内存 {process.get('memory', '')}%"
                )

    recent_points = evidence.get("recent_points", [])
    if recent_points:
        parts.append("近期指标 " + " ".join(f"{ts}:{value}" for ts, value in recent_points))
    return " ".join(str(part) for part in parts if part not in (None, ""))


def calculate_metrics(
    relevant_docs: list[str], retrieved_docs: list[str], top_k: int
) -> dict[str, Any]:
    """Legacy positive-only document labels; fixed K and exact filename stems."""
    result = legacy_file_metrics(relevant_docs, retrieved_docs, top_k)
    return {**result, "relevant_docs": relevant_docs,
            "retrieved_docs": _deduplicate(retrieved_docs)[:top_k]}



def compute_metrics(
    scenario: dict[str, Any],
    top_k: int = 5,
    search_fn: Callable[[str, int], list[Document]] | None = None,
) -> dict[str, Any]:
    """对单个场景运行检索并计算文档级指标。"""
    relevant = scenario.get("relevant_docs", [])
    if not relevant:
        result = calculate_metrics([], [], top_k)
        return {**result, "skipped": True, "message": "场景未标注 relevant_docs"}

    query = build_eval_query(scenario)
    try:
        if search_fn is None:
            from app.services.vector_store_manager import vector_store_manager

            docs = vector_store_manager.search_relevant_documents(query, k=top_k)
        else:
            docs = search_fn(query, top_k)
    except Exception as exc:
        result = calculate_metrics(relevant, [], top_k)
        return {
            **result,
            "skipped": True,
            "message": f"检索服务不可用 ({exc})",
            "query": query,
        }

    result = calculate_metrics(relevant, [_document_name(doc) for doc in docs], top_k)
    return {**result, "skipped": False, "query": query}
