from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import config
from app.harness.config import HarnessSettings
from app.harness.repository import HarnessRepository
from app.harness.worker import HarnessWorker
from app.models.alert import AlertEvidence, AlertRecord
from app.services.alert_service import AlertService, build_alert_diagnosis_task


def _alert() -> AlertRecord:
    return AlertRecord(
        alert_id="alert-123",
        source="lhm",
        host="workstation-01",
        severity="critical",
        alert_name="MATLAB process crashed",
        metric="process_exit",
        value=1,
        threshold=0,
        duration_sec=5,
        sensor_id="matlab",
        ts="2026-09-10T12:00:00+08:00",
        evidence=AlertEvidence(
            crash_type="access_violation",
            crash_log="MATLAB terminated unexpectedly",
        ),
    )


@pytest.mark.asyncio
async def test_trigger_diagnosis_creates_durable_alert_run(monkeypatch) -> None:
    captured: dict = {}

    class FakeRepository:
        def create_run(self, **kwargs):
            captured.update(kwargs)
            return {"id": "run-456"}

    monkeypatch.setattr(config, "oncall_auto_diagnosis", True)
    service = AlertService(repository_factory=lambda: FakeRepository())
    alert = _alert()

    run_id = await service.trigger_diagnosis(alert)

    assert run_id == "run-456"
    assert alert.diagnosis_run_id == "run-456"
    assert captured["kind"] == "alert_diagnosis"
    assert captured["session_id"] == "alert_alert-123"
    assert captured["payload"]["alert"]["alert_id"] == "alert-123"
    assert "MATLAB process crashed" in captured["task"]
    assert await service.trigger_diagnosis(alert) == "run-456"


@pytest.mark.asyncio
async def test_alert_worker_restores_payload_and_delivers_report(
    tmp_path, monkeypatch
) -> None:
    path = (tmp_path / "alert-worker.sqlite").as_posix()
    repository = HarnessRepository(f"sqlite+pysqlite:///{path}")
    alert = _alert()
    run = repository.create_run(
        task=build_alert_diagnosis_task(alert),
        session_id="alert_alert-123",
        kind="alert_diagnosis",
        payload={"alert": alert.model_dump(mode="json")},
    )

    execute_alert = AsyncMock(return_value="durable diagnosis report")
    fake_aiops_module = ModuleType("app.services.aiops_service")
    fake_aiops_module.aiops_service = SimpleNamespace(
        execute_alert_diagnosis=execute_alert
    )
    monkeypatch.setitem(sys.modules, "app.services.aiops_service", fake_aiops_module)

    send_report = AsyncMock()
    monkeypatch.setattr(
        "app.services.alert_service.send_diagnosis_report_email", send_report
    )

    settings = HarnessSettings(
        database_url=f"sqlite+pysqlite:///{path}",
        poll_interval_seconds=0.01,
        lease_seconds=3,
    )
    worker = HarnessWorker(
        repository,
        settings=settings,
        worker_id="alert-worker",
    )

    assert await worker.run_once() is True
    saved = repository.get_run(run["id"])
    assert saved is not None
    assert saved["status"] == "succeeded"
    assert saved["final_report"] == "durable diagnosis report"
    execute_alert.assert_awaited_once()
    restored_alert = execute_alert.await_args.args[0]
    assert restored_alert.alert_id == "alert-123"
    assert execute_alert.await_args.kwargs["initial_state"]["input"]
    send_report.assert_awaited_once()
    assert send_report.await_args.args[0].alert_id == "alert-123"
    assert send_report.await_args.args[1] == "durable diagnosis report"
    assert any(
        event["type"] == "alert_diagnosis_email_sent"
        for event in repository.list_events(run["id"])
    )
    repository.close()
