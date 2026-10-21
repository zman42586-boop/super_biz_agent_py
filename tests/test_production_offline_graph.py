"""Production LangGraph nodes with model, RAG and memory APIs replaced at their boundaries."""

import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from app.harness.config import HarnessSettings
from app.harness.monitoring import HostMonitoringRepository
from app.harness.monitoring_models import MonitoredHost
from app.harness.repository import HarnessRepository
from app.harness.worker import HarnessWorker
from app.tools.host_history import read_host_history


@pytest.mark.asyncio
async def test_production_planner_executor_replanner_offline(tmp_path, monkeypatch):
    repository = HarnessRepository(
        f"sqlite+pysqlite:///{(tmp_path / 'real-graph.sqlite').as_posix()}"
    )
    monitor = HostMonitoringRepository(repository)
    monitor.record_heartbeat("pc", {"cpu_percent": 94, "memory_percent": 97})
    monitor.record_logs(
        "pc",
        [
            {
                "event_id": "matlab-error",
                "source": "runtime.log",
                "offset": 10,
                "text": "ERROR MATLAB:nomem at worker.m:8",
            }
        ],
    )
    with repository._session_factory.begin() as session:
        session.get(MonitoredHost, "pc").last_heartbeat_at = repository.db_now(session) - timedelta(
            seconds=100
        )
    run_id = monitor.scan()[0]
    planner = importlib.import_module("app.agent.aiops.planner")
    executor = importlib.import_module("app.agent.aiops.executor")
    replanner = importlib.import_module("app.agent.aiops.replanner")
    discovery = AsyncMock(side_effect=ConnectionError("Independent Prometheus MCP unavailable"))
    for module in [planner, executor, replanner]:
        monkeypatch.setattr(module, "get_mcp_tools_with_circuit_breaker", discovery)
    monkeypatch.setattr(
        planner,
        "retrieve_knowledge",
        SimpleNamespace(
            name="retrieve_knowledge", ainvoke=AsyncMock(return_value="检索证据不足，无匹配来源")
        ),
    )
    monkeypatch.setattr(
        executor, "select_tools_for_task", AsyncMock(return_value=[read_host_history])
    )
    monkeypatch.setattr(planner, "load_memory_context", lambda: "")

    class FakeLLM:
        def with_structured_output(self, schema, **kwargs):
            if schema.__name__ == "Plan":
                return RunnableLambda(
                    lambda _: schema(steps=["read_host_history 获取故障前指标与日志"])
                )
            if schema.__name__ == "Response":
                return RunnableLambda(
                    lambda _: schema(
                        response="原因未确定。MATLAB 内存错误是故障前事实；主机失联可能与网络或采集器故障有关。"
                    )
                )
            raise AssertionError(f"Unexpected schema {schema}")

        def bind_tools(self, tools):
            assert [t.name for t in tools] == ["read_host_history"]
            return self

        async def ainvoke(self, messages):
            if any(isinstance(m, ToolMessage) for m in messages):
                return AIMessage(
                    content="已获得带 evidence_id 的历史 CPU/内存和 MATLAB 错误日志；原因未确定。"
                )
            return AIMessage(
                content="",
                tool_calls=[{"name": "read_host_history", "args": {}, "id": "history-1"}],
            )

    monkeypatch.setattr(planner.llm_factory, "create_chat_model", lambda **kwargs: FakeLLM())
    service_module = importlib.import_module("app.services.aiops_service")
    archive = AsyncMock()
    monkeypatch.setattr(service_module.memory_writer, "save_incident", archive)
    monkeypatch.setattr("app.services.alert_service.send_diagnosis_report_email", AsyncMock())
    monkeypatch.setattr("app.services.mail_service.send_alert_email", lambda *args: None)
    worker = HarnessWorker(repository, settings=HarnessSettings(database_url="sqlite://"))
    assert await worker.run_once()
    run = repository.get_run(run_id)
    assert run["status"] == "succeeded"
    assert run["current_step_index"] == 1
    assert repository.list_steps(run_id)[0]["status"] == "succeeded"
    assert repository.list_tool_calls(run_id)[0]["tool_name"] == "read_host_history"
    assert {
        e["payload"].get("node")
        for e in repository.list_events(run_id)
        if e["type"] == "checkpoint_saved"
    }.issuperset({"planner", "executor", "replanner"})
    assert "原因未确定" in run["final_report"]
    assert "MATLAB:nomem" in run["final_report"]
    assert "## 已观测事实" in run["final_report"] and "## 检索证据" in run["final_report"]
    assert "## 分析推断" in run["final_report"]
    assert archive.await_args.kwargs["report"] == run["final_report"]
    assert discovery.await_count >= 1
