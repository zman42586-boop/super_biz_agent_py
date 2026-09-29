from langchain_core.documents import Document


def _candidate(
    source: str,
    *,
    parent: str | None = None,
    child_index: int = 0,
    dense_rank: int | None = None,
    bm25_rank: int | None = None,
    score: float = 0.03,
):
    doc = Document(
        page_content="证据",
        metadata={
            "_file_name": source,
            "_parent_id": parent or source,
            "_parent_content": "父章节",
            "_child_content": "证据",
            "_child_index": child_index,
            "_dense_rank": dense_rank,
            "_bm25_rank": bm25_rank,
            "_dual_rank_high_confidence": (
                dense_rank is not None
                and bm25_rank is not None
                and dense_rank <= 10
                and bm25_rank <= 10
            ),
        },
    )
    return doc, score


def test_empty_retrieval_retries_only_once(monkeypatch) -> None:
    from app.services.vector_store_manager import VectorStoreManager

    manager = VectorStoreManager.__new__(VectorStoreManager)
    calls: list[str] = []
    monkeypatch.setattr(
        manager,
        "_hybrid_candidates",
        lambda query, candidate_count, expr: (calls.append(query) or []),
    )

    outcome = manager.search_with_diagnostics("UNKNOWN_X9", k=1)

    assert len(calls) == 2
    assert outcome.attempts == 2
    assert outcome.rewritten is True
    assert outcome.reranker_used is False
    assert outcome.documents == []
    assert outcome.confidence.level == "low"


def test_insufficient_dual_rank_parents_use_cross_encoder(monkeypatch) -> None:
    import app.services.vector_store_manager as module
    from app.services.vector_store_manager import VectorStoreManager

    manager = VectorStoreManager.__new__(VectorStoreManager)
    candidates = [
        _candidate("a.md", dense_rank=1, bm25_rank=1),
        _candidate("b.md", dense_rank=2, bm25_rank=None),
    ]
    monkeypatch.setattr(manager, "_hybrid_candidates", lambda *args: candidates)
    reranked: list[str] = []
    monkeypatch.setattr(module, "score_documents", lambda query, docs: [0.9, 0.7])
    monkeypatch.setattr(
        module,
        "rerank_with_fallback",
        lambda query, values, k, cross_scores=None: (
            reranked.append(query) or [values[0][0], values[1][0]]
        ),
    )

    outcome = manager.search_with_diagnostics("同义表达", k=2)

    assert outcome.confidence.level == "medium"
    assert outcome.confidence.high_confidence_parent_count == 1
    assert reranked == ["同义表达"]
    assert outcome.reranker_used is True


def test_enough_dual_rank_parents_skip_cross_encoder(monkeypatch) -> None:
    from app.services import vector_store_manager as module
    from app.services.vector_store_manager import VectorStoreManager

    manager = VectorStoreManager.__new__(VectorStoreManager)
    candidates = [
        _candidate(f"p{i}.md", dense_rank=i, bm25_rank=i + 1)
        for i in range(1, 6)
    ]
    monkeypatch.setattr(manager, "_hybrid_candidates", lambda *args: candidates)
    monkeypatch.setattr(module, "score_documents", lambda query, docs: [0.9] * len(docs))
    monkeypatch.setattr(
        module,
        "rerank_with_fallback",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not rerank")),
    )

    outcome = manager.search_with_diagnostics("CPU profile", k=5)

    assert outcome.confidence.level == "high"
    assert outcome.confidence.high_confidence_parent_count == 5
    assert outcome.reranker_used is False
    assert len(outcome.documents) == 5
    assert all(doc.metadata["_dual_rank_high_confidence"] is not False for doc in outcome.documents)


def test_rrf_fusion_keeps_both_route_ranks_and_deduplicates_children() -> None:
    from app.services.vector_store_manager import VectorStoreManager

    shared_dense = _candidate("a.md", parent="a", child_index=0, score=0.9)[0]
    shared_bm25 = _candidate("a.md", parent="a", child_index=0, score=8.0)[0]
    dense_only = _candidate("b.md", parent="b", child_index=0, score=0.8)[0]
    bm25_only = _candidate("c.md", parent="c", child_index=0, score=7.0)[0]

    fused = VectorStoreManager._rrf_fuse(
        [(shared_dense, 0.9), (dense_only, 0.8)],
        [(shared_bm25, 8.0), (bm25_only, 7.0)],
        candidate_count=3,
    )

    assert len(fused) == 3
    top_doc, top_score = fused[0]
    assert top_doc.metadata["_parent_id"] == "a"
    assert top_doc.metadata["_dense_rank"] == 1
    assert top_doc.metadata["_bm25_rank"] == 1
    assert top_doc.metadata["_dual_rank_high_confidence"] is True
    assert top_score > fused[1][1]


def test_high_confidence_counts_unique_parents_not_children(monkeypatch) -> None:
    import app.services.vector_store_manager as module
    from app.services.vector_store_manager import VectorStoreManager

    manager = VectorStoreManager.__new__(VectorStoreManager)
    candidates = [
        _candidate("one.md", parent="same", child_index=index, dense_rank=index + 1, bm25_rank=index + 1)
        for index in range(3)
    ]
    monkeypatch.setattr(manager, "_hybrid_candidates", lambda *args: candidates)
    monkeypatch.setattr(module, "score_documents", lambda query, docs: [0.9] * len(docs))
    monkeypatch.setattr(
        module,
        "rerank_with_fallback",
        lambda query, values, k, cross_scores=None: [values[0][0]],
    )

    outcome = manager.search_with_diagnostics("内存不足", k=2)

    assert outcome.confidence.level == "medium"
    assert outcome.confidence.high_confidence_parent_count == 1
    assert outcome.reranker_used is True


def test_reranked_candidates_with_weak_semantic_score_are_marked_insufficient(
    monkeypatch,
) -> None:
    import app.services.vector_store_manager as module
    from app.services.vector_store_manager import VectorStoreManager

    manager = VectorStoreManager.__new__(VectorStoreManager)
    candidates = [_candidate("dense-only.md", dense_rank=1, bm25_rank=None)]
    monkeypatch.setattr(manager, "_hybrid_candidates", lambda *args: candidates)
    monkeypatch.setattr(module, "score_documents", lambda query, docs: [0.1])
    monkeypatch.setattr(
        module,
        "rerank_with_fallback",
        lambda query, values, k, cross_scores=None: [values[0][0]],
    )

    outcome = manager.search_with_diagnostics("没有支持证据的问题", k=1)

    assert outcome.reranker_used is True
    assert outcome.confidence.level == "low"
    assert outcome.confidence.semantic_score == 0.1
    assert outcome.documents[0].metadata["_evidence_sufficient"] is False
