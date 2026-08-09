from types import SimpleNamespace

from app.services.retrieval_confidence import ConfidenceResult
from app.services.retrieval_context import format_diagnostic_context


def test_empty_retrieval_still_requires_unknown_cause_and_more_evidence() -> None:
    outcome = SimpleNamespace(
        documents=[],
        confidence=ConfidenceResult(0.0, "low", 0.0, 0.0, 0.0, ""),
        attempts=2,
        rewritten=True,
        reranker_used=False,
    )

    context = format_diagnostic_context("UNKNOWN_ERROR", outcome)

    assert "未检索到可用知识库证据" in context
    assert "原因未确定" in context
    assert "需要补充" in context
