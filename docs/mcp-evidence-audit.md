# MCP 与故障证据链路核查

更新：2026-10-20。本轮保留 Monitor MCP，没有删除服务或替换整体接入架构。

## 当前存储链路

| 数据 | 采集和上报 | 保存位置与限制 |
|---|---|---|
| CPU/内存/Top 进程/可选温度 | 采集器轻量心跳 | MySQL monitored_hosts、host_evidence；默认两小时、每主机 1600 指标 |
| MATLAB 运行日志 | 配置 MATLAB_LOG_GLOBS，增量读取关键行及上下文后 POST /api/host-logs | MySQL host_evidence；默认每主机 400 条、每条4096字符；不是完整日志备份 |
| Crash Dump | 进程退出后扫描新修改的文本 Dump，解析类型、前3000字符与栈摘要 | 原文件仍在采集机；摘要及来源/修改时间/内容哈希/栈进入 Alert，再进入 Run 输入；未上传二进制 Dump 原文件 |
| 本地紧急快照/日志游标 | 采集器写入 memory/emergency | 本地文件，不是云端完整历史 |
| Agent 工具结果 | Gateway/Step/ToolCall | MySQL 有界结果、Evidence State、memory/artifacts 的超长结果 |
| 诊断状态/报告/通知 | Harness Worker | MySQL agent_runs、步骤、事件、通知；MemorySaver 仅为进程内图缓存 |

两小时历史是 TTL 与数量限制共同作用的上限，不能承诺任意查询都拥有完整两小时记录。失联与进程退出 Run 冻结已有有界历史，随后缓存清理不删除任务快照。主机指标采样不是 MATLAB 内部 profiler，也不能证明泄漏。

## 工具来源

`search_log` 查询 FastAPI 所在机器的 `logs/app_*.log`，包含文件、行号、log_id 和日志时间；它不是 MATLAB diary 工具。当前增加真实采样主机字段，避免被误当作目标主机的运行日志。

Monitor 的 psutil 查询来自 MCP Server 所在主机。`service_name` 是兼容标签，没有按该参数选择远程主机。此前实现把当前值复制到过去时间窗口并标记 real；本轮只修正这处证据语义：返回一个当前采样、host_id、observed_at、data_source=current_snapshot、historical_available=false。过去的 start/end 参数保留为 requested_range，不能用来推断历史趋势。

LHM 默认连接本机 Web Server。若显式传远程 LHM URL，其传感器实际主机仍需额外认证与映射；当前工具不能替代跨主机监控平台的身份系统。这类数据不能单独证明 MATLAB 本次根因。

MATLAB 已崩溃但主机还在线时，实时资源采样只能描述崩溃后的状态；诊断应优先使用故障前历史和 Dump。主机失联时已有 available_tools/ToolGateway 明确禁用实时 MCP 和 search_log，仅保留冻结历史、RAG 和时间工具，不能连接失联主机强制修复。

## 已修正的取证缺陷

- Dump 类型匹配原先将文本转大写、关键词仍小写，导致 Stack Overflow 等匹配失败；现已统一大小写。
- 采集器启动时建立 Dump 修改时间基线，减少把启动前旧文件关联到新退出事件。仍没有 OS 层 PID/进程生命周期与 Dump 的严格关联，需后续接入进程事件/文件元数据。
- 提取栈不再只藏在终端输出；来源、修改时间、文本内容哈希和栈随告警传递。哈希针对已解码文本，不是二进制 Dump 的原始字节校验和。
- 进程退出后的系统快照没有改称“退出前快照”；持续趋势必须来自历史心跳。
- 检索 query 与模型摘要不登记为事实；只登记明确适配的现场和知识来源。

## 后续架构建议（P1，未实施）

同服务的 host_evidence 数据读取继续走 Python 服务层，不再套一层 MCP HTTP。Agent 的动态查询能力可保留为 LangGraph Tools；跨系统独立监控平台再考虑 MCP。现有 Monitor MCP 便于当前演示与独立服务接入，先保留，后续在测量调用成本、部署拓扑和身份能力之后决定是否收敛。

本轮没有接入 Prometheus、集中日志平台、完整 Dump 对象存储，也没有增加自动修改 MATLAB/Simulink、进程重启或主机失联远程操作。
