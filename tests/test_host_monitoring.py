import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select, update

from app.config import Settings, config
from app.harness.config import HarnessSettings
from app.harness.log_collector import IncrementalLogCollector
from app.harness.models import AgentRun
from app.harness.monitoring import HostMonitoringRepository
from app.harness.monitoring_models import HostEvidence, MonitoredHost, Notification
from app.harness.repository import HarnessRepository
from app.harness.runtime import HarnessRunContext, get_run_context, harness_run_context
from app.harness.tool_gateway import ToolExecutionError, ToolGateway
from app.harness.worker import HarnessWorker
from app.tools.host_history import available_tools, read_host_history


def setup(tmp_path, **kwargs):
    repository = HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'host.sqlite').as_posix()}")
    settings = Settings(
        _env_file=None, oncall_heartbeat_timeout_sec=3, oncall_heartbeat_suspected_sec=1, **kwargs
    )
    return repository, HostMonitoringRepository(repository, settings)


def age(repository, host, seconds):
    with repository._session_factory.begin() as session:
        session.execute(
            update(MonitoredHost)
            .where(MonitoredHost.host == host)
            .values(last_heartbeat_at=repository.db_now(session) - timedelta(seconds=seconds))
        )


def test_normal_loss_restart_and_recovery(tmp_path):
    repository, monitor = setup(tmp_path)
    monitor.record_heartbeat("MATLAB-PC", {"cpu_percent": 70})
    assert monitor.scan() == []
    age(repository, "MATLAB-PC", 2)
    assert monitor.scan() == []
    with repository._session_factory() as session:
        assert session.get(MonitoredHost, "MATLAB-PC").status == "suspected_down"
    monitor.record_heartbeat("MATLAB-PC", {"cpu_percent": 71})
    assert monitor.alerts(True) == []
    age(repository, "MATLAB-PC", 4)
    # Recreate repository/process state; no new heartbeat is needed after restart.
    restarted = HostMonitoringRepository(
        HarnessRepository(str(repository.engine.url)), monitor.settings
    )
    created = restarted.scan()
    assert len(created) == 1
    assert restarted.scan() == []
    run = repository.get_run(created[0])
    assert run["kind"] == "host_down_diagnosis" and run["status"] == "pending"
    assert run["input"]["host"] == "MATLAB-PC"
    assert run["input"]["tools"]["MCP"] == "prometheus_history_only"
    assert len(run["input"]["history"]["metrics"]) == 2
    assert len(restarted.alerts(True)) == 1
    restarted.record_heartbeat("MATLAB-PC", {"cpu_percent": 20})
    assert restarted.alerts(True) == []
    assert restarted.alerts()[0].status == "resolved"
    age(repository, "MATLAB-PC", 4)
    assert len(restarted.scan()) == 1
    assert restarted.alerts(True)[0].evidence.extra["outage_seq"] == 2


def test_concurrent_scans_create_one_outage(tmp_path):
    repository, monitor = setup(tmp_path)
    monitor.record_heartbeat("pc", {})
    age(repository, "pc", 4)
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda _: monitor.scan(), range(8)))
    assert sum(len(r) for r in runs) == 1
    with repository._session_factory() as session:
        assert len(list(session.scalars(select(AgentRun)))) == 1
        assert len(list(session.scalars(select(Notification)))) == 1


def test_history_limits_ttl_dedup_and_host_isolation(tmp_path):
    repository, monitor = setup(
        tmp_path,
        oncall_history_metrics_per_host=3,
        oncall_history_logs_per_host=2,
        oncall_history_ttl_sec=10,
        oncall_history_log_chars=128,
    )
    for name in ["a", "b"]:
        monitor.record_heartbeat(name, {"cpu_percent": 1})
    for i in range(10):
        monitor.record_heartbeat("a", {"cpu_percent": i})
        monitor.record_logs(
            "a", [{"event_id": str(i), "text": "ERROR token=private-value " + "x" * 500}]
        )
    monitor.record_logs("a", [{"event_id": "9", "text": "duplicate"}])
    assert len(monitor.get_history("a")["metrics"]) == 3
    logs = monitor.get_history("a")["logs"]
    assert len(logs) == 2 and all(len(row["payload"]["text"]) <= 128 for row in logs)
    assert "private-value" not in json.dumps(logs)
    assert len(monitor.get_history("b")["metrics"]) == 1
    with repository._session_factory.begin() as session:
        before = session.get(MonitoredHost, "a").last_heartbeat_at
        session.execute(
            update(HostEvidence)
            .where(HostEvidence.host == "a")
            .values(received_at=repository.db_now(session) - timedelta(seconds=11))
        )
    assert monitor.get_history("a") == {"metrics": [], "logs": []}
    monitor.record_logs("a", [{"event_id": "new", "text": "ERROR MATLAB:oom"}])
    with repository._session_factory() as session:
        assert session.get(MonitoredHost, "a").last_heartbeat_at == before
        assert len(list(session.scalars(select(HostEvidence).where(HostEvidence.host == "a")))) == 1
    with pytest.raises(ValueError):
        monitor.record_logs("unregistered", [{"event_id": "x", "text": "ERROR"}])
    with pytest.raises(ValueError):
        monitor.record_logs("a", [{"event_id": "large", "text": "x" * 70000}])


@pytest.mark.asyncio
async def test_offline_tools_do_not_invoke_realtime_tools_when_prometheus_discovery_fails(tmp_path):
    repository, monitor = setup(tmp_path)
    monitor.record_heartbeat("pc", {"cpu_percent": 32})
    age(repository, "pc", 4)
    run_id = monitor.scan()[0]
    run = repository.claim_next_run("worker", 30)
    discovery = AsyncMock(side_effect=ConnectionError("Independent Prometheus MCP unavailable"))
    realtime = SimpleNamespace(name="query_cpu_metrics", ainvoke=AsyncMock())
    with harness_run_context(
        HarnessRunContext(
            run_id, repository, "worker", run["version"], "pc", run["input"]["history"]
        )
    ):
        local, remote = await available_tools([SimpleNamespace(name="search_log")], discovery)
        assert [tool.name for tool in local] == ["read_host_history"] and remote == []
        assert "evidence_id" in await read_host_history.ainvoke({})
        with pytest.raises(ToolExecutionError, match="unavailable"):
            await ToolGateway().execute(tool=realtime, arguments={})
    discovery.assert_awaited_once()
    realtime.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_log_rotation_outage_worker_graph_and_email(tmp_path, monkeypatch):
    """Real HTTP/repository/Worker/LangGraph; graph nodes and SMTP are deterministic doubles."""
    repository, monitor = setup(tmp_path)
    monkeypatch.setattr(config, "alert_webhook_token", "synthetic-token")
    from app.api import alerts
    from app.services.alert_service import AlertService

    service = AlertService(repository_factory=lambda: repository)
    monkeypatch.setattr(alerts, "alert_service", service)
    app = FastAPI()
    app.include_router(alerts.router, prefix="/api")
    client = TestClient(app)
    headers = {"Authorization": "Bearer synthetic-token"}
    for cpu in [25, 60, 96]:
        assert (
            client.post(
                "/api/heartbeat",
                headers=headers,
                json={"host": "MATLAB-PC", "cpu_percent": cpu, "memory_percent": 90},
            ).status_code
            == 200
        )
    runtime = tmp_path / "matlab.log"
    runtime.write_text(
        "running\nERROR MATLAB:nomem password=synthetic-secret\n at worker.m:8\n", encoding="utf-8"
    )
    collector = IncrementalLogCollector([str(tmp_path / "matlab.log*")], tmp_path / "cursor.json")
    collector.collect()
    batch = collector.batch()
    assert (
        client.post(
            "/api/host-logs", headers=headers, json={"host": "MATLAB-PC", "entries": batch}
        ).status_code
        == 200
    )
    collector.acknowledge(batch)
    runtime.rename(tmp_path / "matlab.log.1")
    runtime.write_text("WARNING worker disconnected\n", encoding="utf-8")
    collector.collect()
    assert (
        client.post(
            "/api/host-logs",
            headers=headers,
            json={"host": "MATLAB-PC", "entries": collector.batch()},
        ).status_code
        == 200
    )
    age(repository, "MATLAB-PC", 4)
    run_id = monitor.scan()[0]
    assert monitor.scan() == []
    claimed = repository.get_run(run_id)
    assert "synthetic-secret" not in json.dumps(claimed)
    assert [row["payload"]["cpu_percent"] for row in claimed["input"]["history"]["metrics"]] == [
        25,
        60,
        96,
    ]
    assert "WARNING worker disconnected" in json.dumps(claimed, ensure_ascii=False)
    visited = []

    async def planner(state):
        visited.append("planner")
        return {**state, "plan": ["historical evidence"]}

    async def executor(state):
        visited.append("executor")
        context = get_run_context()
        tools, mcp = await available_tools([], AsyncMock(side_effect=AssertionError("offline")))
        assert not mcp
        evidence = await tools[0].ainvoke({})
        context.repository.merge_checkpoint(
            run_id, "executor", {"past_steps": [["history", evidence]], "plan": []}
        )
        return {**state, "plan": [], "response": ""}

    async def replanner(state):
        visited.append("replanner")
        return {**state, "response": "原因未确定；MATLAB:nomem 是历史错误，需要核查网络和采集器。"}

    graph = StateGraph(dict)
    graph.add_node("planner", planner)
    graph.add_node("executor", executor)
    graph.add_node("replanner", replanner)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "replanner")
    graph.add_edge("replanner", END)
    compiled = graph.compile()

    async def execute(task, **kwargs):
        result = await compiled.ainvoke({"input": task})
        yield {"type": "complete", "response": result["response"]}

    fake = ModuleType("app.services.aiops_service")
    fake.aiops_service = SimpleNamespace(execute=execute)
    monkeypatch.setitem(sys.modules, "app.services.aiops_service", fake)
    report_mail = AsyncMock()
    instant = []
    monkeypatch.setattr("app.services.alert_service.send_diagnosis_report_email", report_mail)
    monkeypatch.setattr(
        "app.services.mail_service.send_alert_email", lambda *args: instant.append(args)
    )
    from app.harness.notifications import dispatch_notifications

    await dispatch_notifications(repository)
    assert len(instant) == 1
    worker = HarnessWorker(repository, settings=HarnessSettings(database_url="sqlite://"))
    assert await worker.run_once()
    saved = repository.get_run(run_id)
    assert saved["status"] == "succeeded"
    assert visited == ["planner", "executor", "replanner"]
    for title in ["已观测事实", "检索证据", "分析推断", "原因未确定", "unavailable"]:
        assert title in saved["final_report"]
    report_mail.assert_awaited_once()
    await dispatch_notifications(repository)
    assert len(instant) == 1 and report_mail.await_count == 1
