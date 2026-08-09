"""检索置信度与一次性查询改写。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_core.documents import Document

from app.services.retrieval_reranker import _source_name, _tokens

_GENERIC_TOKENS = {
    "matlab", "crash", "dump", "error", "故障", "异常", "崩溃", "问题", "如何", "排查",
}
_FAULT_PHRASES = (
    "访问违规", "内存不足", "内存泄漏", "栈溢出", "高 CPU", "进程消失", "显存耗尽",
    "驱动崩溃", "无响应", "闪退", "错误码", "事件查看器", "崩溃日志",
)


@dataclass(frozen=True)
class ConfidenceResult:
    score: float
    level: str
    lexical_coverage: float
    exact_marker_coverage: float
    source_dominance: float
    top_source: str
    semantic_score: float = 0.0


def _exact_markers(text: str) -> set[str]:
    markers = set(re.findall(r"\b(?:0x[0-9a-fA-F]+|[A-Z][A-Z0-9_]{3,}|[A-Za-z0-9_.-]+\.(?:dll|mexw64|so))\b", text))
    markers.update(re.findall(r"\b(?:mx[A-Z][A-Za-z0-9_]+|gpuArray|gpuDevice|dbstack|datastore|parfor|timeit)\b", text))
    return {marker.lower() for marker in markers if marker.lower() not in _GENERIC_TOKENS}


def evaluate_confidence(
    query: str,
    candidates: list[tuple[Document, float]],
    *,
    low_threshold: float,
    high_threshold: float,
) -> ConfidenceResult:
    if not candidates:
        return ConfidenceResult(0.0, "low", 0.0, 0.0, 0.0, "")

    top_doc = candidates[0][0]
    top_source = _source_name(top_doc)
    top_source_docs = [doc for doc, _ in candidates[:5] if _source_name(doc) == top_source]
    evidence_text = "\n".join(doc.page_content for doc in top_source_docs or [top_doc]).lower()

    query_tokens = {token for token in _tokens(query) if token not in _GENERIC_TOKENS}
    evidence_tokens = _tokens(evidence_text)
    lexical = len(query_tokens & evidence_tokens) / len(query_tokens) if query_tokens else 0.0

    markers = _exact_markers(query)
    exact = (
        sum(
            marker in evidence_text or marker.replace("_", " ") in evidence_text
            for marker in markers
        ) / len(markers)
        if markers else lexical
    )
    dominance = len(top_source_docs) / min(5, len(candidates))
    trust = str(top_doc.metadata.get("_trust_level", "medium"))
    trust_score = {"high": 1.0, "internal": 0.9, "medium": 0.75}.get(trust, 0.5)

    score = max(0.0, min(1.0, 0.45 * exact + 0.30 * lexical + 0.15 * dominance + 0.10 * trust_score))
    level = "high" if score >= high_threshold else "low" if score < low_threshold else "medium"
    return ConfidenceResult(score, level, lexical, exact, dominance, top_source)


def rewrite_query_for_retry(query: str) -> str:
    """从长日志中保留诊断标识符和故障短语；最多由调用方执行一次。"""
    markers = sorted(_exact_markers(query))
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
