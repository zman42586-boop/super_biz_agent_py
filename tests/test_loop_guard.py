from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from app.agent.aiops.executor import executor
from app.harness.config import HarnessSettings
from app.harness.loop_guard import LoopGuard, LoopGuardPolicy, tool_fingerprint
from app.harness.repository import HarnessRepository
from app.harness.runtime import HarnessRunContext, harness_run_context
from app.harness.tool_gateway import ToolExecutionError, ToolGateway, ToolPolicy
from app.harness.worker import HarnessWorker


class EchoArgs(BaseModel):
    query: str


class EchoTool:
    name = "echo"
    args_schema = EchoArgs

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, arguments):
        self.calls += 1
        return arguments["query"]


def _repository(tmp_path, name: str = "loop-guard.sqlite") -> HarnessRepository:
    path = (tmp_path / name).as_posix()
    return HarnessRepository(f"sqlite+pysqlite:///{path}")


def test_step_guard_blocks_limit_repeat_and_no_progress() -> None:
    guard = LoopGuard(
        LoopGuardPolicy(
            max_steps=3,
            max_repeated_steps=2,
            max_no_progress_steps=2,
        )
    )

    max_steps = guard.evaluate_step("another step", [("a", "1"), ("b", "2"), ("c", "3")])
    assert max_steps.reason == "max_steps"

    repeated = guard.evaluate_step(
        "Query MATLAB logs!",
        [("query matlab logs", "same"), ("QUERY MATLAB LOGS", "same")],
    )
    assert repeated.reason == "repeated_step"

    no_progress = guard.evaluate_step(
        "try another source",
        [("first", "未找到相关数据"), ("second", "执行失败: connection")],
    )
    assert no_progress.reason == "no_progress"


def _observed_step(tool_name: str, facts: dict, summary: str = "observed") -> dict:
    return {
        "schema": "tool_result.v1",
        "status": "success",
        "summary": summary,
        "key_facts": {"tools": [{"tool_name": tool_name, "status": "success", "key_facts": facts}]},
    }


def test_snapshot_progress_ignores_small_fluctuations_and_timestamps() -> None:
    guard = LoopGuard(LoopGuardPolicy(max_no_progress_steps=2))
    steps = [
        (
            "cpu",
            _observed_step(
                "query_cpu_metrics",
                {
                    "service_name": "matlab",
                    "current_cpu_percent": 42,
                    "alert_info": {"threshold": 80, "triggered": False},
                },
            ),
        ),
        (
            "cpu 2",
            _observed_step(
                "query_cpu_metrics",
                {
                    "service_name": "matlab",
                    "current_cpu_percent": 43,
                    "alert_info": {"threshold": 80, "triggered": False},
                    "timestamp": "new sample",
                },
            ),
        ),
        (
            "cpu 3",
            _observed_step(
                "query_cpu_metrics",
                {
                    "service_name": "matlab",
                    "current_cpu_percent": 96,
                    "alert_info": {"threshold": 80, "triggered": True},
                },
            ),
        ),
    ]
    assert guard.evaluate_step("next", steps).allowed
    steps.extend(
        [
            (
                "cpu 4",
                _observed_step(
                    "query_cpu_metrics",
                    {
                        "service_name": "matlab",
                        "current_cpu_percent": 97,
                        "alert_info": {"threshold": 80, "triggered": True},
                    },
                ),
            ),
            (
                "cpu 5",
                _observed_step(
                    "query_cpu_metrics",
                    {
                        "service_name": "matlab",
                        "current_cpu_percent": 96,
                        "alert_info": {"threshold": 80, "triggered": True},
                    },
                ),
            ),
        ]
    )
    assert guard.evaluate_step("next", steps).reason == "no_progress"


def test_log_evidence_ids_detect_new_lines_despite_changing_summary() -> None:
    guard = LoopGuard(LoopGuardPolicy(max_no_progress_steps=2))
    steps = [
        ("logs 1", _observed_step("search_log", {"query": "MATLAB", "log_ids": ["a"]}, "found 1")),
        (
            "logs 2",
            _observed_step("search_log", {"query": "MATLAB", "log_ids": ["a"]}, "found 1 again"),
        ),
        (
            "logs 3",
            _observed_step("search_log", {"query": "MATLAB", "log_ids": ["a", "b"]}, "found 2"),
        ),
    ]
    assert guard.evaluate_step("next", steps).allowed
    steps.append(
        ("logs 4", _observed_step("search_log", {"query": "MATLAB", "log_ids": ["a", "b"]}))
    )
    steps.append(
        ("logs 5", _observed_step("search_log", {"query": "MATLAB", "log_ids": ["a", "b"]}))
    )
    assert guard.evaluate_step("next", steps).reason == "no_progress"


def test_dynamic_same_arguments_use_interval_not_lifetime_cap() -> None:
    guard = LoopGuard(LoopGuardPolicy(max_tool_calls=20, min_dynamic_call_interval_seconds=5))
    old_at = (datetime.now(UTC) - timedelta(seconds=8)).isoformat()
    calls = [
        {
            "tool_name": "query_cpu_metrics",
            "arguments": {"service_name": "matlab"},
            "finished_at": old_at,
        }
        for _ in range(3)
    ]
    assert guard.evaluate_tool_call("query_cpu_metrics", {"service_name": "matlab"}, calls).allowed
    calls[-1]["finished_at"] = datetime.now(UTC).isoformat()
    assert (
        guard.evaluate_tool_call("query_cpu_metrics", {"service_name": "matlab"}, calls).reason
        == "dynamic_tool_interval"
    )
    assert guard.evaluate_tool_call("query_cpu_metrics", {"service_name": "other"}, calls).allowed
    assert tool_fingerprint("query_cpu_metrics", {"a": 1, "b": 2}) == tool_fingerprint(
        "query_cpu_metrics", {"b": 2, "a": 1}
    )


def test_static_same_arguments_allow_three_calls_by_default() -> None:
    guard = LoopGuard(LoopGuardPolicy())
    calls = [{"tool_name": "echo", "arguments": {"query": "same"}} for _ in range(2)]
    assert guard.evaluate_tool_call("echo", {"query": "same"}, calls).allowed
    calls.append({"tool_name": "echo", "arguments": {"query": "same"}})
    assert guard.evaluate_tool_call("echo", {"query": "same"}, calls).reason == "repeated_tool_call"


@pytest.mark.asyncio
async def test_executor_stops_repeated_step_before_llm_call(tmp_path) -> None:
    repository = _repository(tmp_path, "step-loop.sqlite")
    run = repository.create_run(task="repeat step", session_id="loop-step")
    repository.claim_next_run("worker", lease_seconds=30)
    state = {
        "input": "diagnose",
        "plan": ["Query MATLAB logs!"],
        "past_steps": [
            ("query matlab logs", "same evidence"),
            ("QUERY MATLAB LOGS", "same evidence"),
        ],
        "response": "",
        "steps_summary": "",
    }

    with harness_run_context(HarnessRunContext(run["id"], repository)):
        update = await executor(state)

    assert update["plan"] == []
    assert update["loop_guard"]["reason"] == "repeated_step"
    assert "LoopGuard" in update["past_steps"][-1][1]
    guard_events = [
        event
        for event in repository.list_events(run["id"])
        if event["type"] == "loop_guard_triggered"
    ]
    assert guard_events[-1]["payload"]["reason"] == "repeated_step"
    repository.close()


@pytest.mark.asyncio
async def test_tool_guard_blocks_third_identical_call_and_records_event(tmp_path) -> None:
    repository = _repository(tmp_path, "tool-loop.sqlite")
    run = repository.create_run(task="repeat tools", session_id="loop-tool")
    repository.claim_next_run("worker", lease_seconds=30)
    tool = EchoTool()
    guard = LoopGuard(LoopGuardPolicy(max_tool_calls=10, max_repeated_tool_calls=2))
    gateway = ToolGateway({"echo": ToolPolicy(timeout_seconds=1, max_retries=0)}, guard=guard)

    with harness_run_context(HarnessRunContext(run["id"], repository)):
        for step_index in range(2):
            step = repository.start_step(run["id"], step_index, f"echo {step_index}")
            await gateway.execute(
                tool=tool,
                arguments={"query": "same"},
                step_id=step["id"],
                step_index=step_index,
            )

        third = repository.start_step(run["id"], 2, "echo 2")
        with pytest.raises(ToolExecutionError) as exc_info:
            await gateway.execute(
                tool=tool,
                arguments={"query": "same"},
                step_id=third["id"],
                step_index=2,
            )

    assert exc_info.value.code == "LOOP_GUARD_REPEATED_TOOL_CALL"
    assert tool.calls == 2
    assert len(repository.list_tool_calls(run["id"])) == 2
    guard_events = [
        event
        for event in repository.list_events(run["id"])
        if event["type"] == "loop_guard_triggered"
    ]
    assert guard_events[-1]["payload"]["reason"] == "repeated_tool_call"
    repository.close()


@pytest.mark.asyncio
async def test_worker_fails_run_when_total_timeout_expires(tmp_path) -> None:
    repository = _repository(tmp_path, "run-timeout.sqlite")
    run = repository.create_run(task="slow run", session_id="loop-timeout")

    async def slow_executor(_run):
        await asyncio.sleep(0.2)
        yield {"type": "complete", "response": "too late"}

    settings = HarnessSettings(
        database_url="sqlite://",
        lease_seconds=3,
        run_timeout_seconds=0.02,
    )
    worker = HarnessWorker(
        repository,
        settings=settings,
        worker_id="timeout-worker",
        executor=slow_executor,
    )

    assert await worker.run_once() is True
    saved = repository.get_run(run["id"])
    assert saved is not None
    assert saved["status"] == "failed"
    assert saved["error_code"] == "RUN_TIMEOUT"
    guard_events = [
        event
        for event in repository.list_events(run["id"])
        if event["type"] == "loop_guard_triggered"
    ]
    assert guard_events[-1]["payload"]["reason"] == "run_timeout"
    repository.close()


@pytest.mark.asyncio
async def test_worker_preserves_graph_recursion_error_code(tmp_path) -> None:
    repository = _repository(tmp_path, "graph-limit.sqlite")
    run = repository.create_run(task="recursive graph", session_id="loop-graph")

    async def recursive_executor(_run):
        yield {
            "type": "error",
            "error_code": "GRAPH_RECURSION_LIMIT",
            "message": "graph recursion limit reached",
        }

    settings = HarnessSettings(
        database_url="sqlite://",
        lease_seconds=3,
        graph_recursion_limit=7,
    )
    worker = HarnessWorker(
        repository,
        settings=settings,
        worker_id="graph-worker",
        executor=recursive_executor,
    )

    assert await worker.run_once() is True
    saved = repository.get_run(run["id"])
    assert saved is not None
    assert saved["status"] == "failed"
    assert saved["error_code"] == "GRAPH_RECURSION_LIMIT"
    guard_events = [
        event
        for event in repository.list_events(run["id"])
        if event["type"] == "loop_guard_triggered"
    ]
    assert guard_events[-1]["payload"]["reason"] == "graph_recursion_limit"
    assert guard_events[-1]["payload"]["limit"] == 7
    repository.close()
