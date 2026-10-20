# Agent Harness：持久化诊断执行

更新：2026-10-20。保留现有 MySQL Harness，新增 Evidence Verifier 的 phase、计数与截止时间持久化；没有迁移到 Redis，也没有替换 LangGraph MemorySaver。

## 职责

- agent_runs：输入、最新 state_json/plan_json、报告、租约、Fencing version、错误和时间。
- agent_steps：计划步骤、执行状态、输出与原始 Evidence；支持完成后图快照未落库的恢复窗口。
- tool_calls：参数、逻辑调用、有限尝试、结果、artifact、原始 evidence 和幂等键。
- agent_run_events：checkpoint、步骤、调用、验证和完成事件，可重放 SSE。
- host_evidence/monitored_hosts：有界历史和心跳状态；Run 冻结本次已有历史。
- harness_notifications：报告完成与通知原子入队；SMTP 交付不确定时不盲目重发。

## 执行与恢复

Planner → Executor → Microcompact → Replanner → Evidence Verifier → Final Report。验证失败可受限回 Replanner；根因未确定也可以正常完成 Run，不能把 succeeded 当成诊断正确。

关键状态说明见 [Evidence Verifier 协议](docs/evidence-verification.md)。恢复优先使用 phase，不再仅凭非空 response 或 plan 跳过验证。验证调用前先预留次数与指纹，进程中断不能刷新重试机会。旧快照无 phase 时继续使用兼容入口，但旧自由文本报告不会自动当作已验证结论。

MySQL 认领增加 version，续租不增加。所有关键写入在事务内校验 run_id/worker_id/version/有效租约；旧所有者被拒绝。MemorySaver 不提供跨进程恢复，跨进程依据 state_json 和持久化 Step/ToolCall。

## 累计预算

默认8步、20次逻辑工具调用（包含最多一次 Planner 知识预取）、工具10秒和有限重试、300秒 Run 截止时间、两次无进展、两次验证修正。工具内部 attempts 仍单独记录，不等同新逻辑调用。即时会话也保留工具调用计数。

第一认领时按数据库 UTC 时间保存 deadline_at；自动接管和人工 resume 不重新获得300秒。过期 Run 仍按 RUN_TIMEOUT 失败，保留已有证据与错误事件。Step/Tool/验证预算导致的非支持候选则输出安全报告，不无限循环。

有效图递归限制至少 `3*max_steps + 2*(1+max_corrections) + 4`；默认34。原配置若更大则沿用更大的值，不能把新增节点意外变成正常路径的递归错误。

## 启动与 API

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 9900
.\.venv\Scripts\python.exe scripts\harness_worker.py
```

Run 创建、查询、SSE、取消、恢复入口仍为 `/api/runs`、`/api/runs/{run_id}`、`/steps`、`/events`、`/cancel`、`/resume`。真实 critical 告警创建 alert_diagnosis，持续心跳失联创建 host_down_diagnosis。普通告警内存去重仍不是跨 API 实例的完整告警数据库。

## 数据库与配置

本轮新增字段都保存在 JSON，不新增 SQL 表/列。已部署的主机监控表及 Fencing 迁移要求仍见 [durable-oncall](docs/durable-oncall.md)。不得混跑不兼容旧 Worker；不要在本轮主动删除已有表、列或 Milvus 集合。

完整配置见 `.env.example`，新增 HARNESS_VERIFICATION_MAX_CORRECTIONS、HARNESS_VERIFICATION_TIMEOUT_SEC、HARNESS_EVIDENCE_HISTORY_SEC、HARNESS_EVIDENCE_FUTURE_SEC。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest --no-cov
.\scripts\test_mysql_disposable.ps1
```

第二条命令在 Docker MySQL 内创建唯一临时库和用户，仅授予该库权限，执行 Worker 强制终止和 Verifier 接管测试，最终只清理生成的临时库和用户。不要将 HARNESS_TEST_MYSQL_URL 指向业务库。真实执行结果见 [验收报告](docs/aiops-improvement-20261020.md)。
