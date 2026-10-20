# MCP Servers

更新：2026-10-20。

本目录只保留诊断取证类 MCP 服务。主动 OnCall 检测由 `scripts/lhm_alert_agent.py`
负责，温度/进程监控在同一主机运行；日志检索为 FastAPI 本地工具 `app/tools/log_tool.py`。

## 服务列表

### Monitor Server (`monitor_server.py`)

监控数据服务，端口 `8004`，MCP 地址：

```bash
http://localhost:8004/mcp
```

核心工具：

- `query_cpu_metrics`：查询MCP Server所在主机的当前CPU，只有一个真实采样点，不生成历史趋势
- `query_memory_metrics`：查询MCP Server所在主机当前内存，不以当前值回填过去时间窗口
- `list_lhm_sensors`：读取 LibreHardwareMonitor 温度传感器列表
- `get_lhm_temperature`：查询匹配传感器的当前温度

## 启动

推荐使用项目根目录的 Windows 脚本：

```bat
start-windows.bat
```

手动启动 Monitor MCP：

```bash
python mcp_servers/monitor_server.py
```

## AIOps 诊断分工

```text
主动检测层: scripts/lhm_alert_agent.py (温度 + 进程监控)
告警入口:   POST /api/alerts/ingest
诊断取证:   Monitor MCP + 本地工具
通知输出:   SMTP 邮件
```

Agent 诊断时可调用：

```python
query_cpu_metrics(service_name="local-host")
query_memory_metrics(service_name="local-host")
get_lhm_temperature()
```

本地日志由 `search_log` 工具提供，不再通过 CLS MCP：

```python
search_log(query="error timeout", minutes=60, limit=100)
```

## 参考资料

- [FastMCP 文档](https://github.com/jlowin/fastmcp)
- [MCP 协议](https://modelcontextprotocol.io/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [主项目 README](../README.md)

---

注意：MCP 工具是被 Agent 调用的诊断取证接口；主动 OnCall 检测不由 MCP
承担，而由 `lhm_alert_agent.py` 或外部告警系统触发。

## 证据来源约束

CPU/内存返回host_id、observed_at、data_source=current_snapshot、historical_available=false。service_name只是兼容标签，不能选择目标主机；start/end仅返回在requested_range，不提供历史数据。故障前历史使用read_host_history读取MySQL中冻结的主机证据。

LHM使用远程URL时不猜测实际host_id，不能将采集服务主机误当成传感器主机。主机失联时实时MCP和search_log全部禁用；search_log查询FastAPI日志，不是MATLAB运行日志。缺少主机、事件时间或有效来源的数据不能单独支持根因。

本轮保留MCP服务；同服务数据库读取、跨系统接入和后续取舍见 [数据链路核查](../docs/mcp-evidence-audit.md)。
