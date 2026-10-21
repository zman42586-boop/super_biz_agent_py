# Prometheus MCP

更新：2026-10-21。

`monitor_server.py` 已替换为 Prometheus HTTP API 的只读适配器，端口仍为 `8004`，地址 `http://127.0.0.1:8004/mcp`。不再调用 psutil，不提供 LHM、温度或旧的 `query_cpu_metrics` / `query_memory_metrics` 工具。

唯一工具是 `query_prometheus_metrics`：

```json
{
  "host_id": "实际Windows主机名",
  "metric": "memory_percent",
  "start_time": "2026-10-21T01:00:00Z",
  "end_time": "2026-10-21T01:10:00Z",
  "step_seconds": 15
}
```

支持 `cpu_percent`、`memory_percent`、`memory_total_bytes`、`memory_available_bytes`。不提供任意 PromQL 或动态数据源 URL，默认最多两小时、240点，UTC 时间必须携带时区。CPU 通过 windows_exporter 的 idle counter 在一分钟窗口计算 rate；内存通过 available / physical_total 计算。

指标同时检查目标 host_id、exporter up 和 up 样本新鲜度；缺失、失效、非法范围不会回退到 MCP 机器采样。返回时间为 PromQL 求值时间，不宣称是每个原始采样的硬件读取时间，不补零或制造历史。单点和历史结果有不同证据类型，数据在模型压缩前进入 Evidence 注册表。

Agent 自动告警任务的 host_id 受 ToolGateway 限制。主机失联时仍禁止本机实时工具和 search_log，只允许查询独立 Prometheus 中截止最后心跳的历史；未配置云端副本时，本地电脑失联可能同时导致指标历史不可查询，此时降级为 MySQL 日志/旧快照与知识检索，不能声称已获得 Prometheus 证据。

本地事件代理直接使用 `LOCAL_PROMETHEUS_URL`；MCP 独立进程使用 `PROMETHEUS_URL`，云端部署时后者指向云端副本。启动、远程同步、安全入口与测试见 [Prometheus 部署说明](../docs/prometheus-monitoring.md)。

```powershell
.\.venv\Scripts\python.exe mcp_servers/monitor_server.py
```
