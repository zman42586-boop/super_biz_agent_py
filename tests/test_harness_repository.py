from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.harness.repository import HarnessRepository


def _repository(tmp_path) -> HarnessRepository:
    path = (tmp_path / "harness.sqlite").as_posix()
    return HarnessRepository(f"sqlite+pysqlite:///{path}")


def test_run_step_tool_call_and_event_lifecycle(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_run(task="diagnose MATLAB", session_id="session-1")

    claimed = repository.claim_next_run("worker-1", lease_seconds=30)
    assert claimed is not None
    assert claimed["id"] == run["id"]
    assert claimed["status"] == "running"

    repository.merge_checkpoint(
        run["id"],
        "planner",
        {"plan": ["query memory"], "past_steps": []},
    )
    step = repository.start_step(run["id"], 0, "query memory")
    call, cached = repository.begin_tool_call(
        run_id=run["id"],
        step_id=step["id"],
        tool_name="query_memory_metrics",
        arguments={"service_name": "local"},
        idempotency_key="stable-key",
    )
    assert cached is False

    repository.complete_tool_call(
        call["id"], result={"content": "memory=60%"}, latency_ms=12
    )
    cached_call, cached = repository.begin_tool_call(
        run_id=run["id"],
        step_id=step["id"],
        tool_name="query_memory_metrics",
        arguments={"service_name": "local"},
        idempotency_key="stable-key",
    )
    assert cached is True
    assert cached_call["result"]["content"] == "memory=60%"

    repository.complete_step(step["id"], output="memory is normal")
    repository.merge_checkpoint(
        run["id"],
        "executor",
        {"plan": [], "past_steps": [["query memory", "memory is normal"]]},
    )
    repository.finish_run(run["id"], "final report")

    saved = repository.get_run(run["id"])
    assert saved is not None
    assert saved["status"] == "succeeded"
    assert saved["current_step_index"] == 1
    assert saved["final_report"] == "final report"
    assert repository.list_steps(run["id"])[0]["status"] == "succeeded"
    assert repository.list_tool_calls(run["id"])[0]["attempt_count"] == 1
    assert any(event["type"] == "run_succeeded" for event in repository.list_events(run["id"]))


def test_stale_run_is_interrupted_and_resumed(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_run(task="long task", session_id="session-2")
    repository.claim_next_run("dead-worker", lease_seconds=1)

    future = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=2)
    recovered = repository.recover_stale_runs(now=future)
    assert recovered == 1
    assert repository.get_run(run["id"])["status"] == "interrupted"

    resumed = repository.claim_next_run("new-worker", lease_seconds=30)
    assert resumed is not None
    assert resumed["id"] == run["id"]
    assert resumed["status"] == "running"
    assert resumed["worker_id"] == "new-worker"


def test_cancel_and_manual_resume(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_run(task="cancel me", session_id="session-3")
    cancelled = repository.cancel_run(run["id"])
    assert cancelled["status"] == "cancelled"

    resumed = repository.resume_run(run["id"])
    assert resumed["status"] == "pending"


def test_agentops_metrics_and_quality_evaluation(tmp_path) -> None:
    repository = _repository(tmp_path)

    succeeded = repository.create_run(task="diagnose success", session_id="metrics-1")
    repository.claim_next_run("worker-1", lease_seconds=30)
    step = repository.start_step(succeeded["id"], 0, "query logs")
    call, _ = repository.begin_tool_call(
        run_id=succeeded["id"],
        step_id=step["id"],
        tool_name="query_logs",
        arguments={"host": "local"},
        idempotency_key="metrics-tool-key",
    )
    repository.fail_tool_call(
        call["id"],
        error_code="TOOL_TIMEOUT",
        error_message="temporary timeout",
        latency_ms=10,
        terminal=False,
    )
    retried, cached = repository.begin_tool_call(
        run_id=succeeded["id"],
        step_id=step["id"],
        tool_name="query_logs",
        arguments={"host": "local"},
        idempotency_key="metrics-tool-key",
    )
    assert cached is False
    repository.complete_tool_call(retried["id"], result={"content": "ok"}, latency_ms=20)
    repository.complete_step(step["id"], output="found evidence")
    repository.append_event(succeeded["id"], "loop_guard_triggered", {"reason": "test"})
    repository.append_event(succeeded["id"], "run_resumed", {"reason": "test"})
    repository.finish_run(succeeded["id"], "report")

    failed = repository.create_run(task="diagnose failure", session_id="metrics-2")
    repository.claim_next_run("worker-1", lease_seconds=30)
    repository.fail_run(failed["id"], "RUN_TIMEOUT", "timed out")
    repository.create_run(task="still queued", session_id="metrics-3")

    evaluation = repository.record_evaluation(
        succeeded["id"],
        experiment_name="agentops-v1",
        evaluator_name="human-review",
        score=8.5,
        passed=True,
        metrics={"root_cause_accuracy": 0.9},
        comment="root cause and evidence are correct",
    )
    assert evaluation["score"] == 8.5
    assert repository.list_evaluations(succeeded["id"])[0]["passed"] is True

    summary = repository.get_metrics_summary(window_hours=24)
    assert summary["runs"]["total"] == 3
    assert summary["runs"]["status_counts"]["succeeded"] == 1
    assert summary["runs"]["status_counts"]["failed"] == 1
    assert summary["runs"]["success_rate"] == 0.5
    assert summary["runs"]["average_steps"] == 0.3333
    assert summary["runs"]["loop_guard_rate"] == 0.3333
    assert summary["runs"]["recovery_rate"] == 0.3333
    assert summary["runs"]["error_codes"] == {"RUN_TIMEOUT": 1}
    assert summary["tools"]["retry_rate"] == 1.0
    assert summary["tools"]["success_rate"] == 1.0
    assert summary["tools"]["latency_ms"]["p95"] == 20
    assert summary["quality"]["average_score"] == 8.5
    assert summary["quality"]["pass_rate"] == 1.0


def test_empty_agentops_metrics_use_null_rates(tmp_path) -> None:
    summary = _repository(tmp_path).get_metrics_summary(window_hours=24)
    assert summary["runs"]["total"] == 0
    assert summary["runs"]["success_rate"] is None
    assert summary["tools"]["latency_ms"]["p95"] is None
    assert summary["quality"]["average_score"] is None
