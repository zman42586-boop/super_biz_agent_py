from langchain_core.documents import Document

from app.services import retrieval_reranker
from app.services.retrieval_reranker import (
    expand_query,
    rerank_candidates,
    rerank_documents,
    rerank_with_fallback,
)


def _doc(source: str, content: str) -> Document:
    return Document(page_content=content, metadata={"_file_name": source})


def test_expand_query_adds_matlab_memory_terms() -> None:
    expanded = expand_query("MATLAB 内存持续增长，可能发生 OOM")

    assert "预分配" in expanded
    assert "whos" in expanded
    assert "datastore" in expanded


def test_rerank_returns_unique_sources_and_uses_lexical_evidence() -> None:
    candidates = [
        (_doc("generic.md", "通用服务故障说明"), 0.92),
        (_doc("memory.md", "MATLAB Out of Memory，使用 memory 和 whos 排查"), 0.88),
        (_doc("memory.md", "MATLAB 内存持续增长时检查动态扩容并预分配"), 0.86),
    ]

    ranked = rerank_candidates(
        "MATLAB OOM 内存动态扩容",
        candidates,
        k=2,
        dense_weight=0.6,
        score_higher_is_better=True,
    )

    assert [doc.metadata["_file_name"] for doc in ranked] == ["memory.md", "generic.md"]


def test_cross_encoder_rerank_scores_and_deduplicates_sources(monkeypatch) -> None:
    class FakeCrossEncoder:
        def predict(self, pairs, show_progress_bar=False):
            assert show_progress_bar is False
            return [0.1, 0.9, 0.8]

    monkeypatch.setattr(retrieval_reranker, "_cross_encoder", FakeCrossEncoder())
    docs = [
        _doc("generic.md", "通用信息"),
        _doc("memory.md", "内存泄漏"),
        _doc("memory.md", "OOM 处理"),
    ]

    ranked = rerank_documents("MATLAB 内存泄漏", docs, k=2, reranker_weight=1.0)

    assert [doc.metadata["_file_name"] for doc in ranked] == ["memory.md", "generic.md"]
    assert ranked[0].metadata["_retrieval_pipeline"] == "dense+bm25->rrf->cross_encoder"


def test_rerank_failure_falls_back_to_unique_rrf_results(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(retrieval_reranker, "rerank_documents", fail)
    candidates = [
        (_doc("first.md", "chunk 1"), 0.9),
        (_doc("first.md", "chunk 2"), 0.8),
        (_doc("second.md", "chunk 3"), 0.7),
    ]

    ranked = rerank_with_fallback("query", candidates, k=2)

    assert [doc.metadata["_file_name"] for doc in ranked] == ["first.md", "second.md"]
