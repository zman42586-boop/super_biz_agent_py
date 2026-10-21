"""Contract tests for host/time scope, true history, gaps, and local exporters."""

import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.agent.aiops.evidence import tool_evidence
from app.config import Settings
from app.harness.monitoring import HostMonitoringRepository
from app.harness.repository import HarnessRepository
from app.harness.runtime import HarnessRunContext, harness_run_context
from app.harness.tool_gateway import ToolExecutionError, ToolGateway, _prepared_result
from app.monitoring.prometheus import PrometheusClient, expression
from app.tools.host_history import available_tools
from scripts.configure_monitoring import local_config

NOW = datetime(2026, 10, 21, 8, tzinfo=UTC)


def api_client(result=None, *, host="pc", timestamp=None, vector=False, code=200, body=None):
    captured = []
    if body is None:
        pairs = result if result is not None else [[(NOW - timedelta(seconds=15)).timestamp(), "40"], [NOW.timestamp(), "55"]]
        series = {"metric": {"host_id": host}, "value" if vector else "values":
                  [timestamp or NOW.timestamp(), "55"] if vector else pairs}
        body = {"status": "success", "data": {"resultType": "vector" if vector else "matrix", "result": [series]}}

    def handler(request):
        captured.append(request)
        return httpx.Response(code, json=body)

    return PrometheusClient("http://prom.test", transport=httpx.MockTransport(handler)), captured


def query_range(client):
    return client.query("pc", "memory_percent", start_time=(NOW - timedelta(minutes=1)).isoformat(), end_time=NOW.isoformat(), now=NOW)


def test_range_keeps_real_values_host_and_promql_evaluation_times():
    client, requests = api_client()
    row = query_range(client)
    assert [p["value"] for p in row["data_points"]] == [40, 55]
    assert row["timestamp_basis"] == "promql_evaluation" and row["historical_available"]
    assert requests[0].url.path == "/api/v1/query_range"
    query = requests[0].url.params["query"]
    assert 'host_id="pc"' in query and "timestamp(up" in query
    assert "windows_memory_physical_total_bytes" in query


@pytest.mark.parametrize("metric", ["cpu_percent", "memory_percent", "memory_total_bytes", "memory_available_bytes"])
def test_each_metric_uses_host_scoped_health_guard(metric):
    query = expression("pc", metric)
    assert 'host_id="pc"' in query and "count by (host_id)" in query
    if metric == "cpu_percent":
        assert "rate(" in query and "[1m]" in query


def test_instant_values_and_stale_upstream_point():
    client, _ = api_client(vector=True)
    assert client.query("pc", "cpu_percent", now=NOW)["data_points"][0]["value"] == 55
    client, _ = api_client(vector=True, timestamp=(NOW - timedelta(minutes=6)).timestamp())
    row = client.query("pc", "cpu_percent", now=NOW)
    assert row["status"] == "unavailable" and row["data_points"] == []


def test_missing_and_nan_are_not_zero_or_an_invented_curve():
    client, _ = api_client(body={"status": "success", "data": {"resultType": "matrix", "result": []}})
    assert query_range(client)["data_points"] == []
    client, _ = api_client(result=[[NOW.timestamp(), "NaN"]])
    assert query_range(client)["status"] == "unavailable"


def test_real_prometheus_millisecond_truncation_at_query_start_is_accepted():
    start = (NOW - timedelta(minutes=1)).replace(microsecond=123456)
    truncated = start.replace(microsecond=123000)
    client, _ = api_client(result=[[truncated.timestamp(), "40"]])
    assert client.query("pc", "memory_percent", start_time=start.isoformat(), end_time=NOW.isoformat(), now=NOW)["data_points"]
    client, _ = api_client(result=[[(start - timedelta(seconds=2)).timestamp(), "40"]])
    with pytest.raises(RuntimeError, match="outside"):
        client.query("pc", "memory_percent", start_time=start.isoformat(), end_time=NOW.isoformat(), now=NOW)


@pytest.mark.parametrize("kwargs", [
    {"start_time": "2026-10-21T08:00:00"},
    {"start_time": "2026-10-20T00:00:00Z"},
    {"start_time": "2026-10-21T08:00:00Z"},
    {"start_time": "2026-10-21T07:00:00Z", "step_seconds": 1},
    {"start_time": "2026-10-21T07:00:00Z", "step_seconds": 5},
    {"end_time": "2026-10-22T00:00:00Z"},
])
def test_invalid_unbounded_or_future_queries_never_contact_server(kwargs):
    client, requests = api_client()
    with pytest.raises(ValueError):
        client.query("pc", "cpu_percent", now=NOW, **kwargs)
    assert requests == []


def test_host_mismatch_unknown_metric_and_errors_fail_closed():
    client, _ = api_client(host="other")
    with pytest.raises(RuntimeError, match="host_id"):
        query_range(client)
    with pytest.raises(ValueError, match="Unsupported"):
        client.query("pc", "arbitrary_promql", now=NOW)
    client, _ = api_client(code=503, body={"secret": "must-not-be-returned"})
    with pytest.raises(RuntimeError, match="HTTPStatusError") as error:
        query_range(client)
    assert "secret" not in str(error.value)


def test_prometheus_results_register_exact_points_and_preserve_mcp_text_blocks():
    client, _ = api_client()
    row = query_range(client)
    blocks = [{"type": "text", "text": json.dumps(row)}]
    _, structured = _prepared_result("query_prometheus_metrics", blocks)
    assert structured["raw_result"]["data_source"] == "prometheus"
    evidence = tool_evidence("query_prometheus_metrics", blocks, {"run_id": "run", "host_id": "pc"})
    assert len(evidence) == 2 and evidence[0]["host_id"] == "pc"
    assert evidence[0]["source_type"] == "historical_metric"
    assert tool_evidence("query_prometheus_metrics", row, {"host_id": "other"}) == []


def test_temperature_is_not_an_available_metric():
    with pytest.raises(ValueError, match="Unsupported"):
        expression("pc", "temperature_c")


def test_liveness_only_heartbeat_does_not_create_fake_metric_samples(tmp_path):
    repository = HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'metrics.sqlite').as_posix()}")
    monitor = HostMonitoringRepository(repository, Settings(_env_file=None))
    monitor.record_heartbeat("pc", {})
    assert monitor.get_history("pc")["metrics"] == []
    monitor.record_heartbeat("pc", {"cpu_percent": 0})
    assert monitor.get_history("pc")["metrics"][0]["payload"]["cpu_percent"] == 0


@pytest.mark.asyncio
async def test_offline_discovery_exposes_only_prometheus_history():
    discovery = AsyncMock(return_value=[SimpleNamespace(name="query_prometheus_metrics"), SimpleNamespace(name="search_log")])
    with harness_run_context(HarnessRunContext("run", Mock(), offline_host="pc")):
        local, remote = await available_tools([SimpleNamespace(name="search_log")], discovery)
    assert [t.name for t in local] == ["read_host_history"]
    assert [t.name for t in remote] == ["query_prometheus_metrics"]


@pytest.mark.asyncio
async def test_gateway_binds_offline_queries_to_host_and_frozen_cutoff():
    tool = SimpleNamespace(name="query_prometheus_metrics", ainvoke=AsyncMock(return_value={"status": "unavailable"}))
    repo = Mock()
    repo.check_ownership.return_value = True
    with harness_run_context(HarnessRunContext("run", repo, offline_host="pc", metrics_end_time=NOW.isoformat())):
        with pytest.raises(ToolExecutionError, match="host_id"):
            await ToolGateway().execute(tool=tool, arguments={"host_id": "other", "metric": "cpu_percent"})
        with pytest.raises(ToolExecutionError, match="last heartbeat"):
            await ToolGateway().execute(tool=tool, arguments={"host_id": "pc", "metric": "cpu_percent", "end_time": (NOW + timedelta(seconds=1)).isoformat()})
        await ToolGateway().execute(tool=tool, arguments={"host_id": "pc", "metric": "cpu_percent"})
    args = tool.ainvoke.call_args.args[0]
    assert args["end_time"] == NOW.isoformat() and args["start_time"] == (NOW - timedelta(minutes=10)).isoformat()


def test_local_config_host_labels_and_explicit_secure_cloud_sync():
    config = local_config("pc")
    assert "remote_write" not in config
    assert all(job["static_configs"][0]["labels"]["host_id"] == "pc" for job in config["scrape_configs"])
    with pytest.raises(ValueError, match="HTTPS"):
        local_config("pc", remote_url="http://cloud.example/api/v1/write")
    with pytest.raises(ValueError, match="TOKEN_FILE"):
        local_config("pc", remote_url="https://cloud.example/api/v1/write")
    assert local_config("pc", remote_url="http://127.0.0.1:19090/api/v1/write")["remote_write"]


def test_event_agent_reads_prometheus_and_heartbeat_has_no_metric_dependency(monkeypatch):
    from scripts import lhm_alert_agent as agent

    monkeypatch.setattr(agent, "local_metric", lambda metric: {"data_points": [{"timestamp": NOW.isoformat(), "value": 64}]})
    monkeypatch.setattr(agent.psutil, "cpu_percent", Mock(side_effect=AssertionError("Direct sampling forbidden")))
    snapshot = agent.collect_system_snapshot()
    assert snapshot["data_source"] == "prometheus" and snapshot["memory_percent"] == 64
    monkeypatch.setattr(agent, "collect_system_snapshot", Mock(side_effect=AssertionError("Heartbeat must stay independent")))
    stopping = threading.Event()
    sent = []

    def send(*args):
        sent.append(args)
        stopping.set()

    monkeypatch.setattr(agent, "send_heartbeat", send)
    agent.heartbeat_loop(stopping)
    assert sent == [()]
