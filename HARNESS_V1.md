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
HARNESS_TOOL_TIMEOUT_SEC=10
HARNESS_TOOL_MAX_RETRIES=2
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

## Recovery demonstration

1. Create a Run and start the worker.
2. Stop the worker after at least one `step_succeeded` event.
3. Wait longer than `HARNESS_LEASE_SEC` and restart the worker.
4. The stale Run changes to `interrupted`, is claimed again and resumes from `state_json`.
5. A successful ToolCall with the same idempotency key is reused instead of executed twice.

For side-effecting tools, the external system must also accept an idempotency key. If it
cannot, an uncertain result should be sent to manual review rather than retried blindly.
