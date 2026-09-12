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

    metrics = client.get("/api/runs/metrics/summary?window_hours=24")
    assert metrics.status_code == 200
    assert metrics.json()["runs"]["total"] == 1

    steps = client.get(f"/api/runs/{run_id}/steps")
    assert steps.status_code == 200
    assert steps.json() == {"run_id": run_id, "steps": []}

    evaluated = client.post(
        f"/api/runs/{run_id}/evaluations",
        json={
            "experiment_name": "candidate-v1",
            "evaluator_name": "human-review",
            "score": 9,
            "passed": True,
            "metrics": {"root_cause_accuracy": 1},
            "comment": "correct diagnosis",
        },
    )
    assert evaluated.status_code == 201
    assert evaluated.json()["score"] == 9

    evaluations = client.get(f"/api/runs/{run_id}/evaluations")
    assert evaluations.status_code == 200
    assert evaluations.json()["evaluations"][0]["experiment_name"] == "candidate-v1"

    cancelled = client.post(f"/api/runs/{run_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"

    resumed = client.post(f"/api/runs/{run_id}/resume")
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "pending"
