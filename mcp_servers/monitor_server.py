"""Read-only MCP adapter to Prometheus; never samples the MCP host's hardware."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import FastMCP

from app.config import config
from app.monitoring.prometheus import PrometheusClient

mcp = FastMCP("Prometheus")


def query_metrics(host_id: str, metric: str, start_time: str | None = None,
                  end_time: str | None = None, step_seconds: int = 15) -> dict:
    try:
        return PrometheusClient.from_settings(config).query(
            host_id, metric, start_time=start_time, end_time=end_time, step_seconds=step_seconds)
    except (ValueError, RuntimeError) as exc:
        return {"status": "error", "error": str(exc), "data_source": "prometheus", "host_id": host_id}


@mcp.tool()
def query_prometheus_metrics(host_id: str, metric: str, start_time: str | None = None,
                             end_time: str | None = None, step_seconds: int = 15) -> dict:
    """查询指定 host_id 的 Prometheus 指标，包含真实历史；不连接被监控主机。

    metric: cpu_percent / memory_percent / memory_total_bytes / memory_available_bytes。
    start_time/end_time 使用带时区 ISO-8601；不传 start_time 查询单点。
    最多两小时、240点。CPU使用一分钟rate，空数据表示缺失，不能当成0或证明宕机。
    """
    return query_metrics(host_id, metric, start_time, end_time, step_seconds)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8004, path="/mcp")
