from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.harness.repository as repository_module
from app.api.runs import router
from app.harness.repository import HarnessRepository


def test_run_api_create_inspect_cancel_and_resume(tmp_path, monkeypatch) -> None:
    path = (tmp_path / "api.sqlite").as_posix()
    repository = HarnessRepository(f"sqlite+pysqlite:///{path}")
    monkeypatch.setattr(repository_module, "_repository", repository)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    created = client.post(
        "/api/runs",
        json={"task": "diagnose MATLAB crash", "session_id": "api-test"},
    )
    assert created.status_code == 202
    run_id = created.json()["id"]

    fetched = client.get(f"/api/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "pending"

    steps = client.get(f"/api/runs/{run_id}/steps")
    assert steps.status_code == 200
    assert steps.json() == {"run_id": run_id, "steps": []}

    cancelled = client.post(f"/api/runs/{run_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"

    resumed = client.post(f"/api/runs/{run_id}/resume")
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "pending"
