"""轻量检索增强：查询扩展、文档去重和词法重排。"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from langchain_core.documents import Document
from loguru import logger

from app.config import config

_cross_encoder = None

_QUERY_EXPANSIONS = {
    "stack_overflow": "栈溢出 无限递归 递归深度 dbstack 迭代替代",
    "栈溢出": "stack_overflow 无限递归 递归深度 dbstack 迭代替代",
    "access_violation": "访问违规 MEX 指针 NULL 野指针 mxGetPr",
    "访问违规": "access_violation MEX 指针 NULL 野指针 mxGetPr",
    "out_of_memory": "内存不足 OOM memory whos 预分配 分块 datastore tall array 动态扩容",
    "oom": "内存不足 out_of_memory memory whos 预分配 分块 datastore tall array 动态扩容",
    "内存": "OOM out_of_memory memory whos 预分配 分块 datastore tall array 动态扩容",
    "ram": "MATLAB 内存 memory OOM 内存泄漏 clear 未释放 循环增长",
    "cpu_percent": "CPU 高负载 profile profiler timeit parfor 计算密集型 性能分析",
    "cpu": "高负载 profile profiler timeit parfor 计算密集型 性能分析",
    "mex": "MEX mexw64 访问违规 指针 mxGetPr mxMalloc mxCalloc mxFree",
    "unknown": "未知退出 进程崩溃 直接退出 CrashDumps 系统事件查看器 进程退出码 诊断方法",
    "未找到崩溃日志": "未知退出 进程崩溃 直接退出 CrashDumps 系统事件查看器 进程退出码 诊断方法",
}


def expand_query(query: str) -> str:
    """根据告警关键词补充 MATLAB 运维同义词。"""
    query_lower = query.lower()
    has_specific_failure = any(
        marker in query_lower
        for marker in ("oom", "out_of_memory", "内存持续", "栈溢出", "access_violation", "访问违规", "mexw64")
    )
    additions = [
        terms
        for key, terms in _QUERY_EXPANSIONS.items()
        if key in query_lower
        and not (has_specific_failure and key in {"unknown", "未找到崩溃日志"})
    ]
    return " ".join([query, *additions]) if additions else query


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    tokens = set(re.findall(r"[a-z0-9_+.%-]+", normalized))
    for sequence in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(sequence) == 1:
            tokens.add(sequence)
        else:
            tokens.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def _normalize(values: Iterable[float]) -> list[float]:
    values = list(values)
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [1.0] * len(values)
    return [(value - low) / (high - low) for value in values]


def _source_name(doc: Document) -> str:
    return str(doc.metadata.get("_file_name") or doc.metadata.get("_source") or "")


def rerank_candidates(
    query: str,
    candidates: list[tuple[Document, float]],
    k: int,
    dense_weight: float = 0.75,
    score_higher_is_better: bool = False,
) -> list[Document]:
    """按文档聚合候选分块，并结合向量分数和词法重合度重排。"""
    if not candidates or k <= 0:
        return []

    grouped: dict[str, list[tuple[Document, float]]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        doc, score = candidate
        grouped[_source_name(doc) or f"__chunk_{index}"].append((doc, score))

    rows: list[dict[str, object]] = []
    query_tokens = _tokens(expand_query(query))
    for source, chunks in grouped.items():
        best_doc, best_score = (
            max(chunks, key=lambda item: item[1])
            if score_higher_is_better
            else min(chunks, key=lambda item: item[1])
        )
        dense_relevance = best_score if score_higher_is_better else -best_score
        document_tokens = _tokens("\n".join(doc.page_content for doc, _ in chunks))
        lexical_score = (
            len(query_tokens & document_tokens) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        rows.append(
            {
                "source": source,
                "doc": best_doc,
                "dense": dense_relevance,
                "lexical": lexical_score,
            }
        )

    raw_dense_scores = [float(row["dense"]) for row in rows]
    if score_higher_is_better and all(0.0 <= score <= 1.0 for score in raw_dense_scores):
        dense_scores = raw_dense_scores
    else:
        dense_scores = _normalize(raw_dense_scores)
    for row, dense_score in zip(rows, dense_scores, strict=True):
        row["score"] = dense_weight * dense_score + (1 - dense_weight) * float(row["lexical"])

    rows.sort(key=lambda row: (float(row["score"]), float(row["dense"])), reverse=True)
    results: list[Document] = []
    for row in rows[:k]:
        doc = row["doc"]
        assert isinstance(doc, Document)
        metadata = dict(doc.metadata)
        metadata.update(
            {
                "_retrieval_score": float(row["score"]),
                "_dense_score": float(row["dense"]),
                "_lexical_score": float(row["lexical"]),
            }
        )
        results.append(Document(page_content=doc.page_content, metadata=metadata))
    return results


def _get_cross_encoder():
    """延迟加载，避免应用启动时阻塞模型初始化。"""
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder

        _cross_encoder = CrossEncoder(
            config.rag_reranker_model,
            cache_folder=".hf-cache",
            max_length=512,
        )
    return _cross_encoder


def score_documents(query: str, documents: list[Document]) -> list[float]:
    """返回 Cross-Encoder 原始相关性分数。"""
    model = _get_cross_encoder()
    pairs = [(query, str(doc.metadata.get("_child_content") or doc.page_content)) for doc in documents]
    return [float(score) for score in model.predict(pairs, show_progress_bar=False)]


def rerank_documents(
    query: str,
    documents: list[Document],
    k: int,
    *,
    reranker_weight: float | None = None,
    cross_scores: list[float] | None = None,
    initial_scores: list[float] | None = None,
) -> list[Document]:
    """融合 RRF 初始名次与 Cross-Encoder 分数，并按文档来源去重。"""
    if not documents or k <= 0:
        return []
    if not config.rag_reranker_enabled:
        return documents[:k]

    weight = config.rag_reranker_weight if reranker_weight is None else reranker_weight
    if not 0 <= weight <= 1:
        raise ValueError("reranker_weight 必须在 0 到 1 之间")
    scores = cross_scores if cross_scores is not None else score_documents(query, documents)
    if len(scores) != len(documents):
        raise ValueError("cross_scores 数量必须与 documents 一致")
    if initial_scores is not None and len(initial_scores) != len(documents):
        raise ValueError("initial_scores 数量必须与 documents 一致")
    normalized_cross = _normalize(scores)
    normalized_initial = (
        _normalize(initial_scores)
        if initial_scores is not None
        else [1 - index / max(len(documents) - 1, 1) for index in range(len(documents))]
    )

    best_by_source: dict[str, tuple[Document, float, float, float]] = {}
    for index, (doc, raw_score, cross_score) in enumerate(
        zip(documents, scores, normalized_cross, strict=True)
    ):
        source = _source_name(doc) or f"__chunk_{index}"
        initial_score = normalized_initial[index]
        raw_initial_score = initial_scores[index] if initial_scores is not None else initial_score
        score = weight * cross_score + (1 - weight) * initial_score
        current = best_by_source.get(source)
        if current is None or score > current[1]:
            best_by_source[source] = (doc, score, raw_score, raw_initial_score)

    ranked = sorted(best_by_source.values(), key=lambda item: item[1], reverse=True)[:k]
    results: list[Document] = []
    for doc, score, raw_score, raw_initial_score in ranked:
        metadata = dict(doc.metadata)
        metadata["_reranker_score"] = score
        metadata["_cross_encoder_raw_score"] = raw_score
        metadata["_reranker_weight"] = weight
        metadata["_initial_retrieval_score"] = raw_initial_score
        metadata["_retrieval_pipeline"] = "dense+bm25->rrf->cross_encoder"
        results.append(Document(page_content=doc.page_content, metadata=metadata))
    return results


def rerank_with_fallback(
    query: str,
    candidates: list[tuple[Document, float]],
    k: int,
    *,
    cross_scores: list[float] | None = None,
) -> list[Document]:
    """重排不可用时保留RRF顺序，不能把可用召回结果变成空列表。"""
    candidate_docs = [doc for doc, _ in candidates]
    try:
        return rerank_documents(
            query,
            candidate_docs,
            k=k,
            initial_scores=[float(score) for _, score in candidates],
            cross_scores=cross_scores,
        )
    except Exception as error:
        logger.warning(f"Cross-Encoder 重排失败，回退到 RRF 结果: {error}")
        results: list[Document] = []
        seen: set[str] = set()
        for doc in candidate_docs:
            source = _source_name(doc)
            if source and source not in seen:
                seen.add(source)
                results.append(doc)
            if len(results) == k:
                break
        return results
