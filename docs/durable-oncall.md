# 持久化主机失联诊断与 Worker 恢复

在已有 FastAPI + MySQL + Harness Worker + LangGraph + RAG + MCP 上扩展。进程退出仍走 `alert_diagnosis`；主机持续失联新增 `host_down_diagnosis`，pending Run 由 Worker 认领。没有新增日志平台。

## 数据库迁移

要求 MySQL 8（SKIP LOCKED）。备份数据库，停止 API 和**全部**旧 Worker，然后运行：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_harness_v2.py
```

脚本读取 `.env`/环境变量的 `HARNESS_DATABASE_URL` 或 `HARNESS_MYSQL_*`，可重复执行，保留已有 Run、Checkpoint 和报告。MySQL DDL 可能隐式提交，失败后检查结构再重试。

| 变更 | 作用 |
|---|---|
| 原有 `agent_runs.version INTEGER`（无新增列） | 每次成功认领加一，续租及普通写入不改变 Token |
| `monitored_hosts` | host 主键、最后心跳、快照、状态、失联轮次、Run ID、告警 JSON |
| `host_evidence` | host、metric/log、UTC 接收时间、JSON；host/kind/time 索引；host/kind/event_key 唯一键 |
| `harness_notifications` | 唯一通知键、Run、payload、状态、交付 Token、认领时间、错误 |

迁移仅创建缺失的新表，不修改 Run 数据、不重置 version、不新增 lease_epoch。旧 version 修订号直接作为高水位，下一次认领加一。不可混跑新旧 Worker；即使旧代码也使用 version，其续租递增语义仍不兼容。停止全部旧 Worker 后等待其现存租约过期，再启动新版，周期扫描中断任务并以新 version 恢复。确认没有存活的旧进程后才切换代码。

若先前已部署 lease_epoch，脚本和 ORM 均保留该列及数据，但新版不读取或修改它。先备份、停全部 API/Worker、检查其他服务/报表/触发器对该列的依赖，再迁移并等待旧租约过期。不要在线强行删列；运行稳定并确认无依赖后，可在另一个维护窗口单独评估 DROP COLUMN，本次没有执行删除。回滚也必须停全部进程，恢复备份/兼容代码并重新认领，不能让跨版本旧进程继续运行。MySQL DDL 非完整事务，失败后检查表结构再重试。

## Worker 与副作用

独立维护协程默认每 5 秒扫描 running 且租约过期的任务，忙 Worker 也会扫描。行锁和条件更新清除旧所有权，记录 run_interrupted；正常 Worker 可认领 interrupted，记录 run_resumed。认领、续租、租约判断使用数据库 UTC 时钟。

`version` 只表示认领代次。认领 pending/interrupted 使用行锁及条件更新原子递增并返回；Checkpoint、Step、ToolCall、执行事件、完成和失败在事务内通过条件 no-op UPDATE 锁父 Run，校验 run_id、worker_id、version、running、有效租约，再修改子记录。SQLAlchemy MySQL 驱动启用 FOUND_ROWS，no-op UPDATE 的 rowcount 表示匹配行数。等待锁后再次以新数据库时刻检查过期时间。Step 和调用幂等键也校验所属 Run。续租同样锁定/检查，但不递增 version。回收、取消、管理端恢复也不递增，重新认领才递增。默认租约 30 秒，每租约时长的三分之一续租（默认约 10 秒），HARNESS_LEASE_SEC 可配置。过期只表示执行权失效，不能证明 Worker 崩溃；过期 Worker 不能续租复活，续租失败会取消执行。

直接调用关键写入需要 worker_id/version 或有效 HarnessRunContext；cancel/resume 是独立管理操作。检查所有旧依赖：原修订号递增仅在 Repository，Worker 的 LangGraph execution_id 改为 run_id:version；SSE 使用独立事件 ID，质量指标不依赖修订号。没有将 version 用作普通数据变更通知。

成功报告和结果通知共用提交事务，邮件失败不覆盖报告。唯一通知键与原子认领保证自动发送至多尝试一次；SMTP 异常或发送进程消失进入 uncertain，超过 120 秒的 sending 由扫描标记 uncertain，不自动重发。需查询状态/last_error，核实 SMTP 后人工处理。

此策略可能丢失通知，不能承诺 exactly-once 邮件。Token 只保护 MySQL 写入，无法撤销已发出的工具请求、SMTP DATA、文件写入或 Milvus 入库；外部服务需自己的幂等键/Token。Incident 和工具结果文件按认领 version 隔离。回收时对仍 running 的 ToolCall 记录 external_tool_execution_uncertain，包含旧 Worker、version、调用 ID 及潜在重复执行标记；执行后发现所有权丢失不提交结果。标记 read_only=false 的工具不自动重试，也拒绝自动重放未确认/失败的调用，记录 external_tool_replay_blocked；需人工核实或外部服务幂等控制。现有诊断工具默认只读，新增有副作用的工具必须显式配置该策略，不能承诺恢复时远端只执行一次。

## 主机监测与历史证据

```text
独立轻量心跳 -> monitored_hosts + host_evidence(metric)
独立增量日志 -> POST /api/host-logs -> host_evidence(log)
API 启动即扫描数据库中的注册主机
  30 秒无心跳：suspected_down；60 秒无心跳：down
  同一事务：失联轮次 + 即时通知 + pending 诊断 Run
Worker -> Planner -> Executor -> Replanner
  read_host_history + RAG；实时 MCP / search_log unavailable
  事实 / 检索证据 / 推断 -> 原子完成 + 诊断通知
恢复心跳 -> online，告警 resolved
```

`ONCALL_AUTO_DIAGNOSIS=false` 时只创建即时通知。主机行锁确保多实例重复扫描同轮失联只产生一次任务，重启后无需先收到新心跳。原有普通进程告警的内存去重没有全面改造为跨 API 实例告警数据库。

心跳间隔、服务器扫描间隔、失联阈值独立配置：默认 HEARTBEAT_INTERVAL_SEC=5、ONCALL_HEARTBEAT_CHECK_INTERVAL_SEC=10、ONCALL_HEARTBEAT_TIMEOUT_SEC=60。疑似阈值 ONCALL_HEARTBEAT_SUSPECTED_SEC=30。POLL_INTERVAL_SEC=5 仍控制本地进程轮询。网络请求超时仍可能延迟一次心跳。Milvus 不可用时 API 可降级启动；MySQL 不可用时不能承诺持久化接收。

需要自行配置 MATLAB diary/运行日志到文件，再设置 MATLAB_LOG_GLOBS，例如 `C:\matlab-logs\runtime.log*`，多个 glob 用分号分隔，包含轮转文件。Crash Dump 不加入 glob，沿用原退出事件解析/上报。

增量读取使用持久化字节游标，识别文件身份变化、轮转、截断，提取 ERROR/WARNING、错误码、异常和栈行，保留前 3 行及后 5 行上下文。游标和脱敏队列存于 memory/emergency/matlab_log_cursor.json。失败保留稳定 event_id，重启可重试；云端唯一键避免重传重复，上传确认后移除。默认每 2 秒上传，每轮最多读 64 KiB/8 文件，重试队列最多 128 条/256 KiB，参数见 .env.example。极长未结束行可能跳过，队列溢出丢最旧内容，dropped 记录队列丢弃数；它不是完整日志备份。

普通心跳不传完整日志。云端默认保留接收 UTC 时间最近 7200 秒，每主机独立最多 1600 指标/400 日志，每日志 4096 字符，每批内容 64 KiB/100 条；ONCALL_HISTORY_* 可配置。写入和周期扫描清理，离线主机也清理，查询过滤 TTL。采集器 ts 仅为辅助时间。Run 冻结有界证据，任务审计快照和通知不随缓存 TTL 删除，长期使用需要 Run/通知归档策略。工具和 Prompt 会对历史采样，完整有界历史存于任务输入。

脱敏覆盖常见密钥、Bearer、SMTP 密码、邮箱、用户路径，无法识别所有敏感业务内容。只上传获准日志，使用受信任网络/TLS；共享 Webhook Token 不提供每主机独立身份认证。

Planner/Executor/Replanner 跳过 MCP 发现；Tool Gateway 只允许历史、知识检索和时间工具。云端 search_log 不作为该主机证据。报告分服务端事实、实际检索记录和未验证的模型分析，心跳无法确认物理状态，保守输出“原因未确定”。模型断言过滤只是辅助检查，不能替代内容评估。

## 验收与限制

```powershell
.\.venv\Scripts\python.exe -m pytest --no-cov tests/test_fenced_recovery.py tests/test_host_monitoring.py tests/test_log_retention_notifications.py tests/test_production_offline_graph.py
```

覆盖并发、旧 Token 拒绝、忙 Worker 扫描、失去租约停止、短暂丢包/失联/重启/恢复、TTL/容量隔离、日志轮转/截断/失败重试、SMTP 不确定状态、迁移幂等。HTTP 测试执行真实 API、数据库、Worker 与 LangGraph；另一个测试执行实际生产 Planner/Executor/Replanner 节点，但替换模型、RAG 返回值、记忆写入和 SMTP。通过不等于真实 MATLAB、模型质量或邮件交付已验收。

MySQL 强制终止测试需要**空的专用测试库**，安全注入 HARNESS_TEST_MYSQL_URL，不要指向业务库或直接在终端历史输入密码：

```powershell
.\.venv\Scripts\python.exe -m pytest --no-cov tests/test_mysql_worker_recovery.py
```

未提供变量明确 skip。测试启动 A 子进程并 terminate，B/C 保持运行，验证超时回收、Checkpoint 恢复、旧 Token 拒绝。SQLite 并发结果不能冒充 MySQL 行锁/隔离级别实测。还需人工运行真实 MATLAB 日志、停止心跳，检查真实 MySQL Run、Worker 事件、模型报告和邮件。

本次本地验证（2026-10-11，Python 3.11）：除 test_document_splitter_v2.py 和 test_adaptive_retrieval.py 外的回归为 99 passed、1 skipped（MySQL）。全量命令因缺少 transformers 在收集阶段失败；单独执行自适应检索得到 6 failed，均因缺少 google.protobuf，未完成检索逻辑验证。生产图测试替换模型、RAG、记忆写入和 SMTP；HTTP/日志轮转测试采用 SQLite 与确定性图节点。未运行真实 MySQL、MATLAB、Milvus、模型 API 或 SMTP 验收。不要把这些本地结果视为生产依赖已通过。
