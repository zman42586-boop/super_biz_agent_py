"""基于 Dense/BM25 双路排名一致性的检索置信度。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_core.documents import Document

_FAULT_PHRASES = (
    "访问违规", "内存不足", "内存泄漏", "栈溢出", "高 CPU", "进程消失", "显存耗尽",
    "驱动崩溃", "无响应", "闪退", "错误码", "事件查看器", "崩溃日志",
)


@dataclass(frozen=True)
class ConfidenceResult:
    """一次混合召回的可解释置信度结果。

    ``score`` 表示高置信度唯一 Parent 对最终 Top K 的覆盖比例；它是路由分数，
    不是模型生成的概率。
    """

    score: float
    level: str
    high_confidence_parent_count: int = 0
    required_parent_count: int = 0
    rank_window: int = 10
    candidate_count: int = 0
    semantic_score: float = 0.0


def parent_key(doc: Document) -> str:
    """返回用于最终上下文去重的稳定 Parent 标识。"""
    metadata = doc.metadata
    return str(
        metadata.get("_parent_id")
        or metadata.get("_file_name")
        or metadata.get("_source")
        or doc.page_content
    )


def is_dual_rank_high_confidence(doc: Document, *, rank_window: int) -> bool:
    """同一 Child 同时进入 Dense/BM25 排名前 ``rank_window`` 才算高置信度。"""
    dense_rank = doc.metadata.get("_dense_rank")
    bm25_rank = doc.metadata.get("_bm25_rank")
    return (
        isinstance(dense_rank, int)
        and isinstance(bm25_rank, int)
        and dense_rank <= rank_window
        and bm25_rank <= rank_window
    )


def evaluate_rank_confidence(
    candidates: list[tuple[Document, float]],
    *,
    final_k: int,
    rank_window: int = 10,
) -> ConfidenceResult:
    """按双路排名交集覆盖的唯一 Parent 数量决定是否跳过重排。"""
    if not candidates or final_k <= 0:
        return ConfidenceResult(
            score=0.0,
            level="low",
            required_parent_count=max(0, final_k),
            rank_window=rank_window,
            candidate_count=len(candidates),
        )

    high_parent_keys = {
        parent_key(doc)
        for doc, _ in candidates
        if is_dual_rank_high_confidence(doc, rank_window=rank_window)
    }
    high_count = len(high_parent_keys)
    score = min(1.0, high_count / final_k)
    return ConfidenceResult(
        score=score,
        level="high" if high_count >= final_k else "medium",
        high_confidence_parent_count=high_count,
        required_parent_count=final_k,
        rank_window=rank_window,
        candidate_count=len(candidates),
    )


def rewrite_query_for_retry(query: str) -> str:
    """从长日志中保留诊断标识符和故障短语；最多由调用方执行一次。"""
    markers = sorted(
        {
            marker.lower()
            for marker in re.findall(
                r"\b(?:0x[0-9a-fA-F]+|[A-Z][A-Z0-9_]{3,}|"
                r"[A-Za-z0-9_.-]+\.(?:dll|mexw64|so)|"
                r"mx[A-Z][A-Za-z0-9_]+|gpuArray|gpuDevice|dbstack|datastore|parfor|timeit)\b",
                query,
            )
        }
    )
    phrases = [phrase for phrase in _FAULT_PHRASES if phrase.lower() in query.lower()]
    lines = [line.strip() for line in query.splitlines() if line.strip()]
    compact_line = " ".join(lines[:3])[:240]
    parts = ["MATLAB 运维故障诊断", *markers, *phrases, compact_line]
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.lower()
        if part and key not in seen:
            seen.add(key)
            deduped.append(part)
    return " ".join(deduped)
