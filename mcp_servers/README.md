# MCP Servers

本目录只保留诊断取证类 MCP 服务。主动 OnCall 检测由 `scripts/lhm_alert_agent.py`
负责，日志检索已迁移为 FastAPI 本地工具 `app/tools/log_tool.py`。

## 服务列表

### Monitor Server (`monitor_server.py`)

监控数据服务，端口 `8004`，MCP 地址：

```bash
http://localhost:8004/mcp
```

核心工具：

- `query_cpu_metrics`：使用 `psutil` 查询本机真实 CPU 使用率
- `query_memory_metrics`：使用 `psutil` 查询本机真实内存使用率
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
主动检测层: scripts/lhm_alert_agent.py 或外部告警源
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
