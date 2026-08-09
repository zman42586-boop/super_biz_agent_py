from tests.eval_retrieval import build_eval_query, calculate_metrics


def test_build_eval_query_includes_runtime_evidence() -> None:
    scenario = {
        "description": "MATLAB 进程异常退出",
        "alert": {
            "alert_name": "MATLAB.exe 异常退出",
            "metric": "process_exit",
            "evidence": {
                "crash_type": "unknown",
                "crash_log": "no crash dump",
                "system_snapshot": {
                    "cpu_percent": 35.0,
                    "memory_percent": 99.1,
                    "top_processes": [{"name": "MATLAB.exe", "cpu": 15.0, "memory": 94.0}],
                },
            },
        },
    }

    query = build_eval_query(scenario)

    assert "内存使用率 99.1%" in query
    assert "MATLAB.exe" in query
    assert "no crash dump" in query


def test_metrics_deduplicate_retrieved_document_names() -> None:
    metrics = calculate_metrics(
        relevant_docs=["memory", "profiler"],
        retrieved_docs=["memory.md", "memory.md", "profiler.md", "other.md"],
        top_k=3,
    )

    assert metrics["retrieved_docs"] == ["memory.md", "profiler.md", "other.md"]
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_k"] == 2 / 3
    assert metrics["mrr"] == 1.0
