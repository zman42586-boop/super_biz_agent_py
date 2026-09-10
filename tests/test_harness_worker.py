from __future__ import annotations

import pytest

from app.harness.config import HarnessSettings
from app.harness.repository import HarnessRepository
from app.harness.worker import HarnessWorker


@pytest.mark.asyncio
async def test_worker_completes_claimed_run(tmp_path) -> None:
    path = (tmp_path / "worker.sqlite").as_posix()
    repository = HarnessRepository(f"sqlite+pysqlite:///{path}")
    run = repository.create_run(task="fake diagnosis", session_id="worker-session")

    async def fake_executor(claimed_run):
        repository.merge_checkpoint(
            claimed_run["id"],
            "planner",
            {"plan": [], "past_steps": [["fake", "done"]]},
        )
        yield {"type": "complete", "response": "fake report"}

    settings = HarnessSettings(
        database_url=f"sqlite+pysqlite:///{path}",
        poll_interval_seconds=0.01,
        lease_seconds=3,
    )
    worker = HarnessWorker(
        repository,
        settings=settings,
        worker_id="test-worker",
        executor=fake_executor,
    )

    assert await worker.run_once() is True
    saved = repository.get_run(run["id"])
    assert saved["status"] == "succeeded"
    assert saved["final_report"] == "fake report"
    assert saved["current_step_index"] == 1
