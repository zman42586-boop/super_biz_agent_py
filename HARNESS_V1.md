# Agent Harness v1

Harness v1 turns the existing LangGraph diagnosis flow into a durable job system. MySQL is
the source of truth; Redis is intentionally not required in this phase.

## Responsibilities

- `agent_runs`: job state, latest checkpoint, lease and final report.
- `agent_steps`: one durable row per planned/executed step.
- `tool_calls`: validated tool arguments, attempts, latency, normalized errors and an
  idempotency key.
- `agent_run_events`: replayable SSE events. Clients reconnect with `Last-Event-ID`.
- `HarnessWorker`: claims jobs with a database row lock, renews a lease and resumes stale jobs.
- `ToolGateway`: validates arguments, applies timeouts, retries only transient failures,
  deduplicates successful calls and stores oversized output as an artifact.

## Start locally

The Docker Compose file now includes MySQL with development-only defaults:

```powershell
docker compose -f vector-database.yml up -d mysql
uv sync
```

Production credentials must be supplied through environment variables:

```dotenv
HARNESS_MYSQL_HOST=127.0.0.1
HARNESS_MYSQL_PORT=3306
HARNESS_MYSQL_DATABASE=superbiz_agent
HARNESS_MYSQL_USER=superbiz
HARNESS_MYSQL_PASSWORD=replace_me
HARNESS_AUTO_CREATE_SCHEMA=true
HARNESS_LEASE_SEC=30
HARNESS_POLL_INTERVAL_SEC=1
HARNESS_RUN_TIMEOUT_SEC=300
HARNESS_TOOL_TIMEOUT_SEC=10
HARNESS_TOOL_MAX_RETRIES=2
HARNESS_MAX_STEPS=8
HARNESS_MAX_TOOL_CALLS=20
HARNESS_MAX_REPEATED_STEPS=2
HARNESS_MAX_REPEATED_TOOL_CALLS=2
HARNESS_MAX_NO_PROGRESS_STEPS=2
HARNESS_GRAPH_RECURSION_LIMIT=32
```

`HARNESS_DATABASE_URL` can override the individual MySQL settings. Tests use an isolated
SQLite database, but the runtime default is `mysql+pymysql`.

Start API and worker in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 9900
.\.venv\Scripts\python.exe scripts\harness_worker.py
```

## API

Create a durable run:

```http
POST /api/runs
Content-Type: application/json

{"task":"诊断 MATLAB 最近一次异常退出","session_id":"demo-1"}
```

Inspect and control it:

```text
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/steps
GET  /api/runs/{run_id}/events
POST /api/runs/{run_id}/cancel
POST /api/runs/{run_id}/resume
```

## Automatic alert diagnosis

`POST /api/alerts/ingest` keeps the existing alert deduplication behavior. When a new or
previously unqueued critical alert arrives and `ONCALL_AUTO_DIAGNOSIS=true`, the API creates
an `alert_diagnosis` Run instead of starting an in-process `asyncio` diagnosis task. The
response contains `diagnosis_run_id`, which can be inspected through the Run APIs above.

The complete `AlertRecord`, including evidence, is stored in the Run input. A Harness Worker
reconstructs that alert, resumes the LangGraph state from `state_json`, and sends the diagnosis
email before marking the Run successful. If execution or email delivery fails, the Run is
marked failed and can be resumed explicitly.

## LoopGuard v1

LoopGuard provides deterministic limits outside the LLM prompt:

- The Worker fails a Run with `RUN_TIMEOUT` when its total execution budget expires.
- Planner output and Replanner execution are capped by `HARNESS_MAX_STEPS`.
- Repeated steps and consecutive no-progress results stop execution and force Replanner to
  produce a report from the evidence already collected.
- Tool Gateway blocks excess calls and repeated `tool_name + arguments` fingerprints.
- LangGraph has an explicit recursion limit as the final graph-level circuit breaker.

Every trigger appends a `loop_guard_triggered` event. Step triggers are also stored in the
Run checkpoint under `state_json.loop_guard`, so recovery keeps the latest guard decision.

## Recovery demonstration

1. Create a Run and start the worker.
2. Stop the worker after at least one `step_succeeded` event.
3. Wait longer than `HARNESS_LEASE_SEC` and restart the worker.
4. The stale Run changes to `interrupted`, is claimed again and resumes from `state_json`.
5. A successful ToolCall with the same idempotency key is reused instead of executed twice.

For side-effecting tools, the external system must also accept an idempotency key. If it
cannot, an uncertain result should be sent to manual review rather than retried blindly.
