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
from app.harness.repository import HarnessRepository, get_harness_repository
from app.harness.runtime import HarnessRunContext, harness_run_context

RunExecutor = Callable[[dict[str, Any]], AsyncIterator[dict[str, Any]]]


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

    async def run_forever(self) -> None:
        recovered = await asyncio.to_thread(self.repository.recover_stale_runs)
        if recovered:
            logger.warning(f"[Harness] recovered {recovered} stale run(s)")
        logger.info(f"[Harness] worker started: {self.worker_id}")
        while not self._stopping.is_set():
            worked = await self.run_once()
            if not worked:
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(), timeout=self.settings.poll_interval_seconds
                    )
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stopping.set()

    async def run_once(self) -> bool:
        run = await asyncio.to_thread(
            self.repository.claim_next_run,
            self.worker_id,
            self.settings.lease_seconds,
        )
        if run is None:
            return False

        run_id = str(run["id"])
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._lease_heartbeat(run_id, heartbeat_stop)
        )
        try:
            context = HarnessRunContext(run_id=run_id, repository=self.repository)
            with harness_run_context(context):
                async for event in self.executor(run):
                    await asyncio.to_thread(
                        self.repository.append_event,
                        run_id,
                        str(event.get("type", "agent_event")),
                        event,
                    )
                    if await asyncio.to_thread(self.repository.is_cancelled, run_id):
                        logger.info(f"[Harness] run cancelled: {run_id}")
                        return True
                    if event.get("type") == "error":
                        raise RuntimeError(str(event.get("message", "Agent execution failed")))
                    if event.get("type") == "complete":
                        report = str(
                            event.get("response")
                            or (event.get("diagnosis") or {}).get("report")
                            or ""
                        )
                        await self._deliver_alert_report(run, report)
                        await asyncio.to_thread(self.repository.finish_run, run_id, report)
                        logger.info(f"[Harness] run succeeded: {run_id}")
                        return True
            raise RuntimeError("Agent stream ended without a complete event")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"[Harness] run failed: {run_id}: {exc}")
            await asyncio.to_thread(
                self.repository.fail_run,
                run_id,
                "RUN_EXECUTION_FAILED",
                str(exc),
            )
            return True
        finally:
            heartbeat_stop.set()
            await heartbeat_task

    async def _lease_heartbeat(self, run_id: str, stopping: asyncio.Event) -> None:
        interval = max(1.0, self.settings.lease_seconds / 3)
        while not stopping.is_set():
            try:
                await asyncio.wait_for(stopping.wait(), timeout=interval)
                break
            except TimeoutError:
                renewed = await asyncio.to_thread(
                    self.repository.renew_lease,
                    run_id,
                    self.worker_id,
                    self.settings.lease_seconds,
                )
                if not renewed:
                    logger.warning(f"[Harness] lease no longer owned: {run_id}")
                    break

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

    async def _deliver_alert_report(self, run: dict[str, Any], report: str) -> None:
        """Send the diagnosis email before marking an alert Run successful."""
        if run.get("kind") != "alert_diagnosis":
            return

        from app.models.alert import AlertRecord
        from app.services.alert_service import send_diagnosis_report_email

        alert_payload = (run.get("input") or {}).get("alert")
        if not alert_payload:
            raise ValueError("alert_diagnosis Run is missing input.alert")
        alert = AlertRecord.model_validate(alert_payload)
        alert.diagnosis_report = report
        await send_diagnosis_report_email(alert, report)
        await asyncio.to_thread(
            self.repository.append_event,
            str(run["id"]),
            "alert_diagnosis_email_sent",
            {"alert_id": alert.alert_id},
        )


async def run_worker() -> None:
    repository = get_harness_repository()
    repository.health_check()
    worker = HarnessWorker(repository)
    try:
        await worker.run_forever()
    finally:
        repository.close()
