"""Historical evidence for an offline run; never opens a connection to a host."""

import json

from langchain_core.tools import tool

from app.harness.runtime import get_run_context


@tool
def read_host_history() -> str:
    """读取当前失联诊断任务的故障前指标采样和 MATLAB 关键日志，含 evidence_id/UTC 接收时间。"""
    context = get_run_context()
    if not context or not (context.offline_host or context.host_id):
        return "历史主机证据 unavailable：当前任务没有目标主机"
    history = context.evidence or {}
    metrics = history.get("metrics", [])
    stride = max(1, len(metrics) // 60)
    logs = [
        {**row, "payload": {**row["payload"], "text": row["payload"]["text"][:256]}}
        for row in history.get("logs", [])[-20:]
    ]
    return json.dumps(
        {
            "host": context.offline_host or context.host_id,
            "metrics": metrics[::stride][-60:],
            "logs": logs,
            "total_metrics": len(metrics),
            "total_logs": len(history.get("logs", [])),
            "sampled": True,
            "MCP": "prometheus_history_only" if context.offline_host else "prometheus",
            "search_log": "unavailable" if context.offline_host else "FastAPI_service_logs_only",
        },
        ensure_ascii=False,
    )


async def available_tools(local_tools, discover):
    context = get_run_context()
    if context and context.offline_host:
        local = [t for t in local_tools if t.name != "search_log"] + [read_host_history]
        try:
            # Only the independent Prometheus API can be queried when a host is
            # offline. Never discover a per-host exporter or other live tools.
            remote = [t for t in await discover() if t.name == "query_prometheus_metrics"]
        except Exception:
            remote = []
        return local, remote
    if context and context.host_id:
        local_tools = list(local_tools) + [read_host_history]
    return local_tools, await discover()


def offline_notice():
    context = get_run_context()
    if context and context.offline_host:
        return (
            f"\n主机 {context.offline_host} 已失联：实时主机工具和 search_log unavailable。"
            "query_prometheus_metrics只能查询失联前历史，不能访问故障主机；数据源须为可用的云端副本。"
            "read_host_history 读取故障前采样，retrieve_knowledge 仅提供参考案例。"
            "不得将云端本机日志当成该主机日志；原因未确定，禁止断言物理宕机。"
        )
    return ""
