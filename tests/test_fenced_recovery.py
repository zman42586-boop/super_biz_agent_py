import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.harness.config import HarnessSettings
from app.harness.models import AgentRun
from app.harness.repository import HarnessRepository, LeaseLostError
from app.harness.runtime import HarnessRunContext, harness_run_context
from app.harness.worker import HarnessWorker


def repo(tmp_path):
    return HarnessRepository(f"sqlite+pysqlite:///{(tmp_path / 'fencing.sqlite').as_posix()}")


def expire(repository, run_id):
    with repository._session_factory.begin() as session:
        session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(lease_expires_at=repository.db_now(session) - timedelta(seconds=1))
        )


def test_zombie_cannot_write_checkpoint_steps_tools_or_report(tmp_path):
    repository = repo(tmp_path)
    run = repository.create_run(task="diagnose", session_id="fence")
    a = repository.claim_next_run("A", 30)
    ca = HarnessRunContext(run["id"], repository, "A", a["version"])
    with harness_run_context(ca):
        repository.merge_checkpoint(run["id"], "planner", {"plan": ["old"]})
        step = repository.start_step(run["id"], 0, "query")
        call, _ = repository.begin_tool_call(
            run_id=run["id"],
            step_id=step["id"],
            tool_name="history",
            arguments={},
            idempotency_key="call",
        )
    expire(repository, run["id"])
    assert repository.recover_stale_runs() == 1
    b = repository.claim_next_run("B", 30)
    assert b["version"] == a["version"] + 1
    cb = HarnessRunContext(run["id"], repository, "B", b["version"])
    with harness_run_context(cb):
        repository.merge_checkpoint(run["id"], "executor", {"plan": ["B evidence"]})
        repository.complete_step(step["id"], output="B step")
        repository.complete_tool_call(call["id"], result={"content": "B evidence"}, latency_ms=1)
    mutations = [
        lambda: repository.merge_checkpoint(run["id"], "planner", {"plan": ["A"]}),
        lambda: repository.start_step(run["id"], 1, "stale"),
        lambda: repository.complete_step(step["id"], output="A step"),
        lambda: repository.fail_step(step["id"], "A", "stale"),
        lambda: repository.complete_tool_call(call["id"], result={"content": "A"}, latency_ms=0),
        lambda: repository.fail_tool_call(
            call["id"], error_code="A", error_message="stale", latency_ms=0, terminal=True
        ),
        lambda: repository.begin_tool_call(
            run_id=run["id"],
            step_id=step["id"],
            tool_name="history",
            arguments={},
            idempotency_key="call",
        ),
        lambda: repository.finish_run(run["id"], "A report"),
        lambda: repository.fail_run(run["id"], "A", "stale"),
        lambda: repository.append_event(run["id"], "stale", {}),
    ]
    with harness_run_context(ca):
        for mutation in mutations:
            with pytest.raises(LeaseLostError):
                mutation()
    assert not repository.renew_lease(run["id"], "A", 30, a["version"])
    with harness_run_context(cb):
        repository.finish_run(run["id"], "B report")
    with harness_run_context(ca), pytest.raises(LeaseLostError):
        repository.finish_run(run["id"], "A overwrite")
    saved = repository.get_run(run["id"])
    assert saved["final_report"] == "B report"
    assert saved["state"]["plan"] == ["B evidence"]
    assert repository.list_steps(run["id"])[0]["output"]["content"] == "B step"
    assert repository.list_tool_calls(run["id"])[0]["result"]["content"] == "B evidence"


def test_expired_owner_cannot_renew_or_write_before_recovery(tmp_path):
    repository = repo(tmp_path)
    run = repository.create_run(task="diagnose", session_id="expired")
    a = repository.claim_next_run("same-worker", 30)
    expire(repository, run["id"])
    assert not repository.renew_lease(run["id"], "same-worker", 30, a["version"])
    with pytest.raises(LeaseLostError):
        repository.finish_run(run["id"], "stale", worker_id="same-worker", version=a["version"])
    repository.recover_stale_runs()
    b = repository.claim_next_run("same-worker", 30)
    assert b["version"] != a["version"]
    with pytest.raises(LeaseLostError):
        repository.finish_run(run["id"], "stale", worker_id="same-worker", version=a["version"])
    with pytest.raises(LeaseLostError):
        repository.finish_run(run["id"], "missing credentials")


def test_parallel_claim_and_recovery_are_atomic(tmp_path):
    repository = repo(tmp_path)
    for i in range(12):
        repository.create_run(task="parallel", session_id=f"parallel-{i}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda i: repository.claim_next_run(f"w{i}", 30), range(40)))
    claims = [row for row in claimed if row]
    assert len(claims) == len({row["id"] for row in claims}) == 12
    for row in claims:
        expire(repository, row["id"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        recovered = list(pool.map(lambda _: repository.recover_stale_runs(), range(8)))
    assert sum(recovered) == 12
    for row in claims:
        assert sum(e["type"] == "run_interrupted" for e in repository.list_events(row["id"])) == 1


@pytest.mark.asyncio
async def test_live_workers_recover_dead_worker_without_restart(tmp_path):
    repository = repo(tmp_path)
    run = repository.create_run(task="lost A", session_id="periodic")
    repository.claim_next_run("dead-A", 1)
    expire(repository, run["id"])
    done = asyncio.Event()

    async def execute(_run):
        yield {"type": "complete", "response": "recovered by live worker"}
        done.set()

    workers = [
        HarnessWorker(
            repository,
            settings=HarnessSettings(
                database_url="sqlite://", poll_interval_seconds=0.01, recovery_interval_seconds=0.02
            ),
            worker_id=name,
            executor=execute,
        )
        for name in ["B", "C"]
    ]
    tasks = [asyncio.create_task(w.run_forever()) for w in workers]
    try:
        for _ in range(200):
            if repository.get_run(run["id"])["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        saved = repository.get_run(run["id"])
        assert saved["status"] == "succeeded"
        assert saved["version"] == 2
        assert any(e["type"] == "run_resumed" for e in repository.list_events(run["id"]))
    finally:
        for w in workers:
            w.stop()
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_maintenance_runs_while_worker_is_busy(tmp_path):
    repository = repo(tmp_path)
    dead = repository.create_run(task="A died", session_id="dead")
    repository.claim_next_run("A", 30)
    busy = repository.create_run(task="B busy", session_id="busy")
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(_run):
        started.set()
        await release.wait()
        yield {"type": "complete", "response": "done"}

    worker = HarnessWorker(
        repository,
        settings=HarnessSettings(database_url="sqlite://", recovery_interval_seconds=0.02),
        worker_id="B",
        executor=execute,
    )
    task = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(started.wait(), 2)
        expire(repository, dead["id"])
        for _ in range(200):
            if repository.get_run(dead["id"])["status"] == "interrupted":
                break
            await asyncio.sleep(0.01)
        assert repository.get_run(dead["id"])["status"] == "interrupted"
        assert repository.get_run(busy["id"])["status"] == "running"
    finally:
        worker.stop()
        release.set()
        await task


@pytest.mark.asyncio
async def test_lease_loss_cancels_execution_and_does_not_mark_failure(tmp_path, monkeypatch):
    repository = repo(tmp_path)
    run = repository.create_run(task="pause A", session_id="cancel-stale")
    cancelled = asyncio.Event()

    async def execute(_run):
        try:
            await asyncio.sleep(5)
            yield {"type": "complete", "response": "stale"}
        finally:
            cancelled.set()

    monkeypatch.setattr(repository, "renew_lease", lambda *args: False)
    worker = HarnessWorker(
        repository,
        settings=HarnessSettings(database_url="sqlite://", lease_seconds=1),
        executor=execute,
    )
    await asyncio.wait_for(worker.run_once(), 2)
    assert cancelled.is_set()
    assert repository.get_run(run["id"])["status"] == "running"
    assert repository.get_run(run["id"])["final_report"] is None


@pytest.mark.asyncio
async def test_paused_worker_resumes_after_live_worker_takeover(tmp_path, monkeypatch):
    repository = repo(tmp_path)
    run = repository.create_run(task="pause entire A", session_id="paused-A")
    paused, resume = asyncio.Event(), asyncio.Event()
    rejected = []

    async def execute_a(claimed):
        step = repository.start_step(claimed["id"], 0, "A evidence")
        paused.set()
        await resume.wait()
        for label, mutation in [
            (
                "checkpoint",
                lambda: repository.merge_checkpoint(run["id"], "executor", {"response": "A"}),
            ),
            ("step", lambda: repository.complete_step(step["id"], output="A")),
            ("report", lambda: repository.finish_run(run["id"], "A")),
        ]:
            with pytest.raises(LeaseLostError):
                mutation()
            rejected.append(label)
        yield {"type": "complete", "response": "A must be rejected"}

    async def suspended_heartbeat(_run, stopping, _lost, _execution):
        # Simulate a suspended process: neither executor nor heartbeat progresses.
        await stopping.wait()

    async def execute_b(claimed):
        step = repository.start_step(claimed["id"], 0, "B evidence")
        repository.complete_step(step["id"], output="B")
        repository.merge_checkpoint(claimed["id"], "executor", {"response": "B report"})
        yield {"type": "complete", "response": "B report"}

    settings = HarnessSettings(
        database_url="sqlite://",
        lease_seconds=1,
        poll_interval_seconds=0.01,
        recovery_interval_seconds=0.02,
    )
    a = HarnessWorker(repository, settings=settings, worker_id="A", executor=execute_a)
    b = HarnessWorker(repository, settings=settings, worker_id="B", executor=execute_b)
    monkeypatch.setattr(a, "_lease_heartbeat", suspended_heartbeat)
    task_a = asyncio.create_task(a.run_once())
    await asyncio.wait_for(paused.wait(), 2)
    task_b = asyncio.create_task(b.run_forever())
    try:
        # Real elapsed lease time, no future clock injection and no B restart.
        for _ in range(2000):
            if repository.get_run(run["id"])["status"] == "succeeded":
                break
            await asyncio.sleep(0.01)
        assert repository.get_run(run["id"])["final_report"] == "B report"
        resume.set()
        await asyncio.wait_for(task_a, 2)
        assert rejected == ["checkpoint", "step", "report"]
        assert repository.get_run(run["id"])["state"]["response"] == "B report"
        assert repository.list_steps(run["id"])[0]["output"]["content"] == "B"
        assert repository.get_run(run["id"])["final_report"] == "B report"
    finally:
        resume.set()
        b.stop()
        await asyncio.gather(task_a, task_b)


def test_three_renewals_keep_version_and_ignore_application_clock(tmp_path, monkeypatch):
    from datetime import datetime

    import app.harness.repository as module

    repository = repo(tmp_path)
    run = repository.create_run(task="clock skew", session_id="clock")
    with repository._session_factory() as session:
        database_year = repository.db_now(session).year
    monkeypatch.setattr(module, "utc_now", lambda: datetime(2100, 1, 1))
    claimed = repository.claim_next_run("A", 30)
    assert claimed["lease_expires_at"].startswith(str(database_year))
    for _ in range(3):
        assert repository.renew_lease(run["id"], "A", 30, claimed["version"])
        assert repository.get_run(run["id"])["version"] == claimed["version"]


def test_three_workers_claim_one_pending_run(tmp_path):
    from threading import Barrier

    repository = repo(tmp_path)
    run = repository.create_run(task="single claim", session_id="single")
    barrier = Barrier(3)

    def claim(worker):
        barrier.wait()
        return repository.claim_next_run(worker, 30)

    with ThreadPoolExecutor(max_workers=3) as pool:
        claims = list(pool.map(claim, ["A", "B", "C"]))
    winners = [claim for claim in claims if claim]
    assert len(winners) == 1
    assert winners[0]["id"] == run["id"] and winners[0]["version"] == 1


def test_only_claim_increments_version_and_recovery_records_remote_risk(tmp_path):
    repository = repo(tmp_path)
    run = repository.create_run(task="version lifecycle", session_id="version")
    # Historical revisions become a high-water mark, never reset during upgrade.
    with repository._session_factory.begin() as session:
        session.execute(update(AgentRun).where(AgentRun.id == run["id"]).values(version=90))
    a = repository.claim_next_run("A", 30)
    assert a["version"] == 91
    with harness_run_context(HarnessRunContext(run["id"], repository, "A", 91)):
        repository.merge_checkpoint(run["id"], "planner", {"plan": ["query"]})
        step = repository.start_step(run["id"], 0, "query")
        call, _ = repository.begin_tool_call(
            run_id=run["id"],
            step_id=step["id"],
            tool_name="remote",
            arguments={},
            idempotency_key="risk",
        )
    assert repository.get_run(run["id"])["version"] == 91
    expire(repository, run["id"])
    assert repository.recover_stale_runs() == 1
    assert repository.get_run(run["id"])["version"] == 91
    risk = [
        e
        for e in repository.list_events(run["id"])
        if e["type"] == "external_tool_execution_uncertain"
    ]
    assert len(risk) == 1 and risk[0]["payload"]["tool_call_id"] == call["id"]
    assert risk[0]["payload"]["potential_duplicate_execution"]
    b = repository.claim_next_run("B", 30)
    assert b["version"] == 92
    assert repository.renew_lease(run["id"], "B", 30, 92)
    with harness_run_context(HarnessRunContext(run["id"], repository, "B", 92)):
        repository.complete_tool_call(call["id"], result={"content": "B"}, latency_ms=1)
        repository.complete_step(step["id"], output="B")
        repository.merge_checkpoint(run["id"], "executor", {"response": "B"})
        repository.finish_run(run["id"], "B")
    assert repository.get_run(run["id"])["version"] == 92
    # Administrative actions don't manufacture claim tokens either.
    repository.cancel_run(run["id"])
    assert repository.get_run(run["id"])["version"] == 92


@pytest.mark.asyncio
@pytest.mark.parametrize("node", ["planner", "executor", "replanner"])
async def test_stale_graph_node_stops_before_starting_external_work(tmp_path, monkeypatch, node):
    import importlib

    repository = repo(tmp_path)
    run = repository.create_run(task="stale graph", session_id="stale-graph")
    a = repository.claim_next_run("A", 30)
    expire(repository, run["id"])
    repository.recover_stale_runs()
    repository.claim_next_run("B", 30)
    module = importlib.import_module(f"app.agent.aiops.{node}")

    def forbidden(*args, **kwargs):
        raise AssertionError("Stale worker must not start model/API work")

    monkeypatch.setattr(module.llm_factory, "create_chat_model", forbidden)
    with harness_run_context(HarnessRunContext(run["id"], repository, "A", a["version"])):
        with pytest.raises(LeaseLostError):
            await getattr(module, node)({"input": "stale", "plan": ["query"], "past_steps": []})
