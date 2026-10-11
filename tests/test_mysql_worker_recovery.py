"""Opt-in MySQL 8 integration test using a disposable EMPTY database.

HARNESS_TEST_MYSQL_URL must target a dedicated test database, never production.
"""

import asyncio
import multiprocessing
import os
import time

import pytest
from sqlalchemy import delete, select

from app.harness.config import HarnessSettings
from app.harness.models import AgentRun, RunEvent
from app.harness.repository import HarnessRepository, LeaseLostError
from app.harness.worker import HarnessWorker


def dead_worker(database_url, result):
    repository = HarnessRepository(database_url)
    run = repository.claim_next_run("hard-killed-A", 2)
    repository.merge_checkpoint(
        run["id"],
        "planner",
        {"plan": ["continue"]},
        worker_id="hard-killed-A",
        version=run["version"],
    )
    result.put((run["id"], run["version"]))
    time.sleep(600)


@pytest.mark.asyncio
async def test_mysql_hard_kill_live_workers_and_fencing():
    url = os.getenv("HARNESS_TEST_MYSQL_URL")
    if not url:
        pytest.skip("HARNESS_TEST_MYSQL_URL absent; requires disposable MySQL 8 database")
    assert url.startswith("mysql+pymysql://"), "Only a dedicated MySQL test URL is accepted"
    repository = HarnessRepository(url)
    with repository._session_factory() as session:
        assert session.scalar(select(AgentRun.id).limit(1)) is None, (
            "Integration database must be empty"
        )
    run = repository.create_run(task="hard kill recovery", session_id="mysql-integration")
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=dead_worker, args=(url, queue))
    process.start()
    tasks, workers = [], []
    try:
        run_id, epoch = await asyncio.to_thread(queue.get, True, 10)
        assert run_id == run["id"]

        async def execute(claimed):
            assert claimed["state"]["plan"] == ["continue"]
            yield {"type": "complete", "response": "recovered MySQL report"}

        workers = [
            HarnessWorker(
                HarnessRepository(url),
                worker_id=name,
                executor=execute,
                settings=HarnessSettings(
                    database_url=url,
                    lease_seconds=3,
                    poll_interval_seconds=0.05,
                    recovery_interval_seconds=0.1,
                ),
            )
            for name in ["live-B", "live-C"]
        ]
        tasks = [asyncio.create_task(w.run_forever()) for w in workers]
        await asyncio.sleep(0.1)
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        for _ in range(150):
            if repository.get_run(run_id)["status"] == "succeeded":
                break
            await asyncio.sleep(0.1)
        saved = repository.get_run(run_id)
        assert saved["status"] == "succeeded" and saved["version"] == epoch + 1
        assert saved["final_report"] == "recovered MySQL report"
        assert sum(e["type"] == "run_interrupted" for e in repository.list_events(run_id)) == 1
        with pytest.raises(LeaseLostError):
            repository.merge_checkpoint(
                run_id,
                "executor",
                {"plan": ["stale"]},
                worker_id="hard-killed-A",
                version=epoch,
            )
        with pytest.raises(LeaseLostError):
            repository.finish_run(run_id, "stale report", worker_id="hard-killed-A", version=epoch)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        for w in workers:
            w.stop()
        await asyncio.gather(*tasks)
        for w in workers:
            w.repository.close()
        with repository._session_factory.begin() as session:
            session.execute(delete(RunEvent).where(RunEvent.run_id == run["id"]))
            session.execute(delete(AgentRun).where(AgentRun.id == run["id"]))
        repository.close()
