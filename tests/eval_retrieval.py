"""RAG 文档级检索评测：Recall@K、Precision@K、MRR。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document


def _doc_matches(doc_filename: str, relevant_doc: str) -> bool:
    name_lower = doc_filename.lower()
    rel_lower = relevant_doc.lower()
    if rel_lower in name_lower:
        return True
    return rel_lower.replace(".md", "").replace("_", " ") in name_lower.replace(
        ".md", ""
    ).replace("_", " ")


def _document_name(doc: Document) -> str:
    return str(doc.metadata.get("_file_name") or doc.metadata.get("_source") or "")


def _deduplicate(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
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
        scenario.get("description", ""),
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
    """按唯一文档名计算指标，避免同一来源的多个分块重复计数。"""
    retrieved = _deduplicate(retrieved_docs)[:top_k]
    if not relevant_docs:
        return {
            "relevant_docs": [],
            "retrieved_docs": retrieved,
            "recall_at_k": 1.0,
            "precision_at_k": 1.0,
            "mrr": 1.0,
        }

    matched = sum(
        any(_doc_matches(name, relevant) for name in retrieved)
        for relevant in relevant_docs
    )
    useful = sum(
        any(_doc_matches(name, relevant) for relevant in relevant_docs)
        for name in retrieved
    )
    reciprocal_rank = next(
        (
            1.0 / rank
            for rank, name in enumerate(retrieved, 1)
            if any(_doc_matches(name, relevant) for relevant in relevant_docs)
        ),
        0.0,
    )
    return {
        "relevant_docs": relevant_docs,
        "retrieved_docs": retrieved,
        "recall_at_k": matched / len(relevant_docs),
        "precision_at_k": useful / len(retrieved) if retrieved else 0.0,
        "mrr": reciprocal_rank,
    }


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
