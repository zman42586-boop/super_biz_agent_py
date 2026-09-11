"""Transactional repository for the durable Agent runtime harness."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.harness.config import HarnessSettings, harness_settings
from app.harness.models import AgentRun, AgentStep, Base, RunEvent, ToolCall, utc_now

TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}


def _json_safe(value: Any) -> Any:
    """Normalize tuples, Pydantic objects and message-like values for JSON columns."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _dt(value: datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds") if value else None


class HarnessRepository:
    """MySQL-backed source of truth; SQLite is supported for fast isolated tests."""

    def __init__(
        self,
        database_url: str,
        *,
        auto_create_schema: bool = True,
        engine: Engine | None = None,
    ) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = engine or create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)
        if auto_create_schema:
            Base.metadata.create_all(self.engine)

    def close(self) -> None:
        self.engine.dispose()

    def health_check(self) -> bool:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return True

    def create_run(
        self,
        *,
        task: str,
        session_id: str,
        kind: str = "diagnosis",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        initial_state = {
            "input": task,
            "plan": [],
            "past_steps": [],
            "response": "",
            "steps_summary": "",
            "loop_guard": {},
        }
        input_json = {"task": task, **(payload or {})}
        now = utc_now()
        with self._session_factory.begin() as session:
            run = AgentRun(
                id=run_id,
                session_id=session_id,
                kind=kind,
                status="pending",
                input_json=_json_safe(input_json),
                plan_json=[],
                state_json=initial_state,
                current_step_index=0,
                created_at=now,
                updated_at=now,
            )
            session.add(run)
            self._append_event(session, run_id, "run_created", {"status": "pending"})
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            run = session.get(AgentRun, run_id)
            return self._run_dict(run) if run else None

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AgentStep)
                .where(AgentStep.run_id == run_id)
                .order_by(AgentStep.step_index)
            ).all()
            return [self._step_dict(row) for row in rows]

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.id)
            ).all()
            return [self._tool_call_dict(row) for row in rows]

    def list_events(self, run_id: str, after_id: int = 0, limit: int = 200) -> list[dict]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.id > after_id)
                .order_by(RunEvent.id)
                .limit(max(1, min(limit, 1000)))
            ).all()
            return [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "type": row.event_type,
                    "payload": row.payload_json,
                    "created_at": _dt(row.created_at),
                }
                for row in rows
            ]

    def append_event(self, run_id: str, event_type: str, payload: dict) -> int:
        with self._session_factory.begin() as session:
            event = self._append_event(session, run_id, event_type, payload)
            session.flush()
            return event.id

    def recover_stale_runs(self, *, now: datetime | None = None) -> int:
        now = now or utc_now()
        with self._session_factory.begin() as session:
            stale = session.scalars(
                select(AgentRun)
                .where(
                    AgentRun.status == "running",
                    AgentRun.lease_expires_at.is_not(None),
                    AgentRun.lease_expires_at < now,
                )
                .with_for_update()
            ).all()
            for run in stale:
                run.status = "interrupted"
                run.worker_id = None
                run.lease_expires_at = None
                run.error_code = "WORKER_LEASE_EXPIRED"
                run.error_message = "Worker heartbeat expired; the run can be resumed safely."
                run.updated_at = now
                run.version += 1
                self._append_event(
                    session,
                    run.id,
                    "run_interrupted",
                    {"reason": "worker_lease_expired"},
                )
            return len(stale)

    def claim_next_run(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = utc_now()
        with self._session_factory.begin() as session:
            run = session.scalar(
                select(AgentRun)
                .where(AgentRun.status.in_(["pending", "interrupted"]))
                .order_by(AgentRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if run is None:
                return None
            previous_status = run.status
            run.status = "running"
            run.worker_id = worker_id
            run.lease_expires_at = now + timedelta(seconds=lease_seconds)
            run.started_at = run.started_at or now
            run.error_code = None
            run.error_message = None
            run.updated_at = now
            run.version += 1
            self._append_event(
                session,
                run.id,
                "run_started" if previous_status == "pending" else "run_resumed",
                {"worker_id": worker_id, "previous_status": previous_status},
            )
            session.flush()
            return self._run_dict(run)

    def renew_lease(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = utc_now()
        with self._session_factory.begin() as session:
            result = session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "running",
                    AgentRun.worker_id == worker_id,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                    version=AgentRun.version + 1,
                )
            )
            return bool(result.rowcount)

    def merge_checkpoint(self, run_id: str, node_name: str, state_update: dict) -> None:
        safe_update = _json_safe(state_update or {})
        now = utc_now()
        with self._session_factory.begin() as session:
            run = session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            state = dict(run.state_json or {})
            state.update(safe_update)
            run.state_json = state
            run.plan_json = list(state.get("plan") or [])
            run.current_step_index = len(state.get("past_steps") or [])
            run.updated_at = now
            run.version += 1
            self._append_event(
                session,
                run_id,
                "checkpoint_saved",
                {"node": node_name, "current_step_index": run.current_step_index},
            )

    def start_step(
        self,
        run_id: str,
        step_index: int,
        description: str,
        input_json: dict | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._session_factory.begin() as session:
            step = session.scalar(
                select(AgentStep)
                .where(AgentStep.run_id == run_id, AgentStep.step_index == step_index)
                .with_for_update()
            )
            if step is None:
                step = AgentStep(
                    run_id=run_id,
                    step_index=step_index,
                    step_type="agent",
                    description=description,
                    status="running",
                    input_json=_json_safe(input_json or {}),
                    attempt_count=1,
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(step)
                session.flush()
            elif step.status != "succeeded":
                step.description = description
                step.status = "running"
                step.attempt_count += 1
                step.started_at = now
                step.finished_at = None
                step.error_code = None
                step.error_message = None
                step.updated_at = now
            self._append_event(
                session,
                run_id,
                "step_started" if step.attempt_count == 1 else "step_resumed",
                {"step_id": step.id, "step_index": step_index, "description": description},
            )
            return self._step_dict(step)

    def complete_step(
        self,
        step_id: int,
        *,
        output: Any,
        artifact_path: str | None = None,
    ) -> None:
        now = utc_now()
        with self._session_factory.begin() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                raise KeyError(f"Step not found: {step_id}")
            step.status = "succeeded"
            step.output_json = {"content": _json_safe(output)}
            step.output_artifact_path = artifact_path
            step.error_code = None
            step.error_message = None
            step.finished_at = now
            step.updated_at = now
            self._append_event(
                session,
                step.run_id,
                "step_succeeded",
                {"step_id": step.id, "step_index": step.step_index},
            )

    def fail_step(self, step_id: int, error_code: str, error_message: str) -> None:
        now = utc_now()
        with self._session_factory.begin() as session:
            step = session.get(AgentStep, step_id)
            if step is None:
                return
            step.status = "failed"
            step.error_code = error_code
            step.error_message = error_message[:4000]
            step.finished_at = now
            step.updated_at = now
            self._append_event(
                session,
                step.run_id,
                "step_failed",
                {
                    "step_id": step.id,
                    "step_index": step.step_index,
                    "error_code": error_code,
                },
            )

    def begin_tool_call(
        self,
        *,
        run_id: str,
        step_id: int,
        tool_name: str,
        arguments: dict,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self._session_factory.begin() as session:
            call = session.scalar(
                select(ToolCall)
                .where(ToolCall.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if call is not None and call.status == "succeeded":
                return self._tool_call_dict(call), True
            if call is None:
                call = ToolCall(
                    run_id=run_id,
                    step_id=step_id,
                    tool_name=tool_name,
                    status="running",
                    arguments_json=_json_safe(arguments),
                    idempotency_key=idempotency_key,
                    attempt_count=1,
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(call)
                session.flush()
            else:
                call.status = "running"
                call.attempt_count += 1
                call.started_at = now
                call.finished_at = None
                call.error_code = None
                call.error_message = None
                call.updated_at = now
            self._append_event(
                session,
                run_id,
                "tool_call_started",
                {
                    "tool_call_id": call.id,
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "attempt": call.attempt_count,
                },
            )
            return self._tool_call_dict(call), False

    def complete_tool_call(
        self,
        tool_call_id: int,
        *,
        result: dict,
        latency_ms: int,
        artifact_path: str | None = None,
    ) -> None:
        now = utc_now()
        with self._session_factory.begin() as session:
            call = session.get(ToolCall, tool_call_id)
            if call is None:
                raise KeyError(f"ToolCall not found: {tool_call_id}")
            call.status = "succeeded"
            call.result_json = _json_safe(result)
            call.result_artifact_path = artifact_path
            call.latency_ms = latency_ms
            call.finished_at = now
            call.updated_at = now
            self._append_event(
                session,
                call.run_id,
                "tool_call_succeeded",
                {
                    "tool_call_id": call.id,
                    "tool_name": call.tool_name,
                    "latency_ms": latency_ms,
                },
            )

    def fail_tool_call(
        self,
        tool_call_id: int,
        *,
        error_code: str,
        error_message: str,
        latency_ms: int,
        terminal: bool,
    ) -> None:
        now = utc_now()
        with self._session_factory.begin() as session:
            call = session.get(ToolCall, tool_call_id)
            if call is None:
                return
            call.status = "failed" if terminal else "retrying"
            call.error_code = error_code
            call.error_message = error_message[:4000]
            call.latency_ms = latency_ms
            call.finished_at = now if terminal else None
            call.updated_at = now
            self._append_event(
                session,
                call.run_id,
                "tool_call_failed" if terminal else "tool_call_retrying",
                {
                    "tool_call_id": call.id,
                    "tool_name": call.tool_name,
                    "error_code": error_code,
                    "attempt": call.attempt_count,
                },
            )

    def finish_run(self, run_id: str, final_report: str) -> None:
        now = utc_now()
        with self._session_factory.begin() as session:
            run = session.get(AgentRun, run_id)
            if run is None:
                raise KeyError(f"Run not found: {run_id}")
            if run.status == "cancelled":
                return
            run.status = "succeeded"
            run.final_report = final_report
            run.worker_id = None
            run.lease_expires_at = None
            run.finished_at = now
            run.updated_at = now
            run.version += 1
            self._append_event(session, run_id, "run_succeeded", {"status": "succeeded"})

    def fail_run(self, run_id: str, error_code: str, error_message: str) -> None:
        now = utc_now()
        with self._session_factory.begin() as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status == "cancelled":
                return
            run.status = "failed"
            run.error_code = error_code
            run.error_message = error_message[:4000]
            run.worker_id = None
            run.lease_expires_at = None
            run.finished_at = now
            run.updated_at = now
            run.version += 1
            self._append_event(
                session, run_id, "run_failed", {"error_code": error_code}
            )

    def cancel_run(self, run_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self._session_factory.begin() as session:
            run = session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                return None
            if run.status not in TERMINAL_RUN_STATUSES:
                run.status = "cancelled"
                run.worker_id = None
                run.lease_expires_at = None
                run.finished_at = now
                run.updated_at = now
                run.version += 1
                self._append_event(session, run_id, "run_cancelled", {})
            return self._run_dict(run)

    def resume_run(self, run_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self._session_factory.begin() as session:
            run = session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                return None
            if run.status not in {"failed", "interrupted", "cancelled"}:
                return self._run_dict(run)
            run.status = "pending"
            run.worker_id = None
            run.lease_expires_at = None
            run.finished_at = None
            run.error_code = None
            run.error_message = None
            run.updated_at = now
            run.version += 1
            self._append_event(session, run_id, "run_queued_for_resume", {})
            return self._run_dict(run)

    def is_cancelled(self, run_id: str) -> bool:
        with self._session_factory() as session:
            status = session.scalar(select(AgentRun.status).where(AgentRun.id == run_id))
            return status == "cancelled"

    @staticmethod
    def _append_event(
        session: Session, run_id: str, event_type: str, payload: dict
    ) -> RunEvent:
        event = RunEvent(
            run_id=run_id,
            event_type=event_type,
            payload_json=_json_safe(payload),
            created_at=utc_now(),
        )
        session.add(event)
        return event

    @staticmethod
    def _run_dict(run: AgentRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "session_id": run.session_id,
            "kind": run.kind,
            "status": run.status,
            "input": run.input_json,
            "plan": run.plan_json,
            "state": run.state_json,
            "current_step_index": run.current_step_index,
            "final_report": run.final_report,
            "worker_id": run.worker_id,
            "lease_expires_at": _dt(run.lease_expires_at),
            "error_code": run.error_code,
            "error_message": run.error_message,
            "version": run.version,
            "created_at": _dt(run.created_at),
            "started_at": _dt(run.started_at),
            "finished_at": _dt(run.finished_at),
            "updated_at": _dt(run.updated_at),
        }

    @staticmethod
    def _step_dict(step: AgentStep) -> dict[str, Any]:
        return {
            "id": step.id,
            "run_id": step.run_id,
            "step_index": step.step_index,
            "step_type": step.step_type,
            "description": step.description,
            "status": step.status,
            "input": step.input_json,
            "output": step.output_json,
            "output_artifact_path": step.output_artifact_path,
            "attempt_count": step.attempt_count,
            "error_code": step.error_code,
            "error_message": step.error_message,
            "started_at": _dt(step.started_at),
            "finished_at": _dt(step.finished_at),
            "created_at": _dt(step.created_at),
            "updated_at": _dt(step.updated_at),
        }

    @staticmethod
    def _tool_call_dict(call: ToolCall) -> dict[str, Any]:
        return {
            "id": call.id,
            "run_id": call.run_id,
            "step_id": call.step_id,
            "tool_name": call.tool_name,
            "status": call.status,
            "arguments": call.arguments_json,
            "result": call.result_json,
            "result_artifact_path": call.result_artifact_path,
            "idempotency_key": call.idempotency_key,
            "attempt_count": call.attempt_count,
            "latency_ms": call.latency_ms,
            "error_code": call.error_code,
            "error_message": call.error_message,
            "started_at": _dt(call.started_at),
            "finished_at": _dt(call.finished_at),
            "created_at": _dt(call.created_at),
            "updated_at": _dt(call.updated_at),
        }


_repository: HarnessRepository | None = None
_repository_lock = threading.Lock()


def get_harness_repository(
    settings: HarnessSettings | None = None,
) -> HarnessRepository:
    """Lazily initialize the repository; importing FastAPI never opens a DB connection."""
    global _repository
    if settings is not None:
        return HarnessRepository(
            settings.database_url,
            auto_create_schema=settings.auto_create_schema,
        )
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = HarnessRepository(
                    harness_settings.database_url,
                    auto_create_schema=harness_settings.auto_create_schema,
                )
    return _repository
