from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.agent.aiops.executor import executor
from app.harness.config import HarnessSettings
from app.harness.loop_guard import LoopGuard, LoopGuardPolicy
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

    max_steps = guard.evaluate_step(
        "another step", [("a", "1"), ("b", "2"), ("c", "3")]
    )
    assert max_steps.reason == "max_steps"

    repeated = guard.evaluate_step(
        "Query MATLAB logs!",
        [("query matlab logs", "first"), ("QUERY MATLAB LOGS", "second")],
    )
    assert repeated.reason == "repeated_step"

    no_progress = guard.evaluate_step(
        "try another source",
        [("first", "未找到相关数据"), ("second", "执行失败: connection")],
    )
    assert no_progress.reason == "no_progress"


@pytest.mark.asyncio
async def test_executor_stops_repeated_step_before_llm_call(tmp_path) -> None:
    repository = _repository(tmp_path, "step-loop.sqlite")
    run = repository.create_run(task="repeat step", session_id="loop-step")
    repository.claim_next_run("worker", lease_seconds=30)
    state = {
        "input": "diagnose",
        "plan": ["Query MATLAB logs!"],
        "past_steps": [
            ("query matlab logs", "first evidence"),
            ("QUERY MATLAB LOGS", "second evidence"),
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
    guard = LoopGuard(
        LoopGuardPolicy(max_tool_calls=10, max_repeated_tool_calls=2)
    )
    gateway = ToolGateway(
        {"echo": ToolPolicy(timeout_seconds=1, max_retries=0)}, guard=guard
    )

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
