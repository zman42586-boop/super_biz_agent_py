import csv

import pytest

from app.evaluation.pooling import (
    build_pool, evaluate_dataset, export_annotations, fingerprint, freeze_dataset,
)
from app.evaluation.retrieval import legacy_file_metrics, ranking_metrics


def corpus():
    rows = [{"document_id": "doc", "document_version": "v1", "parent_id": "p1",
             "chunk_id": "c1", "source": "runbook.md", "text": "MATLAB ERR42 memory allocation failed",
             "parent_text": "memory allocation failed", "document_text": "memory allocation failed",
             "title": "MATLAB memory", "section": "ERR42"},
            {"document_id": "other", "document_version": "v1", "parent_id": "p2",
             "chunk_id": "c2", "source": "cpu.md", "text": "CPU profiling",
             "parent_text": "CPU profiling", "document_text": "CPU profiling", "title": "CPU"}]
    return {"chunks": rows, "corpus_hash": fingerprint(rows)}


def test_graded_metrics_dedupe_ids_and_bound_ndcg():
    result = ranking_metrics({"a": 2, "b": 1, "c": 0}, ["a", "a", "b", "c"], 5)
    assert result["recall_at_k"] == 1
    assert result["precision_at_k"] == 2 / 5
    assert result["ndcg_at_k"] == 1
    assert result["mrr"] == 1
    assert legacy_file_metrics(["relevant.md"], ["relevant.md"] * 5, 5)["ndcg_at_k"] == 1


def test_precision_uses_fixed_k_even_for_short_results_and_threshold_is_explicit():
    assert ranking_metrics({"a": 2}, ["a"], 5)["precision_at_k"] == 0.2
    row = ranking_metrics({"a": 1, "b": 2}, ["a", "b"], 5, 2)
    assert row["mrr"] == 0.5 and row["hit_at_1"] == 0
    assert row["ndcg_at_k"] < 1  # Graded gain keeps the distinction between 1 and 2.


def test_unjudged_and_empty_ground_truth_are_not_negative_or_perfect():
    row = ranking_metrics({"a": 2}, ["unknown", "a"], 5)
    assert row["status"] == "needs_judgment" and row["recall_at_k"] is None
    assert row["judged_coverage"] == 0.5
    assert ranking_metrics({}, [], 5)["recall_at_k"] is None
    assert ranking_metrics({"a": 0}, ["a"], 5)["recall_at_k"] is None
    assert ranking_metrics({"a": 2}, [], 5)["recall_at_k"] == 0


def test_legacy_matching_is_exact_not_a_filename_substring():
    row = legacy_file_metrics(["oom"], ["not_oom.md"], 5)
    assert row["recall_at_k"] == 0
    assert legacy_file_metrics(["OOM.MD"], ["oom.md"], 5)["recall_at_k"] == 1


def test_pool_merges_routes_and_retains_each_chunk_rank():
    pool = build_pool(corpus(), [{"id": "q", "text": "MATLAB ERR42 memory"}],
                      dense_runs={"dense": {"q": [{"chunk_id": "c1", "score": 0.9}]}})
    candidate = next(row for row in pool["queries"][0]["candidates"] if row["unit_id"] == "p1")
    assert {row["route"] for row in candidate["matches"]} == {"bm25", "exact", "metadata", "dense"}
    assert all(row["chunk_id"] == "c1" and row["parent_id"] == "p1" for row in candidate["matches"])
    assert candidate["grade"] is None


def test_annotation_freeze_evaluate_and_immutable_version(tmp_path):
    data = corpus()
    pool = build_pool(data, [{"id": "q", "text": "ERR42"}],
                      dense_runs={"dense": {"q": [{"chunk_id": "c1"}, {"chunk_id": "c2"}]}})
    path = tmp_path / "annotations.csv"
    export_annotations(pool, path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row.update(grade="2" if row["unit_id"] == "p1" else "0", reviewer="test_fixture",
                   reason="Synthetic test judgment, not a human study")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    output = tmp_path / "v1.json"
    dataset = freeze_dataset(pool, path, version="test-v1", output=output)
    with pytest.raises(FileExistsError):
        freeze_dataset(pool, path, version="test-v1", output=output)
    run = {"corpus_hash": data["corpus_hash"], "granularity": "parent", "rankings": {"q": ["p1", "p2"]}}
    assert evaluate_dataset(dataset, run)["summary"]["precision_at_k"] == 0.2
    run["rankings"]["q"] = ["unjudged_parent"]
    assert evaluate_dataset(dataset, run)["comparable"] is False
    dataset["annotations"][0]["grade"] = 0
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluate_dataset(dataset, run)


def test_pool_rejects_changed_corpus_and_unknown_rankings():
    data = corpus()
    data["chunks"][0]["text"] = "changed"
    with pytest.raises(ValueError, match="hash"):
        build_pool(data, [{"id": "q", "text": "ERR42"}])
    with pytest.raises(ValueError, match="unknown chunk"):
        build_pool(corpus(), [{"id": "q", "text": "ERR42"}],
                   dense_runs={"dense": {"q": [{"chunk_id": "nonexistent"}]}})


def test_eval_query_does_not_include_hidden_cause_description():
    from tests.eval_retrieval import build_eval_query

    text = build_eval_query({"description": "SECRET_LEAK_ROOT_CAUSE",
                             "alert": {"alert_name": "process disappeared"}})
    assert "SECRET" not in text and "process disappeared" in text


def test_v2_uses_the_same_deduplicated_file_metrics():
    from langchain_core.documents import Document
    from tests.eval_retrieval_v2 import _metrics

    docs = [Document(page_content="same file", metadata={"_file_name": "relevant.md", "_parent_id": f"p{i}"}) for i in range(5)]
    result = _metrics(["relevant.md"], docs, 5)
    assert result["ndcg_at_5"] == 1 and result["precision_at_5"] == 0.2


def test_live_snapshot_ids_ignore_random_milvus_ids_and_separate_same_named_sources():
    from app.evaluation.live_pool import snapshot_rows

    rows = [{"id": "uuid1", "content": "child", "metadata": {"_source": "a/same.md", "_parent_content": "parent"}},
            {"id": "uuid2", "content": "child", "metadata": {"_source": "b/same.md", "_parent_content": "parent"}}]
    before, _ = snapshot_rows(rows)
    rows[0]["id"] = "reindexed_uuid"
    after, _ = snapshot_rows(rows)
    assert before["corpus_hash"] == after["corpus_hash"]
    assert len({row["document_id"] for row in before["chunks"]}) == 2
