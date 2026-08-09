from langchain_core.documents import Document

from app.services.retrieval_confidence import ConfidenceResult


def _candidate(source: str, score: float = 0.03):
    doc = Document(
        page_content="证据",
        metadata={"_file_name": source, "_parent_content": "父章节", "_child_content": "证据"},
    )
    return doc, score


def test_low_confidence_retries_only_once(monkeypatch) -> None:
    from app.services.vector_store_manager import VectorStoreManager
    import app.services.vector_store_manager as module

    manager = VectorStoreManager.__new__(VectorStoreManager)
    calls: list[str] = []
    monkeypatch.setattr(
        manager,
        "_hybrid_candidates",
        lambda query, candidate_count, expr: (calls.append(query) or [_candidate("a.md")]),
    )
    monkeypatch.setattr(
        manager,
        "_confidence",
        lambda query, candidates: ConfidenceResult(0.2, "low", 0.1, 0.0, 0.2, "a.md"),
    )
    monkeypatch.setattr(module, "score_documents", lambda query, docs: [0.1])

    outcome = manager.search_with_diagnostics("UNKNOWN_X9", k=1)

    assert len(calls) == 2
    assert outcome.attempts == 2
    assert outcome.rewritten is True
    assert outcome.reranker_used is False
    assert outcome.documents[0].metadata["_evidence_sufficient"] is False


def test_semantic_evidence_can_recover_low_lexical_confidence(monkeypatch) -> None:
    from app.services.vector_store_manager import VectorStoreManager
    import app.services.vector_store_manager as module

    manager = VectorStoreManager.__new__(VectorStoreManager)
    scored_queries: list[str] = []
    monkeypatch.setattr(manager, "_hybrid_candidates", lambda *args: [_candidate("semantic.md")])
    monkeypatch.setattr(
        manager,
        "_confidence",
        lambda query, candidates: ConfidenceResult(0.2, "low", 0.1, 0.0, 0.2, "semantic.md"),
    )
    monkeypatch.setattr(
        module,
        "score_documents",
        lambda query, docs: (scored_queries.append(query) or [0.9]),
    )
    monkeypatch.setattr(module, "rerank_with_fallback", lambda query, candidates, k, cross_scores=None: [candidates[0][0]])

    outcome = manager.search_with_diagnostics("同义表达", k=1)

    assert outcome.confidence.level == "medium"
    assert outcome.confidence.semantic_score == 0.9
    assert scored_queries == ["同义表达"]
    assert outcome.reranker_used is True
    assert outcome.documents[0].metadata["_evidence_sufficient"] is True


def test_high_confidence_skips_cross_encoder(monkeypatch) -> None:
    from app.services.vector_store_manager import VectorStoreManager
    from app.services import vector_store_manager as module

    manager = VectorStoreManager.__new__(VectorStoreManager)
    monkeypatch.setattr(manager, "_hybrid_candidates", lambda *args: [_candidate("official.md")])
    monkeypatch.setattr(
        manager,
        "_confidence",
        lambda query, candidates: ConfidenceResult(0.9, "high", 1.0, 1.0, 1.0, "official.md"),
    )
    monkeypatch.setattr(module, "rerank_with_fallback", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not rerank")))

    outcome = manager.search_with_diagnostics("CPU profile", k=1)

    assert outcome.reranker_used is False
    assert outcome.attempts == 1
    assert outcome.documents[0].metadata["_evidence_sufficient"] is True
