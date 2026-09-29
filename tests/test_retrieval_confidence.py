from langchain_core.documents import Document

from app.services.retrieval_confidence import (
    evaluate_rank_confidence,
    rewrite_query_for_retry,
)


def _doc(parent: str, dense_rank: int | None, bm25_rank: int | None) -> Document:
    return Document(
        page_content=f"证据 {parent}",
        metadata={
            "_parent_id": parent,
            "_dense_rank": dense_rank,
            "_bm25_rank": bm25_rank,
        },
    )


def test_enough_dual_top_parents_produce_high_confidence() -> None:
    candidates = [(_doc(f"p{i}", i, i + 1), 0.03) for i in range(1, 6)]

    result = evaluate_rank_confidence(candidates, final_k=5, rank_window=10)

    assert result.level == "high"
    assert result.score == 1.0
    assert result.high_confidence_parent_count == 5


def test_duplicate_children_count_as_one_parent() -> None:
    candidates = [
        (_doc("same-parent", 1, 1), 0.04),
        (_doc("same-parent", 2, 2), 0.03),
        (_doc("other-parent", 3, 3), 0.02),
    ]

    result = evaluate_rank_confidence(candidates, final_k=3, rank_window=10)

    assert result.level == "medium"
    assert result.high_confidence_parent_count == 2
    assert result.score == 2 / 3


def test_single_route_candidate_is_not_high_confidence() -> None:
    candidates = [(_doc("dense-only", 1, None), 0.02)]

    result = evaluate_rank_confidence(candidates, final_k=1, rank_window=10)

    assert result.level == "medium"
    assert result.high_confidence_parent_count == 0


def test_retry_rewrite_keeps_diagnostic_markers_and_is_bounded_text() -> None:
    query = "noise\n" * 200 + "EXCEPTION_ACCESS_VIOLATION module.mexw64 mxGetPr"

    rewritten = rewrite_query_for_retry(query)

    assert rewritten.startswith("MATLAB 运维故障诊断")
    assert "exception_access_violation" in rewritten.lower()
    assert "module.mexw64" in rewritten.lower()
    assert len(rewritten) < 500
