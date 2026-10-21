"""Read-only live smoke: local/replicated real metrics and the MCP transport."""

import argparse
import asyncio
import json
import math
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client

from app.agent.mcp_client import get_mcp_client
from app.harness.tool_gateway import ToolGateway
from app.monitoring.prometheus import PrometheusClient, utc_datetime


async def check(args):
    end = utc_datetime(args.end_time) if args.end_time else datetime.now(UTC) - timedelta(seconds=15)
    start = end - timedelta(seconds=args.window_seconds)
    params = {"start_time": start.isoformat(), "end_time": end.isoformat(), "step_seconds": 15}
    local = PrometheusClient(args.prometheus_url)
    results = {}
    for metric in ("cpu_percent", "memory_percent"):
        row = local.query(args.host_id, metric, **params)
        if not row["data_points"]:
            raise RuntimeError(f"No real {metric} samples; wait for at least two scrapes and check target health")
        results[metric] = {"points": len(row["data_points"]), "last": row["data_points"][-1:]}
        if args.replica_url:
            replicated = PrometheusClient(args.replica_url).query(args.host_id, metric, **params)
            pairs = list(zip(row["data_points"], replicated["data_points"], strict=True))
            if any(a["timestamp"] != b["timestamp"] or not math.isclose(a["value"], b["value"], rel_tol=1e-9, abs_tol=1e-9) for a, b in pairs):
                raise RuntimeError(f"Replica mismatch for {metric}; wait for remote_write delivery")
    async with Client(args.mcp_url) as client:
        tools = await client.list_tools()
        if [tool.name for tool in tools] != ["query_prometheus_metrics"]:
            raise RuntimeError("Unexpected MCP tools; old direct-sampling server may still be running")
        result = await client.call_tool("query_prometheus_metrics", {"host_id": args.host_id, "metric": "memory_percent", **params})
        data = result.data
        if not isinstance(data, dict) or data.get("data_source") != "prometheus" or not data.get("data_points"):
            raise RuntimeError("MCP did not return real Prometheus data")
    adapter = await get_mcp_client(servers={"monitor": {"transport": "streamable_http", "url": args.mcp_url}}, force_new=True)
    tools = await adapter.get_tools()
    result = await ToolGateway().execute(tool=tools[0],
        arguments={"host_id": args.host_id, "metric": "memory_percent", **params},
        evidence_context={"host_id": args.host_id, "run_id": "live-prometheus-smoke"})
    if not result.evidence or any(row["host_id"] != args.host_id for row in result.evidence):
        raise RuntimeError("Real MCP adapter results did not register host-scoped evidence")
    results["mcp"] = "passed"
    results["gateway_evidence_points"] = len(result.evidence)
    results["replica"] = "passed" if args.replica_url else "not_requested"
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8004/mcp")
    parser.add_argument("--host-id", default=socket.gethostname())
    parser.add_argument("--replica-url", default="")
    parser.add_argument("--window-seconds", type=int, default=120)
    parser.add_argument("--end-time", default="")
    asyncio.run(check(parser.parse_args()))


if __name__ == "__main__":
    main()
