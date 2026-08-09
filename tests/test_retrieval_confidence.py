from langchain_core.documents import Document

from app.services.retrieval_confidence import evaluate_confidence, rewrite_query_for_retry


def _doc(source: str, text: str, trust: str = "medium") -> Document:
    return Document(page_content=text, metadata={"_file_name": source, "_trust_level": trust})


def test_exact_error_markers_produce_higher_confidence() -> None:
    query = "MATLAB EXCEPTION_ACCESS_VIOLATION mexw64 mxGetPr"
    good = [(_doc("mex.md", query, "high"), 0.032)] * 3
    bad = [(_doc("generic.md", "通用服务不可用说明"), 0.032)] * 3

    good_result = evaluate_confidence(query, good, low_threshold=0.35, high_threshold=0.70)
    bad_result = evaluate_confidence(query, bad, low_threshold=0.35, high_threshold=0.70)

    assert good_result.score > bad_result.score
    assert good_result.level == "high"
    assert bad_result.level == "low"


def test_retry_rewrite_keeps_diagnostic_markers_and_is_bounded_text() -> None:
    query = "noise\n" * 200 + "EXCEPTION_ACCESS_VIOLATION module.mexw64 mxGetPr"

    rewritten = rewrite_query_for_retry(query)

    assert rewritten.startswith("MATLAB 运维故障诊断")
    assert "exception_access_violation" in rewritten.lower()
    assert "module.mexw64" in rewritten.lower()
    assert len(rewritten) < 500
