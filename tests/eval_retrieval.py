"""RAG 检索评测 — Recall@K / Precision@K / MRR。

纯向量计算，不需要 LLM。传入 eval 场景，用向量检索拿 top-K 文档，
与 ground truth 对比计算指标。

依赖 Milvus 在线（需已上传 aiops-docs/*.md 到向量库）。
如果 Milvus 不可用，返回空结果并标记 skipped。
"""

from __future__ import annotations

import re
from typing import Any

from app.services.vector_store_manager import vector_store_manager


def _doc_matches(doc_filename: str, relevant_doc: str) -> bool:
    """判断检索到的文档文件名是否匹配场景标注的 relevant_doc。"""
    name_lower = doc_filename.lower()
    rel_lower = relevant_doc.lower()
    # 精确包含
    if rel_lower in name_lower:
        return True
    # 去掉 .md 后缀后模糊匹配
    if rel_lower.replace(".md", "").replace("_", " ") in name_lower.replace(".md", "").replace("_", " "):
        return True
    return False


def compute_metrics(
    scenario: dict[str, Any],
    top_k: int = 5,
) -> dict[str, Any]:
    """对一个场景做检索评测，返回 Recall@K / Precision@K / MRR。

    Args:
        scenario: eval 场景 dict（含 relevant_docs 字段）
        top_k: 检索返回的文档数

    Returns:
        {relevant_docs, retrieved_docs, recall_at_k, precision_at_k, mrr, skipped}
    """
    relevant = scenario.get("relevant_docs", [])
    if not relevant:
        return {
            "relevant_docs": [],
            "retrieved_docs": [],
            "recall_at_k": 1.0,
            "precision_at_k": 1.0,
            "mrr": 1.0,
            "skipped": True,
            "message": "场景未标注 relevant_docs，跳过检索评测",
        }

    # 从告警场景构造查询
    alert = scenario.get("alert", {})
    query_parts = [
        alert.get("alert_name", ""),
        scenario.get("description", ""),
        alert.get("metric", ""),
    ]
    # 如果有崩溃日志，也加入查询
    evidence = alert.get("evidence", {})
    crash_type = evidence.get("crash_type", "")
    if crash_type:
        query_parts.append(crash_type)
    query = " ".join(p for p in query_parts if p)

    # 检索
    try:
        docs = vector_store_manager.similarity_search(query, k=top_k)
    except Exception as e:
        return {
            "relevant_docs": relevant,
            "retrieved_docs": [],
            "recall_at_k": 0.0,
            "precision_at_k": 0.0,
            "mrr": 0.0,
            "skipped": True,
            "message": f"Milvus 不可用 ({e})，跳过检索评测。请先运行 start-windows.bat。",
        }

    retrieved_names = [
        doc.metadata.get("_file_name", doc.metadata.get("_source", ""))
        for doc in docs
    ]

    # ── Recall@K: relevant_docs 中有几个被搜到了 ──
    matched = 0
    for rel in relevant:
        if any(_doc_matches(rn, rel) for rn in retrieved_names):
            matched += 1
    recall_at_k = matched / len(relevant) if relevant else 1.0

    # ── Precision@K: 搜到的文档里有几个是有用的 ──
    useful = 0
    for rn in retrieved_names:
        if any(_doc_matches(rn, rel) for rel in relevant):
            useful += 1
    precision_at_k = useful / len(retrieved_names) if retrieved_names else 0.0

    # ── MRR (Mean Reciprocal Rank): 第一个相关文档排第几 ──
    mrr = 0.0
    for i, rn in enumerate(retrieved_names, 1):
        if any(_doc_matches(rn, rel) for rel in relevant):
            mrr = 1.0 / i
            break

    return {
        "relevant_docs": relevant,
        "retrieved_docs": retrieved_names,
        "recall_at_k": recall_at_k,
        "precision_at_k": precision_at_k,
        "mrr": mrr,
        "skipped": False,
        "query": query,
    }
