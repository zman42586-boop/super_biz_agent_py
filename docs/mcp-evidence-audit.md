# MCP 与故障证据链路核查

更新：2026-10-21。本轮已将 Monitor MCP 的本机 psutil/LHM 实现替换为 Prometheus 查询适配器，并按用户要求移除温度。原10月20日方案仅保留在历史验收记录中，不再代表当前部署方式。

## 当前存储链路

| 数据 | 采集与上报 | 保存位置 |
|---|---|---|
| CPU / 内存 | 标准 windows_exporter → 本地 Prometheus，5秒抓取 | 本地TSDB默认15天，可选remote_write到云端默认30天 |
| 心跳存活 | 事件代理独立HTTP上报host | MySQL monitored_hosts，不为无指标心跳生成空metric记录 |
| MATLAB运行日志 | MATLAB_LOG_GLOBS增量读取关键行及上下文 | MySQL host_evidence(log)，默认2小时、每主机400条、每条4096字符 |
| Crash Dump | 进程退出后扫描新修改文本，保留类型/摘要/栈/来源/mtime/hash | 原文件本机保留，摘要进入Alert和Run；未上传二进制原文件 |
| 事件时指标快照 | 退出时读取本地Prometheus，附求值时间及缺失状态 | Alert输入、Run冻结证据 |
| 工具结果 / 诊断报告 / 通知 | Harness Worker | MySQL步骤、工具调用、Evidence状态、报告、Outbox；超长结果为本地artifact |

兼容旧客户端CPU/内存心跳；既有MySQL指标历史不主动删除，继续按原TTL/数量清理。新指标主存储为Prometheus。温度字段、采集和MCP工具均已移除；未删除用户自行下载的LHM程序或已有历史数据库记录。

## 工具来源与约束

- `query_prometheus_metrics`连接部署配置的Prometheus，按显式host_id查询CPU/内存；不读取MCP主机硬件，不允许任意URL或任意PromQL。
- 查询检查Exporter up、样本新鲜度、主机、时间范围和返回点数。时间为PromQL求值时间，CPU是1分钟窗口rate；空数据不补零，当前值不复制成历史。
- 每个结果点在模型摘要前登记Evidence，包含原文、主机、查询表达式、时间和哈希。当前单点不能独立证明事故根因。
- `read_host_history`读取任务冻结的MySQL关键日志/旧指标；不发请求到故障主机。
- `search_log`仍查询FastAPI服务机器的logs/app_*.log，不是MATLAB diary，不把云端日志当成本地机器日志。
- 失联时只开放独立Prometheus的历史查询，截止最后心跳、host_id受Gateway强制约束。其他主机实时工具与search_log禁止。Prometheus不可用时降级，禁止编造趋势或断言物理宕机。

## 部署边界

本地Exporter与Prometheus默认仅绑定回环；云端副本通过remote_write接收，示例配置默认也仅监听云端回环，需要已有受信任HTTPS认证入口。项目没有真实云服务器地址和登录信息，本轮不宣称完成用户云端发布。

进程名/PID仍由事件代理检测；资源指标不再自行采样。Dump来源mtime仍不能严格证明与某PID生命周期关联。CPU/内存曲线不是MATLAB内部profiler，不能单独证明泄漏。没有加入自动重启、修改模型或远程修复。

部署和测试见[Prometheus接入](prometheus-monitoring.md)、[本轮验收](prometheus-migration-20261021.md)。
