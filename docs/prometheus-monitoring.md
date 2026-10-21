# Prometheus 指标采集与诊断接入

更新：2026-10-21。替换自定义 CPU/内存采样及 MCP 本机采样；按用户要求移除全部温度采集、LHM 工具、温度阈值及启动依赖。不删除用户目录中的 LHM 程序或既有数据库历史。

## 数据分工

```text
Windows windows_exporter :9182 /metrics
    → 本地 Prometheus :9090（5秒抓取，默认保留15天）
        → 本地演示：Prometheus MCP :8004 → Agent
        → 可选 remote_write → 云端 Prometheus（默认保留30天）
                                  ↑ 云端 MCP → Agent

Host Event Agent（沿用 scripts/lhm_alert_agent.py 文件名）
    → 独立轻量心跳 → MySQL monitored_hosts
    → MATLAB 关键日志 → MySQL host_evidence(log)
    → 进程退出 + Dump摘要 + Prometheus事件快照 → 告警 / Harness Run
```

Exporter 读取系统计数；Prometheus 持续抓取、保存时序并执行 PromQL；MCP 只提供受限查询。事件代理保留 psutil 的 PID/进程名检测，不再使用 psutil 计算 CPU/内存、遍历高占用进程或读取温度。Prometheus 自身不采集 MATLAB diary 或二进制 Dump。

新心跳只上传 host，与 Prometheus 可用性无关，不再为每次空心跳生成 MySQL metric 记录。旧客户端的 CPU/内存心跳暂时兼容；既有有界历史不主动删除。事件触发时可以读取本地 Prometheus 的最新计算结果作为快照，附求值时间，缺失时明确 unavailable，不回退采样。

## 本地安装与启动

要求 Windows x64、项目 Python 环境；监控二进制本身无需 Docker。已有 MySQL/Milvus 仍使用 Docker。便携文件、TSDB、日志、PID及本机配置均写入已忽略的 `monitoring/runtime/`。

固定版本与官方发布 SHA256 见 `monitoring/assets.json`：Prometheus 3.15.0、windows_exporter 0.31.9。安装脚本下载后检查哈希，不添加系统服务、防火墙规则或开机启动，不修改 `.env`。

```powershell
uv sync
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install-monitoring.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-monitoring.ps1
.\.venv\Scripts\python.exe mcp_servers/monitor_server.py
```

最后一条命令独立运行 MCP。完整项目也可以使用 `start-windows.bat`，停止使用 `stop-windows.bat`。只停止监控使用 `scripts/stop-monitoring.ps1`；不会删除时序数据。启动时生成匹配 Windows 主机名的 host_id，并先调用 promtool 检查配置。

所有本地指标服务默认仅绑定回环地址。Prometheus Targets 页面 `http://127.0.0.1:9090/targets` 应显示 windows 任务 up。CPU rate 至少需要两次成功抓取；不能在启动时立刻把空结果解释成0%。主机名称须与事件代理上报名称一致，多台电脑必须使用唯一 host_id。当前可查询总 CPU 使用率、物理内存使用率、内存总字节、内存可用字节；默认不采集高基数进程指标。

## MCP 查询与证据

唯一工具 `query_prometheus_metrics(host_id, metric, start_time?, end_time?, step_seconds=15)`。metric 为 `cpu_percent`、`memory_percent`、`memory_total_bytes`、`memory_available_bytes`。默认不传 start_time 为单点查询；历史最多7200秒、240点，较长窗口需要增大 step。例如两小时用 step_seconds=60。

查询端固定表达式，不能传任意 PromQL、Exporter 地址或其他主机 URL。host_id 标签被转义并显式匹配；检查 up、新鲜度和唯一抓取目标。所有指标返回实际 Prometheus 数据；失败/缺失不制造趋势、不补零。时间是 PromQL 求值时间，CPU是1分钟rate的窗口结果，不等同硬件原始采样时间；5秒抓取仍可能遗漏短尖峰。

Gateway 对自动告警任务强制 host_id 与本次主机一致。失联任务只允许截止冻结最后心跳的历史查询，未指定起点时默认前10分钟；无截止时间则拒绝。现有租约、预算、幂等、超时、Evidence和Verifier照常执行。每个返回点带查询、主机、时间、指标值登记为证据，单个当前点不能单独证明根因。

## 云端同步

仅本地保存时，本地电脑关闭后云端无法读取这份 TSDB。需要云端持续诊断时，启用 remote_write，主动把指标同步到云端，无需开放本地入站端口。

1. 在云服务器运行 `docker compose -f monitoring/cloud/compose.yml up -d`。它提供单节点 Prometheus receiver，监听云端回环9090并持久化独立命名卷，不抓取云服务器来冒充本地电脑。
2. 在既有受信任入口配置 HTTPS、请求认证和 `/api/v1/write` 反向代理，目标为云服务器 `http://127.0.0.1:9090/api/v1/write`。接收器的 loopback 端口不能直接作为公网地址使用；项目没有云域名、证书或服务器登录信息，因此不自动发布公网入口。
3. 在本地配置以下环境变量，凭据文件自行存放在 `monitoring/runtime/` 或其他私有路径；模板不包含真实凭据。

```dotenv
PROMETHEUS_REMOTE_WRITE_URL=https://monitor.example.com/api/v1/write
PROMETHEUS_REMOTE_WRITE_TOKEN_FILE=C:/private/prometheus-write-token.txt
```

4. 重启本地监控以重新生成配置。在云端 MCP 所在环境配置 `PROMETHEUS_URL=http://127.0.0.1:9090`；跨受保护入口查询时可设置 `PROMETHEUS_BEARER_TOKEN`。本地事件代理保持 `LOCAL_PROMETHEUS_URL=http://127.0.0.1:9090`。

非回环 remote_write 必须使用 HTTPS 和凭据文件；脚本不自动发送到任何未配置云地址。remote_write 有队列/WAL重试，不保证断电前最后一笔已到云端；长时间断网可能超过可重试窗口，不能承诺无限缓冲。单节点云 Prometheus 也不是高可用系统。

告警入口、心跳失联检测与通知仍由项目现有服务负责，本轮不引入 Alertmanager。CPU/内存是诊断指标，不会因为部署 Prometheus 自动新增高 CPU/内存邮件规则。

## 验证

```powershell
.\.venv\Scripts\python.exe scripts/check_monitoring.py
# 在受控环境验证 remote_write 后，可以比较本地与副本同一时间窗口。
.\.venv\Scripts\python.exe scripts/check_monitoring.py --replica-url http://127.0.0.1:19090
```

脚本检查真实CPU/内存历史、MCP工具列表、实际MCP调用及Agent Gateway证据登记；副本可选。同机19090仅用于验收模拟云端接收器。只比较启用同步后共有的数据窗口，remote_write不回填同步启用前的本地历史；CPU rate还需双方都有完整一分钟原始样本。冷启动验收应等待至少两分钟，必要时使用`--window-seconds 30`缩短比较窗口。结果见[本轮验收记录](prometheus-migration-20261021.md)。

## 参考

- [windows_exporter](https://github.com/prometheus-community/windows_exporter)
- [CPU collector](https://github.com/prometheus-community/windows_exporter/blob/master/docs/collector.cpu.md)
- [Memory collector](https://github.com/prometheus-community/windows_exporter/blob/master/docs/collector.memory.md)
- [Prometheus 查询 API](https://prometheus.io/docs/prometheus/latest/querying/api/)
- [Prometheus 存储与 remote_write](https://prometheus.io/docs/prometheus/latest/storage/)
