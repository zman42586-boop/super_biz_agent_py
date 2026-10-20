"""Database-polling worker that executes and resumes durable Agent runs."""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from loguru import logger

from app.harness.config import HarnessSettings, harness_settings
from app.harness.repository import HarnessRepository, LeaseLostError, get_harness_repository
from app.harness.runtime import HarnessRunContext, harness_run_context

RunExecutor = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]


class RunExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HarnessWorker:
    def __init__(
        self,
        repository: HarnessRepository,
        *,
        settings: HarnessSettings = harness_settings,
        worker_id: str | None = None,
        executor: RunExecutor | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.worker_id = worker_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        )
        self.executor = executor or self._execute_aiops_run
        self._stopping = asyncio.Event()

    async def _maintenance(self) -> None:
        from app.harness.notifications import dispatch_notifications

        while not self._stopping.is_set():
            try:
                recovered = await asyncio.to_thread(self.repository.recover_stale_runs)
                if recovered:
                    logger.warning(f"[Harness] recovered {recovered} stale run(s)")
                await dispatch_notifications(self.repository)
            except Exception:
                logger.exception("[Harness] maintenance failed; will retry")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=max(0.01, self.settings.recovery_interval_seconds),
                )
            except TimeoutError:
                pass

    async def run_forever(self) -> None:
        logger.info(f"[Harness] worker started: {self.worker_id}")
        maintenance = asyncio.create_task(self._maintenance())
        try:
            while not self._stopping.is_set():
                try:
                    worked = await self.run_once()
                except Exception:
                    logger.exception("[Harness] polling failed; will retry")
                    worked = False
                if not worked:
                    try:
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=max(0.01, self.settings.poll_interval_seconds),
                        )
                    except TimeoutError:
                        pass
        finally:
            maintenance.cancel()
            await asyncio.gather(maintenance, return_exceptions=True)

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> bool:
        run = await asyncio.to_thread(
            self.repository.claim_next_run, self.worker_id, self.settings.lease_seconds
        )
        if run is None:
            return False
        run_id = str(run["id"])
        offline = run.get("kind") == "host_down_diagnosis"
        context = HarnessRunContext(
            run_id,
            self.repository,
            self.worker_id,
            run["version"],
            (run.get("input") or {}).get("host") if offline else None,
            (run.get("input") or {}).get("history"),
            (run.get("input") or {}).get("host") or ((run.get("input") or {}).get("alert") or {}).get("host"),
        )
        heartbeat_stop, lost = asyncio.Event(), asyncio.Event()
        with harness_run_context(context):
            execution = asyncio.create_task(self._execute_budgeted_run(run))
            heartbeat = asyncio.create_task(
                self._lease_heartbeat(run, heartbeat_stop, lost, execution)
            )
            try:
                async with asyncio.timeout(max(0.001, self.settings.run_timeout_seconds)):
                    await execution
            except asyncio.CancelledError:
                if not lost.is_set():
                    raise
                logger.warning(f"[Harness] execution stopped after lease loss: {run_id}")
            except LeaseLostError:
                logger.warning(f"[Harness] stale writer rejected: {run_id}")
            except Exception as exc:
                code = (
                    "RUN_TIMEOUT"
                    if isinstance(exc, TimeoutError)
                    else exc.code
                    if isinstance(exc, RunExecutionError)
                    else "RUN_EXECUTION_FAILED"
                )
                try:
                    if code in {"RUN_TIMEOUT", "GRAPH_RECURSION_LIMIT"}:
                        await asyncio.to_thread(
                            self.repository.append_event,
                            run_id,
                            "loop_guard_triggered",
                            {
                                "scope": "run" if code == "RUN_TIMEOUT" else "graph",
                                "reason": "run_timeout"
                                if code == "RUN_TIMEOUT"
                                else "graph_recursion_limit",
                                "limit": self.settings.run_timeout_seconds
                                if code == "RUN_TIMEOUT"
                                else self.settings.graph_recursion_limit,
                            },
                        )
                    await asyncio.to_thread(self.repository.fail_run, run_id, code, str(exc))
                except LeaseLostError:
                    logger.warning(f"[Harness] stale failure rejected: {run_id}")
            finally:
                heartbeat_stop.set()
                await heartbeat
        from app.harness.notifications import dispatch_notifications

        await dispatch_notifications(self.repository)
        return True

    async def _execute_budgeted_run(self, run: dict[str, Any]) -> None:
        remaining, state = await asyncio.to_thread(
            self.repository.reserve_run_deadline, run["id"], self.settings.run_timeout_seconds
        )
        run["state"] = state
        if remaining <= 0:
            raise TimeoutError("Cumulative Run deadline expired before recovery")
        async with asyncio.timeout(remaining):
            await self._execute_claimed_run(run)

    async def _execute_claimed_run(self, run: dict[str, Any]) -> None:
        run_id = str(run["id"])
        async for event in self.executor(run):
            await asyncio.to_thread(
                self.repository.append_event, run_id, str(event.get("type", "agent_event")), event
            )
            if event.get("type") == "error":
                raise RunExecutionError(
                    str(event.get("error_code") or "RUN_EXECUTION_FAILED"),
                    str(event.get("message", "Agent execution failed")),
                )
            if event.get("type") == "complete":
                report = str(
                    event.get("response") or (event.get("diagnosis") or {}).get("report") or ""
                )
                if not report:
                    raise RunExecutionError("EMPTY_REPORT", "Agent returned no report")
                if run.get("kind") == "host_down_diagnosis" and not event.get(
                    "host_report_finalized"
                ):
                    from app.harness.host_report import format_host_report

                    report = await asyncio.to_thread(
                        format_host_report, run, report, self.repository
                    )
                await asyncio.to_thread(self.repository.finish_run, run_id, report)
                logger.info(f"[Harness] run succeeded: {run_id}")
                return
        raise RunExecutionError(
            "RUN_EXECUTION_FAILED", "Agent stream ended without a complete event"
        )

    async def _lease_heartbeat(self, run, stopping, lost, execution) -> None:
        interval = max(0.05, self.settings.lease_seconds / 3)
        while not stopping.is_set():
            try:
                await asyncio.wait_for(stopping.wait(), timeout=interval)
                return
            except TimeoutError:
                try:
                    renewed = await asyncio.to_thread(
                        self.repository.renew_lease,
                        run["id"],
                        self.worker_id,
                        self.settings.lease_seconds,
                        run["version"],
                    )
                except Exception:
                    logger.exception("[Harness] renewal failed; stop execution conservatively")
                    renewed = False
                if not renewed:
                    lost.set()
                    execution.cancel()
                    return

    @staticmethod
    async def _execute_aiops_run(run: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        # Lazy import keeps repository tests independent from Milvus/embedding startup.
        from app.services.aiops_service import aiops_service

        task = str((run.get("input") or {}).get("task", ""))
        state = run.get("state") or None
        execution_id = f"{run['id']}:{run.get('version', 0)}"

        if run.get("kind") == "alert_diagnosis":
            from app.models.alert import AlertRecord

            alert_payload = (run.get("input") or {}).get("alert")
            if not alert_payload:
                raise ValueError("alert_diagnosis Run is missing input.alert")
            alert = AlertRecord.model_validate(alert_payload)
            report = await aiops_service.execute_alert_diagnosis(
                alert,
                initial_state=state,
                execution_id=execution_id,
            )
            yield {
                "type": "complete",
                "stage": "diagnosis_complete",
                "response": report,
                "alert_id": alert.alert_id,
            }
            return

        async for event in aiops_service.execute(
            task,
            session_id=str(run.get("session_id") or run["id"]),
            initial_state=state,
            execution_id=execution_id,
        ):
            yield event


async def run_worker() -> None:
    repository = get_harness_repository()
    repository.health_check()
    worker = HarnessWorker(repository)
    try:
        await worker.run_forever()
    finally:
        repository.close()
