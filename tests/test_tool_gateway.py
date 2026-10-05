from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from app.harness.repository import HarnessRepository
from app.harness.runtime import HarnessRunContext, harness_run_context
from app.harness.tool_gateway import ToolExecutionError, ToolGateway, ToolPolicy


class FakeArgs(BaseModel):
    value: int


class FakeTool:
    name = "fake_tool"
    args_schema = FakeArgs

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, arguments):
        self.calls += 1
        return {"value": arguments["value"] * 2}


class SlowTool(FakeTool):
    name = "slow_tool"

    async def ainvoke(self, arguments):
        self.calls += 1
        await asyncio.sleep(0.05)
        return arguments


class JsonMetricTool:
    name = "query_cpu_metrics"

    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, arguments):
        self.calls += 1
        return json.dumps(
            {
                "service_name": arguments["service_name"],
                "current_cpu_percent": 96,
                "alert_info": {"threshold": 80, "triggered": True},
            }
        )


def _repository(tmp_path) -> HarnessRepository:
    path = (tmp_path / "gateway.sqlite").as_posix()
    return HarnessRepository(f"sqlite+pysqlite:///{path}")


@pytest.mark.asyncio
async def test_gateway_validates_and_reuses_idempotent_result(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_run(task="tool task", session_id="s1")
    repository.claim_next_run("worker", lease_seconds=30)
    step = repository.start_step(run["id"], 0, "call fake")
    gateway = ToolGateway({"fake_tool": ToolPolicy(timeout_seconds=1, max_retries=0)})
    tool = FakeTool()

    with harness_run_context(HarnessRunContext(run["id"], repository)):
        first = await gateway.execute(
            tool=tool, arguments={"value": 3}, step_id=step["id"], step_index=0
        )
        second = await gateway.execute(
            tool=tool, arguments={"value": 3}, step_id=step["id"], step_index=0
        )

    assert "6" in first.content
    assert first.structured_content is not None
    assert first.structured_content["key_facts"]["value"] == 6
    assert json.loads(first.content)["schema"] == "tool_result.v1"
    assert second.cached is True
    assert second.structured_content == first.structured_content
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_gateway_rejects_invalid_arguments_before_call() -> None:
    tool = FakeTool()
    gateway = ToolGateway()

    with pytest.raises(ToolExecutionError) as exc_info:
        await gateway.execute(tool=tool, arguments={"value": "not-an-int"})

    assert exc_info.value.code == "INVALID_ARGUMENTS"
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_gateway_normalizes_json_text_from_monitoring_tool() -> None:
    result = await ToolGateway().execute(
        tool=JsonMetricTool(), arguments={"service_name": "matlab"}
    )
    assert result.structured_content is not None
    assert result.structured_content["key_facts"]["current_cpu_percent"] == 96


@pytest.mark.asyncio
async def test_dynamic_call_replay_in_same_step_uses_cache(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_run(task="cpu", session_id="cpu-cache")
    repository.claim_next_run("worker", lease_seconds=30)
    step = repository.start_step(run["id"], 0, "cpu")
    tool = JsonMetricTool()
    gateway = ToolGateway()
    with harness_run_context(HarnessRunContext(run["id"], repository)):
        first = await gateway.execute(
            tool=tool,
            arguments={"service_name": "matlab"},
            step_id=step["id"],
            step_index=0,
        )
        second = await gateway.execute(
            tool=tool,
            arguments={"service_name": "matlab"},
            step_id=step["id"],
            step_index=0,
        )
    assert first.cached is False
    assert second.cached is True
    assert tool.calls == 1
    repository.close()


@pytest.mark.asyncio
async def test_gateway_retries_timeout_with_bound(tmp_path) -> None:
    repository = _repository(tmp_path)
    run = repository.create_run(task="slow task", session_id="s2")
    repository.claim_next_run("worker", lease_seconds=30)
    step = repository.start_step(run["id"], 0, "call slow")
    tool = SlowTool()
    gateway = ToolGateway({"slow_tool": ToolPolicy(timeout_seconds=0.01, max_retries=1)})

    with harness_run_context(HarnessRunContext(run["id"], repository)):
        with pytest.raises(ToolExecutionError) as exc_info:
            await gateway.execute(
                tool=tool, arguments={"value": 1}, step_id=step["id"], step_index=0
            )

    assert exc_info.value.code == "TIMEOUT"
    assert tool.calls == 2
    calls = repository.list_tool_calls(run["id"])
    assert calls[0]["status"] == "failed"
    assert calls[0]["attempt_count"] == 2
